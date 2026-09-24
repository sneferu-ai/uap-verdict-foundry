#!/usr/bin/env python3
"""Example 2 — HTTP API integration: multipart submit, poll, download.

Requires: `uapvf serve` running and a `.env` with UAPV_OPERATOR_TOKEN set
(the example reads the token from `.env`, the product's own config file);
UAPVF_BASE_URL optional (default http://127.0.0.1:8470).

Run:  python3 examples/api_submit_case.py tests/fixtures/unexplained.jpg
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.getenv("UAPVF_BASE_URL", "http://127.0.0.1:8470")
_ENV_NAME = "UAPV_OPERATOR_TOKEN"


def _credential_from_dotenv(name: str) -> str:
    """Read a key from ./.env (the product's config source)."""
    path = Path(".env")
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return ""


def main() -> int:
    auth = _credential_from_dotenv(_ENV_NAME)
    if not auth:
        print(f"error: set {_ENV_NAME} in .env first", file=sys.stderr)
        return 2
    media = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "tests/fixtures/unexplained.jpg")
    if not media.exists():
        print(f"error: media file not found: {media}", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bearer {auth}"}

    # 1. Submit (multipart; terms_accepted required -> else 400).
    fields = json.loads(Path("examples/fields.example.json").read_text())
    fields.pop("terms_accepted", None)  # sent explicitly below
    with media.open("rb") as fh:
        resp = httpx.post(
            f"{BASE}/api/v1/cases", headers=headers, timeout=60,
            files={"media": (media.name, fh, "image/jpeg")},
            data={**fields, "terms_accepted": "true"})
    if resp.status_code != 202:
        print(f"submit failed: {resp.status_code} {resp.text}", file=sys.stderr)
        return 1
    created = resp.json()
    case_id = created["case_id"]
    print(f"created case {case_id} (status={created['status']}, "
          f"est. ${created['estimated_cost_usd']:.2f})")

    # 2. Poll the list endpoint until terminal.
    deadline = time.time() + 120
    row = None
    while time.time() < deadline:
        listing = httpx.get(f"{BASE}/api/v1/cases", headers=headers, timeout=10)
        listing.raise_for_status()
        row = next((c for c in listing.json() if c["case_id"] == case_id), None)
        if row and row["status"] in ("complete", "failed", "verdict_ready"):
            break
        time.sleep(1)
    if not row or row["status"] == "failed":
        print(f"case did not complete: {row}", file=sys.stderr)
        return 1
    print(f"poll: {row['status']} verdict={row['verdict']}")

    # 3. Read the JSON sidecar and summarize the battery.
    rep = httpx.get(f"{BASE}/api/v1/cases/{case_id}/report.json",
                    headers=headers, timeout=10)
    rep.raise_for_status()
    report = rep.json()
    print("battery: " + " ".join(
        f"{b['category']}={b['result']}" for b in report["battery_results"]))
    unc = report["uncertainty"]
    if unc.get("calibrated"):
        print(f"uncertainty: calibrated {unc['value']} "
              f"(source: {unc.get('calibration_source')})")
    else:
        print(f"uncertainty: uncalibrated ({unc.get('uncertainty_reason')})")
    print(f"report.json downloaded: {len(rep.content)} bytes")

    # 4. Fiction seed (unresolved verdicts only; 404 is a valid outcome).
    fic = httpx.get(f"{BASE}/api/v1/cases/{case_id}/fiction",
                    headers=headers, timeout=10)
    if fic.status_code == 200:
        print("fiction seed head: " + fic.text.splitlines()[0])
    else:
        print(f"fiction: {fic.status_code} {fic.json().get('detail')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
