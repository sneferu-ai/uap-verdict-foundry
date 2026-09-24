#!/usr/bin/env bash
# UI spec §12.3 token audit.
# Every hard-coded color/px/ms literal must live in the token layer
# (tokens.css), the Tailwind @theme wiring (index.css), or CSS internals
# that are inherently literal (font-face metrics and keyframe stops).
# Any other hit is a token leak and fails the audit.
set -euo pipefail

UI_SRC="$(cd "$(dirname "$0")/../ui/src" && pwd)"

# Find candidate literal leaks: hex colors, px lengths, ms durations.
# grep lines are `path:line:content` — filter on the path prefix.
LEAKS=$(grep -RnE '#[0-9a-fA-F]{3,8}\b|[0-9]+(\.[0-9]+)?px\b|[0-9]+ms\b|rgba?\(|hsla?\(' \
  "$UI_SRC" \
  --include='*.css' --include='*.ts' --include='*.tsx' \
  | grep -vE '/(tokens|index)\.css:' \
  | grep -vE '@font-face|@keyframes|font-feature|font-variation' \
  || true)

if [ -n "$LEAKS" ]; then
  echo "TOKEN AUDIT FAIL — hard-coded design literals outside the token layer:"
  echo "$LEAKS"
  exit 1
fi

echo "TOKEN AUDIT PASS — no design literals leak outside tokens.css/index.css."
