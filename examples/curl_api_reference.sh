#!/usr/bin/env bash
# Example 3 — the main /api/v1 endpoints with curl.
# Requires: `uapvf serve` running; UAPV_OPERATOR_TOKEN exported;
# UAPVF_BASE_URL optional (default http://127.0.0.1:8470).
set -euo pipefail

BASE="${UAPVF_BASE_URL:-http://127.0.0.1:8470}"
if [ -z "${UAPV_OPERATOR_TOKEN:-}" ]; then
    echo "error: UAPV_OPERATOR_TOKEN first" >&2
    exit 1
fi

echo "== healthz =="
curl -sS "$BASE/healthz"; echo

echo "== readyz =="
curl -sS "$BASE/readyz"; echo

echo "== case list (Bearer; note X-Total-Count) =="
curl -sS -D - -o /dev/null \
  -H "Authorization: Bearer $UAPV_OPERATOR_TOKEN" \
  "$BASE/api/v1/cases?limit=20" | grep -i '^HTTP/\|^x-total-count'
curl -sS -H "Authorization: Bearer $UAPV_OPERATOR_TOKEN" \
  "$BASE/api/v1/cases?limit=20" | python3 -m json.tool | head -20

echo "== spend summary =="
curl -sS -H "Authorization: Bearer $UAPV_OPERATOR_TOKEN" "$BASE/api/v1/spend"; echo

echo "== unauthenticated call is rejected =="
curl -sS -o /dev/null -w "no token -> %{http_code}\n" "$BASE/api/v1/cases"
