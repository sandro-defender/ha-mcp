# Add-on updates: why the store still shows an old version

## Symptom

Home Assistant lists **Home Assistant Agent Gateway** at `0.1.0` forever, even
though `agent_gateway/config.yaml` on `main` says `0.4.0`, and there is no
**Update** button.

## Cause

You installed the add-on from the aggregator repository
`https://github.com/sandro-defender/ha-addons`, not directly from
`https://github.com/sandro-defender/ha-mcp`.

In `ha-addons` this project is a **git submodule**:

```
ha-addons/
├── repository.json
├── .gitmodules
└── ha-mcp/            <- submodule pinned to commit 2ca33a9 (add-on version 0.1.0)
    └── agent_gateway/
        └── config.yaml  (version: "0.1.0")
```

Supervisor does not follow a submodule's branch. It reads the add-on catalogue
from the **exact commits that the repository pins**:

- cloning/pulling uses `git clone --recursive` + `git submodule update --recursive`,
  which check out the *recorded* submodule commit, not the submodule's `main`;
- the app catalogue is built by scanning the repository folder for
  `**/config.{yaml,json}` (`supervisor/store/data.py`,
  `_find_app_configs`), so what it sees is whatever `ha-mcp/agent_gateway/config.yaml`
  contains at that pinned commit;
- Supervisor only re-reads a repository when the repository's own `HEAD` commit
  changes (`supervisor/store/git.py`, `GitRepo.pull` → `changed`).

The pin in `ha-addons` was never bumped after `0.1.0`. So the store's "latest"
for this add-on *is* `0.1.0`, and since the installed version is also `0.1.0`,
Home Assistant correctly reports "no update". Nothing is broken on the add-on
side.

This is why the upstream commits in `ha-addons` look like
`Update HomeAssistantEditor to latest stable release (fcd729c)` — the other
add-ons have a bot that bumps their pins; this one did not.

## Fix (one time)

Bump the pin in `ha-addons` to the current `ha-mcp` `main`:

```bash
git clone https://github.com/sandro-defender/ha-addons.git
cd ha-addons
git submodule update --init --remote --depth 1 -- ha-mcp
git add ha-mcp
git commit -m "Update ha-mcp to 0.4.0"
git push
```

Or run the ready-made script from this repository:

```bash
./tools/ha-addons-sync/bump-ha-addons-pin.sh            # add --dry-run to preview
```

Then make Home Assistant re-read the repository:

**Settings → Add-ons → Add-on Store → ⋮ → Check for updates**
(or **Reload**). Supervisor also does this by itself roughly once a day.

After that, **Settings → Add-ons → Home Assistant Agent Gateway** shows
`0.4.0` with an **Update** button.

## Fix (permanent)

Submodule pins do not move on their own. Install the sync workflow into
`ha-addons`:

```bash
mkdir -p /path/to/ha-addons/.github/workflows
cp tools/ha-addons-sync/sync-addons.yml /path/to/ha-addons/.github/workflows/
cd /path/to/ha-addons && git add .github/workflows/sync-addons.yml \
  && git commit -m "ci: keep add-on submodule pins in sync" && git push
```

It runs hourly (and on demand from the **Actions** tab), fast-forwards the
`ha-mcp` and `Github-Sync` pins to their branch heads, and commits only when the
add-on version in `config.yaml` actually changed.

`.github/workflows/addon-pin-check.yml` in this repository then watches the pin
and opens a failing check (with the fix in the job summary) if `ha-addons` ever
pins an older version than `main` again.

## Alternative: drop the aggregator for this add-on

`https://github.com/sandro-defender/ha-mcp` is itself a valid add-on
repository (`repository.yaml` at the root, add-on in `agent_gateway/`), which is
what `README.md` recommends. Adding it directly removes the pin entirely: every
merge to `main` that bumps `config.yaml` becomes visible as soon as Supervisor
refreshes the store.

Only caveat: the same add-on from two repositories is a different add-on for
Supervisor (`<repo>_ha_agent_gateway`). Switching means uninstalling the old
entry and installing from `ha-mcp`. Do a **Backup** of the add-on first if you
have already entered settings (options are stored per add-on, not per slug).
