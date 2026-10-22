#!/usr/bin/env bash
# Bump the ha-mcp submodule pin in sandro-defender/ha-addons so Home Assistant
# sees the current add-on version.
#
# Usage:
#   tools/ha-addons-sync/bump-ha-addons-pin.sh [--dry-run] [--repo <url>] [--path <submodule>]
#
# Requirements: git and network access to GitHub (credentials with push access
# to the add-on repository).
set -euo pipefail

REPO_URL="${HA_ADDONS_REPO:-https://github.com/sandro-defender/ha-addons.git}"
SUBMODULE="ha-mcp"
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --path) SUBMODULE="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

read_version() {
  local dir="$1" file
  for file in "$dir/config.yaml" "$dir"/*/config.yaml; do
    [ -f "$file" ] || continue
    sed -nE 's/^version:[[:space:]]*"?([^"[:space:]]+)"?.*/\1/p' "$file" | head -1
    return 0
  done
  return 0
}

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Cloning $REPO_URL …"
git clone --quiet "$REPO_URL" "$WORKDIR/repo"
cd "$WORKDIR/repo"

if ! git ls-tree HEAD "$SUBMODULE" >/dev/null 2>&1; then
  echo "error: '$SUBMODULE' is not a submodule of $REPO_URL" >&2
  exit 1
fi

pinned="$(git ls-tree HEAD "$SUBMODULE" | awk '{print $3}')"
git submodule update --init --recursive --depth 1 -- "$SUBMODULE" >/dev/null
old_version="$(read_version "$SUBMODULE")"

git submodule update --init --recursive --depth 1 --remote -- "$SUBMODULE" >/dev/null
new="$(git -C "$SUBMODULE" rev-parse HEAD)"
new_version="$(read_version "$SUBMODULE")"

echo "pinned commit : ${pinned:0:7}  (add-on version ${old_version:-unknown})"
echo "upstream main : ${new:0:7}  (add-on version ${new_version:-unknown})"
echo "expected from this checkout: $(read_version "$SOURCE_DIR/agent_gateway")"

if [ "$pinned" = "$new" ]; then
  echo "Already up to date — nothing to do."
  exit 0
fi

if [ "$old_version" = "$new_version" ]; then
  echo "Upstream moved but the add-on version did not change; nothing to publish." >&2
  exit 0
fi

git add "$SUBMODULE"
git -c user.name="addon-sync" -c user.email="addon-sync@users.noreply.github.com" \
  commit --quiet -m "Update ${SUBMODULE} to ${new_version:-$new}"

if [ "$DRY_RUN" = "1" ]; then
  echo "Dry run: commit created locally, not pushed."
  echo "Run without --dry-run to push it to $REPO_URL."
  exit 0
fi

git push origin "HEAD:$(git rev-parse --abbrev-ref HEAD)"
echo "Pushed. Reload the add-on store in Home Assistant (Settings → Add-ons → ⋮ → Check for updates)."
