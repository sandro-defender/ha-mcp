"""Backend-enforced IP access management: rules, source resolution, attempts.

Enforcement lives in agent-facing middleware and applies **before**
authentication, handoff redemption, REST calls, and MCP execution.  The
management listener is deliberately *not* subject to these rules: it is
already restricted to the Supervisor's Ingress peer address, and letting IP
rules apply there could lock the administrator out of the panel.  Denied
management attempts are still recorded.

Rule precedence (documented and tested):

1. An explicit **deny** rule always wins — a broader allow rule can never
   bypass it.
2. When allowlist mode is enabled, a source must match an allow rule.
3. When allowlist mode is disabled (the default), sources that match no rule
   are allowed — the pre-IP-feature behaviour is unchanged.

IP grants network-level eligibility only.  It never grants agent identity or
operation permissions: an allowed source still needs a valid, scoped gateway
credential, and policy evaluation is unchanged.

Source addresses come from the socket peer by default.  Forwarded headers
(``X-Forwarded-For``) are honoured only when the socket peer is inside an
explicitly configured trusted-proxy range, and the chain is parsed from the
right, skipping trusted hops; a malformed chain fails safe to the socket
peer.  IPv4-mapped IPv6 addresses are normalised to IPv4 everywhere.
"""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from typing import Any, Final, Literal

from .audit.service import AuditService
from .auth.agents import AgentService
from .auth.rate_limit import SlidingWindowLimiter
from .errors import ConflictError, ValidationFailure
from .storage.db import Database, SettingsStore
from .utils import iso, utcnow

RuleAction = Literal["allow", "deny"]
Decision = Literal["allowed", "denied", "rate_limited"]

IP_ATTEMPTS_MAX_ROWS: Final = 5_000
IP_ATTEMPTS_LIST_MAX: Final = 100
IP_ALLOWED_SAMPLE_WINDOW_SECONDS: Final = 60
IP_DENY_LIMIT_PER_MINUTE: Final = 20
IP_ATTRIBUTION_HOURS: Final = 48
_EVERY_N_INSERTS: Final = 64

LISTENERS = frozenset({"agent", "management"})
DECISIONS = frozenset({"allowed", "denied", "rate_limited"})


