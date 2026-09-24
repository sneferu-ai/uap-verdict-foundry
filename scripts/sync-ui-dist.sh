#!/usr/bin/env bash
# Sync the built Vite SPA into the Python package tree so editable installs
# and wheels include the React console. Run after `cd ui && npm run build`.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
ui_dist="${repo_root}/ui/dist"
package_dist="${repo_root}/src/uapvf/ui_dist"

if [[ ! -f "${ui_dist}/index.html" ]]; then
  echo "error: ${ui_dist}/index.html missing; run 'cd ui && npm run build' first" >&2
  exit 1
fi

rm -rf "${package_dist}"
cp -R "${ui_dist}" "${package_dist}"
echo "synced ${ui_dist} -> ${package_dist}"
