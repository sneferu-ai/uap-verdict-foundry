#!/usr/bin/env bash
# Example 1 — full operator lifecycle through the CLI.
# Requires: `uapvf serve` running, UAPV_CLI_TOKEN exported.
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$(mktemp -d)"

echo "== submit =="
SUBMIT_JSON="$(uapvf case submit \
  --media "$BASE_DIR/tests/fixtures/unexplained.jpg" \
  --fields "$BASE_DIR/examples/fields.example.json" \
  --buyer-ref example-001)"
echo "submit:  $SUBMIT_JSON"
CASE_ID="$(python3 -c 'import sys,json; print(json.load(sys.stdin)["case_id"])' <<<"$SUBMIT_JSON")"

echo "== wait for completion (max 60 s) =="
for _ in $(seq 1 60); do
  STATUS_JSON="$(uapvf case status "$CASE_ID")"
  STATUS="$(python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])' <<<"$STATUS_JSON")"
  [ "$STATUS" = "complete" ] && break
  [ "$STATUS" = "failed" ] && { echo "case failed:"; echo "$STATUS_JSON"; exit 1; }
  sleep 1
done
echo "status:  $STATUS / $(python3 -c 'import sys,json; print(json.load(sys.stdin)["verdict"])' <<<"$STATUS_JSON")"

echo "== export (SHA-256 verified by the CLI) =="
uapvf case export "$CASE_ID" --out "$OUT_DIR"
ls "$OUT_DIR"

echo "== mark paid =="
uapvf case set-payment "$CASE_ID" --status paid

echo "== verify the audit hash chain =="
uapvf audit verify "$CASE_ID"

echo "== clean up (hard delete; audit entries survive) =="
uapvf case delete "$CASE_ID"
echo "export kept at: $OUT_DIR"
