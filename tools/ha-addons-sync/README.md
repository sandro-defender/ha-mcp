# ha-addons sync helpers

Home Assistant serves this add-on through the aggregator repository
[`sandro-defender/ha-addons`](https://github.com/sandro-defender/ha-addons), which
includes it as a **git submodule**. Supervisor reads only the *pinned* commit of
that submodule, so a version bump in this repository is invisible in the add-on
store until the pin in `ha-addons` is bumped. See
[`docs/ADDON_UPDATES.md`](../../docs/ADDON_UPDATES.md) for the full explanation.

Two helpers live here:

| File | Purpose |
| --- | --- |
| `sync-addons.yml` | GitHub Actions workflow to install in `ha-addons/.github/workflows/`. Runs hourly (and on demand) and fast-forwards the `ha-mcp` / `Github-Sync` pins, committing only when the add-on version really changed. |
| `bump-ha-addons-pin.sh` | One-shot local script: clones `ha-addons`, moves the `ha-mcp` pin to the current `main`, commits and pushes. Use `--dry-run` to preview. |

## One-off pin bump

```bash
cd ha-mcp
./tools/ha-addons-sync/bump-ha-addons-pin.sh --dry-run   # preview
./tools/ha-addons-sync/bump-ha-addons-pin.sh             # commit + push
```

Requires git credentials with push access to `ha-addons`.
Then reload the store in Home Assistant: **Settings → Add-ons → ⋮ → Check for
updates**.

## Install the hourly sync workflow

```bash
git clone https://github.com/sandro-defender/ha-addons.git
cd ha-addons
mkdir -p .github/workflows
cp /path/to/ha-mcp/tools/ha-addons-sync/sync-addons.yml .github/workflows/
git add .github/workflows/sync-addons.yml
git commit -m "ci: keep add-on submodule pins in sync"
git push
```

The workflow uses the repository's own `GITHUB_TOKEN` (`contents: write`), so no
personal access token is needed. Add the folder name of any further add-on you
control to its `SUBMODULES` list.

## Watchdog in this repository

`.github/workflows/addon-pin-check.yml` runs daily and fails with the exact
fix commands in the job summary whenever `ha-addons` pins an older version than
`main`.
