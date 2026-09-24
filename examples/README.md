# examples/ — runnable UAP Verdict Foundry examples

Four runnable examples and one fields fixture, all executed against a real
`uapvf serve` in mock mode (`SNEFERU_MOCK=1`).

Prerequisites for all examples (see the root [README](../README.md)):

```bash
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.lock && python3 -m pip install -e .
cp .env.example .env          # set UAPV_OPERATOR_TOKEN to a long random string
uapvf db init
uapvf serve &                 # default http://127.0.0.1:8470

# .env is read by the product, not by your shell — export the same value:
export UAPV_OPERATOR_TOKEN="$(grep '^UAPV_OPERATOR_TOKEN=' .env | cut -d= -f2-)"
export UAPV_CLI_TOKEN="$UAPV_OPERATOR_TOKEN"
export UAPVF_BASE_URL="http://127.0.0.1:8470"   # examples read this; default shown
```

The media and fields fixtures below ship in `tests/fixtures/`.

---

## 1. `cli_case_lifecycle.sh` — full operator lifecycle in the CLI

**Setup:** running server + `UAPV_CLI_TOKEN` exported (above).

**Run:**

```bash
bash examples/cli_case_lifecycle.sh
```

**Expected output (abridged):**

```text
submit:  {"case_id": "<uuid>", "estimated_cost_usd": 0.04, "status": "queued"}
status:  complete / <verdict>, 8 stages completed
export:  report.html, report.json (+ fiction_<id>.fic.md for unresolved verdicts),
         each SHA-256 verified
payment: {"ok": true, "old": "unpaid", "new": "paid"}
audit:   ok=true, 0 problems
```

## 2. `api_submit_case.py` — HTTP API integration (multipart + Bearer)

**Setup:** running server + `UAPV_OPERATOR_TOKEN` exported; `httpx` is a
product dependency, nothing extra to install.

**Run:**

```bash
python3 examples/api_submit_case.py tests/fixtures/unexplained.jpg
```

**Expected output (abridged):**

```text
created case <uuid> (status=queued, est. $0.04)
poll: complete verdict=insufficient_data
battery: aircraft=insufficient satellites=insufficient lens_artifacts=insufficient astronomical=insufficient
uncertainty: uncalibrated (no calibration run available)
report.json downloaded: <n> bytes
fiction seed head: --- case_id: ...
```

## 3. `curl_api_reference.sh` — the main `/api/v1` endpoints with curl

**Setup:** running server + `UAPV_OPERATOR_TOKEN` exported.

**Run:**

```bash
bash examples/curl_api_reference.sh
```

**Expected output:** `200` + `{"status":"ok"}` from `/healthz`; readiness
JSON from `/readyz`; a JSON case array (with `X-Total-Count` header) from
`/api/v1/cases`; the spend summary from `/api/v1/spend`; `401` from an
unauthenticated call.

## 4. `custom_battery_adapter.py` — adding your own battery category

A template for the FR-006 plugin interface: implement one class, register it
in `var/battery_config.yaml`, restart the server, rerun cases. Not executed
by the test suite — copy it onto your `PYTHONPATH` (or into `src/uapvf/
adapters/`) and wire the config snippet shown in its docstring.

## 5. `fields.example.json` — a minimal valid `case submit` fields file

Used by example 1; same schema as the web/API form fields
(see [API.md](../docs/API.md) §8).
