"""Mundane-explanation battery engine (FR-006).

Executes configured categories sequentially. Each category result is one of
positive / negative / insufficient with a cited evidence string and a
deterministic source stamp. Adapter construction failures raise
BatteryConfigError -> unrecoverable case failure (FR-006).
"""
from __future__ import annotations

import json
from typing import List, Optional

from uapvf.adapters import BatteryConfigError
from uapvf.adapters.battery_config_loader import build_adapters, load_battery_config
from uapvf.config import utcnow_iso


def run_battery(
    case_id: str,
    media_path,
    metadata: dict,
    lineage_outputs: List[dict],
    config_path=None,
) -> List[dict]:
    """Run all configured battery categories. Returns one dict per category:
    {category, result, evidence_citation, source_stamp, recorded_at}."""
    config = load_battery_config(config_path)
    adapters = build_adapters(config)
    if not adapters:
        raise BatteryConfigError("battery_config.yaml empty or missing")
    results = []
    for name, adapter, timeout_s in adapters:
        params = dict(adapter.params)
        params.setdefault("timeout_s", timeout_s)
        # Adapters return insufficient for unreachable sources by contract;
        # only an explicit AdapterTimeout escapes and maps to a transient
        # stage retry (FR-008). AdapterSchemaError escapes as unrecoverable.
        outcome = adapter.run(media_path, dict(metadata), list(lineage_outputs), params)
        result = outcome.get("result")
        if result not in ("positive", "negative", "insufficient"):
            result = "insufficient"
        stamp = outcome.get("source_stamp") or {}
        if not stamp:
            from uapvf.adapters import sentinel_stamp

            stamp = sentinel_stamp(name, "adapter returned no source stamp", utcnow_iso())
        results.append(
            {
                "category": name,
                "result": result,
                "evidence_citation": outcome.get("evidence_citation"),
                "source_stamp": stamp,
                "recorded_at": utcnow_iso(),
            }
        )
    return results


def persist_battery_results(conn, case_id: str, run_version: int,
                            results: List[dict]) -> None:
    for r in results:
        conn.execute(
            "INSERT OR REPLACE INTO battery_results "
            "(case_id, category, run_version, result, evidence_citation,"
            " source_stamp_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                case_id,
                r["category"],
                run_version,
                r["result"],
                r.get("evidence_citation"),
                json.dumps(r["source_stamp"], sort_keys=True, ensure_ascii=False),
                r["recorded_at"],
            ),
        )
    conn.commit()


def load_battery_results(conn, case_id: str, run_version: int) -> List[dict]:
    rows = conn.execute(
        "SELECT * FROM battery_results WHERE case_id = ? AND run_version = ? "
        "ORDER BY category",
        (case_id, run_version),
    ).fetchall()
    out = []
    for r in rows:
        try:
            stamp = json.loads(r["source_stamp_json"])
        except Exception:
            stamp = {}
        out.append(
            {
                "category": r["category"],
                "result": r["result"],
                "evidence_citation": r["evidence_citation"],
                "source_stamp": stamp,
                "recorded_at": r["recorded_at"],
            }
        )
    return out
