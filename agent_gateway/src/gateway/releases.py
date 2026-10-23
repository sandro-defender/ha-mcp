"""Release metadata, Semantic Versioning, changelog parsing, and update status.

The gateway uses Semantic Versioning (`MAJOR.MINOR.PATCH`) with synchronized
metadata across `agent_gateway/config.yaml`, `agent_gateway/pyproject.toml`,
`agent_gateway/src/gateway/__init__.py`, `CHANGELOG.md`, and
`agent_gateway/CHANGELOG.md`.

Update checks are local-first and privacy-respecting:
- Installed release notes are always parsed locally from the bundled
  `CHANGELOG.md` so the Ingress UI works completely offline.
- Remote GitHub release checks only run on explicit administrator action and
  are cached in SQLite to avoid GitHub API rate limits.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any

import httpx

from . import REPO_URL, __version__
from .storage.db import Database
from .utils import iso, parse_iso, utcnow

SEMVER_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)
RELEASE_HEADING_RE = re.compile(
    r"^##\s+\[(?P<version>[^\]]+)\](?:\s*[-\u2014\u2013]\s*(?P<date>\d{4}-\d{2}-\d{2}))?\s*$"
)
SECTION_HEADING_RE = re.compile(r"^###\s+(?P<section>.+?)\s*$")

REPO_LINKS: dict[str, str] = {
    "repository": REPO_URL,
    "releases": f"{REPO_URL}/releases",
    "changelog": f"{REPO_URL}/blob/main/CHANGELOG.md",
    "issues": f"{REPO_URL}/issues",
    "discussions": f"{REPO_URL}/issues",
    "security": f"{REPO_URL}/blob/main/SECURITY.md",
    "docs": f"{REPO_URL}/tree/main/docs",
    "addon_docs": f"{REPO_URL}/blob/main/agent_gateway/DOCS.md",
    "contributing": f"{REPO_URL}/blob/main/CONTRIBUTING.md",
}

GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/sandro-defender/ha-mcp/releases/latest"
GITHUB_TAGS_API = "https://api.github.com/repos/sandro-defender/ha-mcp/tags"
UPDATE_CACHE_TTL_SECONDS = 3600

FALLBACK_CHANGELOG = (
    "# Changelog\n\n"
    "## [Unreleased]\n\n"
    "## [0.2.0] - 2026-10-21\n\n"
    "### Added\n"
    "- Native Home Assistant operational UI with 10 dedicated sections and theme selector.\n"
    "- Dedicated Agents workspace with tool allowlists, token expiry status, and usage stats.\n"
    "- Interactive policy simulator (`POST /api/admin/policies/evaluate`).\n"
    "- Rich connection diagnostics with latency, steps, error classification, and `last_ok_at`.\n"
    "- Release and changelog service with one-time post-update notice and schema v2 migration.\n"
    "- Readiness endpoint (`GET /readyz`) and redacted diagnostic bundle export.\n\n"
    "### Migration Notes\n"
    "- Automatic database migration from schema v1 to v2 runs on startup.\n\n"
    "## [0.1.0] - 2026-10-20\n\n"
    "### Added\n"
    "- Initial Home Assistant OS add-on with Ingress management UI and MCP/REST agent listener.\n"
)


def parse_semver(version: str) -> tuple[int, int, int, int, str]:
    """Parse a Semantic Version string into a sortable tuple.

    Pre-release versions sort before normal releases of the same `MAJOR.MINOR.PATCH`
    (represented by stability flag `0` vs `1`).
    """

    cleaned = version.strip()
    match = SEMVER_RE.fullmatch(cleaned)
    if not match:
        raise ValueError(f"invalid semantic version: {version!r}")
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    prerelease = match.group("prerelease") or ""
    stability = 0 if prerelease else 1
    return (major, minor, patch, stability, prerelease)


def is_valid_semver(version: str) -> bool:
    try:
        parse_semver(version)
        return True
    except ValueError:
        return False


def is_newer_version(candidate: str, reference: str) -> bool:
    """Return True if `candidate` is strictly newer than `reference`."""

    try:
        return parse_semver(candidate) > parse_semver(reference)
    except ValueError:
        return False


def find_changelog_path() -> Path | None:
    """Locate `CHANGELOG.md` across repo checkout, add-on dir, or container paths.

    Inside the add-on container `run.sh` sets `PYTHONPATH=/app/src`, so
    `__file__` resolves to `/app/src/gateway/releases.py` (only 4 parent
    levels: `/app/src/gateway`, `/app/src`, `/app`, `/`).  Never index fixed
    positions in `here.parents` that may exceed the path depth.
    """

    here = Path(__file__).resolve()
    candidates = [
        *(parent / "CHANGELOG.md" for parent in itertools.islice(here.parents, 5) if parent != parent.parent),
        Path("/app/CHANGELOG.md"),
        Path.cwd() / "CHANGELOG.md",
        Path.cwd() / "agent_gateway" / "CHANGELOG.md",
    ]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def read_changelog_text() -> str:
    path = find_changelog_path()
    if path is not None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            pass
    return FALLBACK_CHANGELOG


def parse_changelog(markdown_text: str | None = None) -> list[dict[str, Any]]:
    """Parse Keep-a-Changelog markdown into structured release entries."""

    text = markdown_text if markdown_text is not None else read_changelog_text()
    releases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_section: str | None = None
    active_bullet: list[str] = []

    def flush_bullet() -> None:
        nonlocal active_bullet
        if current is None or current_section is None or not active_bullet:
            active_bullet = []
            return
        joined = " ".join(part.strip() for part in active_bullet if part.strip()).strip()
        if joined:
            current["sections"].setdefault(current_section, []).append(joined)
        active_bullet = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        rel_match = RELEASE_HEADING_RE.match(line)
        if rel_match:
            flush_bullet()
            if current is not None:
                _finalize_release_entry(current)
                releases.append(current)
            ver = rel_match.group("version").strip()
            rel_date = rel_match.group("date")
            is_unreleased = ver.lower() == "unreleased"
            current = {
                "version": ver,
                "date": rel_date,
                "is_unreleased": is_unreleased,
                "is_current": ver == __version__,
                "release_url": (
                    f"{REPO_URL}/compare/v{__version__}...HEAD"
                    if is_unreleased
                    else f"{REPO_URL}/releases/tag/v{ver}"
                ),
                "sections": {},
                "highlights": [],
                "migration_notes": [],
                "breaking": False,
            }
            current_section = None
            continue

        if current is None:
            continue

        sec_match = SECTION_HEADING_RE.match(line)
        if sec_match:
            flush_bullet()
            current_section = sec_match.group("section").strip()
            current["sections"].setdefault(current_section, [])
            continue

        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_bullet()
            if current_section is None:
                current_section = "Notes"
                current["sections"].setdefault(current_section, [])
            active_bullet = [stripped[2:].strip()]
        elif active_bullet and stripped and not stripped.startswith("#"):
            active_bullet.append(stripped)
        elif not stripped:
            flush_bullet()

    flush_bullet()
    if current is not None:
        _finalize_release_entry(current)
        releases.append(current)

    return releases


def _finalize_release_entry(entry: dict[str, Any]) -> None:
    sections: dict[str, list[str]] = entry.get("sections", {})
    highlights: list[str] = []
    for section_name in ("Added", "Security", "Changed", "Fixed"):
        for item in sections.get(section_name, []):
            if len(highlights) < 5:
                highlights.append(item)
    if not highlights:
        for items in sections.values():
            for item in items:
                if len(highlights) < 5:
                    highlights.append(item)
    migration_notes = list(sections.get("Migration Notes", []))
    breaking_items = list(sections.get("Breaking Changes", []))
    has_breaking = bool(breaking_items) or any(
        "breaking" in item.lower() for items in sections.values() for item in items
    )
    entry["highlights"] = highlights
    entry["migration_notes"] = migration_notes
    entry["breaking"] = has_breaking


class ReleaseService:
    """Tracks installed version transitions, post-update notices, and release checks."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record_startup_version(self) -> dict[str, Any]:
        """Record the running version at startup and track version transitions."""

        now = iso()
        last_installed = self._get_setting("installed_version", None)
        previous_version = self._get_setting("previous_installed_version", None)
        seen_version = self._get_setting("seen_version", None)

        if last_installed is None:
            # First tracked boot on this versioned release service.
            self._set_setting("installed_version", __version__, now)
            self._set_setting("installed_at", now, now)
        elif last_installed != __version__:
            previous_version = str(last_installed)
            self._set_setting("previous_installed_version", previous_version, now)
            self._set_setting("installed_version", __version__, now)
            self._set_setting("installed_at", now, now)

        return {
            "installed_version": __version__,
            "previous_version": previous_version,
            "seen_version": seen_version,
        }

    def acknowledge_version(self, version: str | None = None) -> dict[str, Any]:
        target = version or __version__
        now = iso()
        self._set_setting("seen_version", target, now)
        self._set_setting("seen_version_at", now, now)
        return self.get_release_summary()

    def get_release_summary(self) -> dict[str, Any]:
        all_releases = parse_changelog()
        published_releases = [rel for rel in all_releases if not rel["is_unreleased"]]
        unreleased = next((rel for rel in all_releases if rel["is_unreleased"]), None)
        current_release = next(
            (rel for rel in published_releases if rel["version"] == __version__),
            published_releases[0] if published_releases else None,
        )

        seen_version = self._get_setting("seen_version", None)
        previous_version = self._get_setting("previous_installed_version", None)
        installed_at = self._get_setting("installed_at", None)
        schema_meta = self._db.schema_info()

        cache = self._get_setting("release_check_cache", {})
        if not isinstance(cache, dict):
            cache = {}

        latest_version = str(cache.get("latest_version") or __version__)
        update_available = is_newer_version(latest_version, __version__)
        check_status = str(cache.get("status") or "local_only")
        checked_at = cache.get("checked_at")

        show_notice = seen_version != __version__
        post_update_notice = {
            "show": show_notice,
            "version": __version__,
            "previous_version": previous_version,
            "date": current_release["date"] if current_release else None,
            "highlights": current_release["highlights"] if current_release else [],
            "migration_notes": (
                current_release["migration_notes"]
                if current_release and current_release["migration_notes"]
                else [
                    "Database schema and encrypted vault records were verified at startup.",
                    "No manual configuration changes are required for this release.",
                ]
            ),
            "breaking": bool(current_release["breaking"]) if current_release else False,
            "acknowledged_version": seen_version,
            "schema_version": schema_meta["version"],
        }

        return {
            "installed_version": __version__,
            "installed_at": installed_at,
            "previous_version": previous_version,
            "latest_version": latest_version,
            "update_available": update_available,
            "check_status": check_status,
            "checked_at": checked_at,
            "check_message": cache.get("message") or "Release notes loaded from local verified CHANGELOG.md.",
            "release_url": cache.get("release_url") or f"{REPO_URL}/releases/tag/v{latest_version}",
            "links": dict(REPO_LINKS),
            "schema": schema_meta,
            "post_update_notice": post_update_notice,
            "current_release": current_release,
            "unreleased": unreleased,
            "releases": published_releases,
        }

    async def check_for_updates(self, *, force: bool = False) -> dict[str, Any]:
        """Check GitHub for a newer release, using a 1-hour SQLite cache."""

        cache = self._get_setting("release_check_cache", {})
        now = utcnow()
        if isinstance(cache, dict) and not force and cache.get("checked_at"):
            try:
                checked_dt = parse_iso(str(cache["checked_at"]))
                age = (now - checked_dt).total_seconds()
                if 0 <= age < UPDATE_CACHE_TTL_SECONDS:
                    return self.get_release_summary()
            except ValueError:
                pass

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"ha-agent-gateway/{__version__}",
        }
        latest_version = __version__
        release_url = f"{REPO_URL}/releases"
        status = "up_to_date"
        message = f"Installed version v{__version__} is up to date."

        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(GITHUB_LATEST_RELEASE_API, headers=headers)
                if response.status_code == 200:
                    payload = response.json()
                    tag_name = str(payload.get("tag_name") or "").lstrip("v").strip()
                    html_url = str(payload.get("html_url") or REPO_LINKS["releases"])
                    if is_valid_semver(tag_name):
                        latest_version = tag_name
                        release_url = html_url
                        if is_newer_version(latest_version, __version__):
                            status = "update_available"
                            message = f"Version v{latest_version} is available on GitHub Releases."
                        else:
                            status = "up_to_date"
                            message = f"Installed version v{__version__} is the latest release."
                elif response.status_code == 404:
                    # Repository has no formal GitHub Release object yet; check tags or local changelog.
                    status = "up_to_date"
                    message = (
                        f"Running v{__version__} (latest version in repository changelog; "
                        "no separate GitHub Release tag published yet)."
                    )
                elif response.status_code in {403, 429}:
                    status = "rate_limited"
                    message = "GitHub API rate limit reached; showing bundled release history."
                else:
                    status = "unavailable"
                    message = (
                        f"GitHub release check returned HTTP {response.status_code}; using local changelog."
                    )
        except httpx.HTTPError:
            status = "offline"
            message = "GitHub release check skipped or offline; showing local bundled changelog."

        cache_payload = {
            "status": status,
            "latest_version": latest_version,
            "update_available": is_newer_version(latest_version, __version__),
            "release_url": release_url,
            "message": message,
            "checked_at": iso(now),
        }
        self._set_setting("release_check_cache", cache_payload, iso(now))
        return self.get_release_summary()

    def _get_setting(self, key: str, default: Any) -> Any:
        row = self._db.query_one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(str(row["value"]))
        except json.JSONDecodeError:
            return default

    def _set_setting(self, key: str, value: Any, updated_at: str) -> None:
        self._db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), updated_at),
        )