def normalize_ip(value: str) -> str | None:
    """Return a canonical address string, or ``None`` when unparseable.

    IPv4-mapped IPv6 (``::ffff:10.0.0.1``) collapses to its IPv4 form so
    rules and observed addresses compare consistently.
    """

    candidate = value.strip()
    if not candidate or "%" in candidate:
        # Zone identifiers (fe80::1%eth0) are interface-local, not stable
        # identities across containers; refuse instead of guessing.
        return None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def parse_network(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """Parse an IP or CIDR into a network; raise :class:`ValidationFailure`."""

    candidate = value.strip()
    if not candidate:
        raise ValidationFailure("an IP address or CIDR range is required")
    try:
        if "/" in candidate:
            return ipaddress.ip_network(candidate, strict=False)
        address = ipaddress.ip_address(candidate)
        return ipaddress.ip_network(f"{address}/{address.max_prefixlen}", strict=False)
    except ValueError as exc:
        raise ValidationFailure("not a valid IPv4/IPv6 address or CIDR range") from exc


def normalize_network(value: str) -> str:
    network = parse_network(value)
    if isinstance(network, ipaddress.IPv6Network):
        mapped = network.network_address.ipv4_mapped
        if mapped is not None and network.prefixlen >= 96:
            # ::ffff:a.b.c.d/120 → a.b.c.d/24 (96 bits of mapped prefix).
            return str(ipaddress.ip_network((mapped, network.prefixlen - 96), strict=False))
    return str(network)


def networks_of(entries: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return [parse_network(item) for item in entries]


@dataclass(frozen=True)
class Rule:
    id: str
    action: RuleAction
    cidr: str
    note: str
    created_at: str
    created_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "cidr": self.cidr,
            "note": self.note,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class Evaluation:
    decision: Decision
    reason: str
    rule_cidr: str | None = None


class IPAccessService:
    """Rule storage, evaluation, source resolution, and attempt history."""

    def __init__(
        self,
        db: Database,
        settings_store: SettingsStore,
        *,
        trusted_proxies: list[str],
        audit: AuditService,
        limiter: SlidingWindowLimiter,
        agents: AgentService | None = None,
    ) -> None:
        self.db = db
        self.settings_store = settings_store
        self.audit = audit
        self.limiter = limiter
        self.agents = agents
        self._trusted_raw = [str(item) for item in trusted_proxies]
        self._trusted = networks_of(self._trusted_raw)
        self._rules: list[Rule] | None = None
        self._inserts = 0

    # ------------------------------------------------------------ trust

    @property
    def trusted_proxies(self) -> list[str]:
        return list(self._trusted_raw)

    @property
    def allowlist_mode(self) -> bool:
        return self.settings_store.get_bool("ip_allowlist_mode")

    # ------------------------------------------------------------- rules

    def _load_rules(self) -> list[Rule]:
        rows = self.db.query_all(
            "SELECT id, action, cidr, note, created_at, created_by FROM ip_rules ORDER BY created_at, id"
        )
        rules: list[Rule] = []
        for row in rows:
            rules.append(
                Rule(
                    id=str(row["id"]),
                    action="deny" if str(row["action"]) == "deny" else "allow",
                    cidr=str(row["cidr"]),
                    note=str(row["note"]),
                    created_at=str(row["created_at"]),
                    created_by=str(row["created_by"]),
                )
            )
        return rules

    def rules(self) -> list[Rule]:
        if self._rules is None:
            self._rules = self._load_rules()
        return self._rules

    def _invalidate(self) -> None:
        self._rules = None

    def create_rule(
        self,
        *,
        action: RuleAction,
        cidr: str,
        note: str = "",
        created_by: str = "",
        revoke_sessions: bool = True,
    ) -> dict[str, Any]:
        if action not in ("allow", "deny"):
            raise ValidationFailure("action must be 'allow' or 'deny'")
        normalized = normalize_network(cidr)
        with self.db.transaction():
            existing = self.db.query_one(
                "SELECT id FROM ip_rules WHERE cidr=? AND action=?", (normalized, action)
            )
            if existing is not None:
                raise ConflictError("this rule already exists")
            rule_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO ip_rules(id, action, cidr, note, created_at, created_by) VALUES(?,?,?,?,?,?)",
                (rule_id, action, normalized, note.strip()[:200], iso(utcnow()), created_by),
            )
        self._invalidate()

        revoked: list[str] = []
        if action == "deny" and revoke_sessions:
            revoked = self._revoke_sessions_for_source(normalized)
        if revoked:
            self.audit.record(
                actor_type="admin",
                actor_id=created_by or "admin",
                tool="ip.rules.revoke_sessions",
                decision="allow",
                status="ok",
                params={"cidr": normalized, "revoked_sessions": revoked},
            )
        rule = next(rule for rule in self.rules() if rule.id == rule_id)
        return {"rule": rule.to_dict(), "revoked_sessions": revoked}

    def update_rule(
        self,
        rule_id: str,
        *,
        action: RuleAction | None = None,
        cidr: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_rule(rule_id)
        new_action = action or current.action
        new_cidr = normalize_network(cidr) if cidr is not None else current.cidr
        new_note = current.note if note is None else note.strip()[:200]
        if new_action not in ("allow", "deny"):
            raise ValidationFailure("action must be 'allow' or 'deny'")
        with self.db.transaction():
            clash = self.db.query_one(
                "SELECT id FROM ip_rules WHERE cidr=? AND action=? AND id!=?",
                (new_cidr, new_action, rule_id),
            )
            if clash is not None:
                raise ConflictError("this rule already exists")
            self.db.execute(
                "UPDATE ip_rules SET action=?, cidr=?, note=? WHERE id=?",
                (new_action, new_cidr, new_note, rule_id),
            )
        self._invalidate()
        return {"rule": self.get_rule(rule_id).to_dict()}

    def delete_rule(self, rule_id: str) -> dict[str, Any]:
        self.get_rule(rule_id)
        with self.db.transaction():
            self.db.execute("DELETE FROM ip_rules WHERE id=?", (rule_id,))
        self._invalidate()
        return {"deleted": rule_id}

    def get_rule(self, rule_id: str) -> Rule:
        for rule in self.rules():
            if rule.id == rule_id:
                return rule
        from .errors import NotFoundError

        raise NotFoundError("rule")

    def set_allowlist_mode(self, enabled: bool, *, actor_id: str = "admin") -> dict[str, Any]:
        self.settings_store.set("ip_allowlist_mode", bool(enabled))
        self.audit.admin_action(
            admin_id=actor_id,
            action="ip.settings_update",
            params={"allowlist_mode": bool(enabled)},
        )
        return {"allowlist_mode": self.allowlist_mode}

    # ------------------------------------------------------- evaluation

    def evaluate(self, source_ip: str) -> Evaluation:
        normalized = normalize_ip(source_ip)
        if normalized is None:
            return Evaluation("denied", "source address could not be identified")
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            return Evaluation("denied", "source address could not be identified")

        rules = self.rules()
        for rule in rules:
            if rule.action == "deny" and self._contains(rule.cidr, address):
                return Evaluation("denied", f"matched deny rule {rule.cidr}", rule.cidr)

        matched_allow = next(
            (rule for rule in rules if rule.action == "allow" and self._contains(rule.cidr, address)),
            None,
        )
        if self.allowlist_mode:
            if matched_allow is not None:
                return Evaluation("allowed", f"matched allow rule {matched_allow.cidr}", matched_allow.cidr)
            return Evaluation("denied", "allowlist mode: no matching allow rule")
        if matched_allow is not None:
            return Evaluation("allowed", f"matched allow rule {matched_allow.cidr}", matched_allow.cidr)
        return Evaluation("allowed", "allowed by default (allowlist mode off)")

    def _contains(self, cidr: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return False
        if network.version != address.version:
            return False
        return address in network

    # ---------------------------------------------------- source address

    def resolve_source_ip(self, peer: str | None, forwarded_for: list[str]) -> str:
        """Resolve the effective source, honouring trusted proxies only."""

        if not peer:
            return "unknown"
        peer_norm = normalize_ip(peer)
        if peer_norm is None:
            return peer
        peer_addr = ipaddress.ip_address(peer_norm)
        if not self._trusted:
            return peer_norm
        if not any(peer_addr.version == net.version and peer_addr in net for net in self._trusted):
            # Header claims from untrusted peers are ignored entirely.
            return peer_norm

        chain: list[str] = []
        for header in forwarded_for:
            for part in header.split(","):
                item = part.strip()
                if item:
                    chain.append(item)
        if not chain:
            return peer_norm

        normalized_chain: list[str] = []
        for item in chain:
            entry = normalize_ip(item)
            if entry is None:
                # Malformed chain → fail safe to the socket peer.
                return peer_norm
            normalized_chain.append(entry)

        for item in reversed(normalized_chain):
            address = ipaddress.ip_address(item)
            if any(address.version == net.version and address in net for net in self._trusted):
                continue
            return item
        # Every hop is a trusted proxy: the left-most entry is the client.
        return normalized_chain[0]

    # ----------------------------------------------------------- attempts

    def record_attempt(
        self,
        source_ip: str,
        *,
        listener: str,
        category: str,
        decision: Decision,
        reason: str,
        agent_id: str | None = None,
        sample: bool = False,
    ) -> bool:
        """Persist one attempt row; returns whether it was stored.

        ``sample=True`` stores at most one allowed row per source per minute
        so an active agent cannot flood the history.
        """

        if listener not in LISTENERS or decision not in DECISIONS:
            return False
        if sample:
            allowed, _retry = self.limiter.check_and_record(
                f"ipok:{source_ip}", limit=1, window_seconds=IP_ALLOWED_SAMPLE_WINDOW_SECONDS
            )
            if not allowed:
                return False
        normalized = normalize_ip(source_ip) or str(source_ip)[:64]
        row_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO ip_attempts(id, ts, ip, listener, category, decision, reason, agent_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                row_id,
                iso(utcnow()),
                normalized,
                listener[:16],
                category[:32],
                decision,
                reason[:200],
                (agent_id[:64] if agent_id else None),
            ),
        )
        self._inserts += 1
        if self._inserts % _EVERY_N_INSERTS == 0:
            self.prune_attempts()
        return True

    def prune_attempts(self, max_rows: int = IP_ATTEMPTS_MAX_ROWS) -> int:
        """Keep only the newest ``max_rows`` attempts (bounded retention)."""

        cursor = self.db.execute_rowcount(
            "DELETE FROM ip_attempts WHERE id NOT IN "
            "(SELECT id FROM ip_attempts ORDER BY ts DESC, id DESC LIMIT ?)",
            (max_rows,),
        )
        return max(0, cursor)

    def list_attempts(
        self,
        *,
        limit: int = 25,
        offset: int = 0,
        decision: str | None = None,
        ip: str | None = None,
        query: str | None = None,
        listener: str | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), IP_ATTEMPTS_LIST_MAX))
        offset = max(0, int(offset))
        clauses: list[str] = []
        params: list[Any] = []
        if decision:
            if decision not in DECISIONS:
                raise ValidationFailure("unknown decision filter")
            clauses.append("decision=?")
            params.append(decision)
        if listener:
            if listener not in LISTENERS:
                raise ValidationFailure("unknown listener filter")
            clauses.append("listener=?")
            params.append(listener)
        if ip:
            normalized = normalize_ip(ip)
            if normalized is None:
                raise ValidationFailure("not a valid IP address")
            clauses.append("ip=?")
            params.append(normalized)
        if query:
            token = query.strip()[:64]
            if token:
                clauses.append("(ip LIKE ? OR reason LIKE ? OR agent_id LIKE ?)")
                like = f"%{token}%"
                params.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        total_row = self.db.query_one(f"SELECT COUNT(*) AS count FROM ip_attempts {where}", tuple(params))
        total = int(total_row["count"]) if total_row else 0
        rows = self.db.query_all(
            f"SELECT ts, ip, listener, category, decision, reason, agent_id FROM ip_attempts "
            f"{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        items = [
            {
                "ts": str(row["ts"]),
                "ip": str(row["ip"]),
                "listener": str(row["listener"]),
                "category": str(row["category"]),
                "decision": str(row["decision"]),
                "reason": str(row["reason"]),
                "agent_id": row["agent_id"] if row["agent_id"] else None,
            }
            for row in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # -------------------------------------------------------- revocation

    def _revoke_sessions_for_source(self, cidr: str) -> list[str]:
        """Revoke temporary sessions recently seen from a denied source.

        Attribution is best-effort (attempt history, 48 h window) and only
        affects temporary sessions; persistent identities are never revoked
        automatically.
        """

        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return []
        import datetime as _dt

        cutoff = iso(utcnow() - _dt.timedelta(hours=IP_ATTRIBUTION_HOURS))
        rows = self.db.query_all(
            "SELECT DISTINCT a.agent_id AS agent_id FROM ip_attempts a "
            "JOIN agents g ON g.id = a.agent_id "
            "WHERE a.ts >= ? AND a.ip IS NOT NULL AND g.kind='temporary' AND g.revoked_at IS NULL",
            (cutoff,),
        )
        revoked: list[str] = []
        for row in rows:
            agent_id = str(row["agent_id"] or "")
            if not agent_id:
                continue
            attempts = self.db.query_all(
                "SELECT DISTINCT ip FROM ip_attempts WHERE agent_id=? AND ts>=?",
                (agent_id, cutoff),
            )
            seen_from_denied = False
            for attempt in attempts:
                attempt_ip = normalize_ip(str(attempt["ip"]))
                if attempt_ip is None:
                    continue
                attempt_addr = ipaddress.ip_address(attempt_ip)
                if attempt_addr.version == network.version and attempt_addr in network:
                    seen_from_denied = True
                    break
            if not seen_from_denied:
                continue
            with self.db.transaction():
                self.db.execute(
                    "UPDATE agents SET revoked_at=?, revoked_reason=? WHERE id=? AND kind='temporary' "
                    "AND revoked_at IS NULL",
                    (iso(utcnow()), f"IP deny rule {cidr}", agent_id),
                )
            if self.agents is not None:
                # Kill the token digest as well so revocation is immediate and
                # indistinguishable from an administrator-triggered revoke.
                self.agents.revoke_agent(agent_id)
            self.audit.record(
                actor_type="admin",
                actor_id="system",
                tool="agent.revoke",
                decision="allow",
                status="ok",
                agent_id=agent_id,
                reason=f"temporary session revoked with IP deny rule {cidr}",
            )
            revoked.append(agent_id)
        return revoked

    # ------------------------------------------------------------- misc

    def status_payload(self) -> dict[str, Any]:
        rules = [rule.to_dict() for rule in self.rules()]
        return {
            "allowlist_mode": self.allowlist_mode,
            "rules": rules,
            "trusted_proxies": self.trusted_proxies,
            "enforcement": {
                "surface": "agent",
                "precedence": "deny wins over allow; allowlist mode requires an allow rule",
                "forwarded_headers": "trusted proxies only; otherwise socket peer",
            },
            "attempts_max_rows": IP_ATTEMPTS_MAX_ROWS,
        }
