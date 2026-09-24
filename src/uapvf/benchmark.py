"""Benchmark runner, metrics, and calibration (FR-014, S6).

`uapvf benchmark prereg` canonicalizes and hashes prereg.json.
`uapvf benchmark run` executes the full pipeline on every seed case under
var/benchmark/seed/ (buyer_ref = '__benchmark_seed__': rate-limit, spend-cap
and terms exempt), computes metrics from ACTUAL battery_results, writes the
10-bin calibration table, and records the run.

Labels rule: only label_source in {adsb_confirmed, satellite_confirmed,
astronomical_confirmed, synthetic} feeds recall / false-no-mundane-match /
calibration; operator_manual feeds insufficient-detection only.
no_mundane_match seeds must be synthetic (ground truth by construction).
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import List, Optional

from uapvf import audit
from uapvf.config import BENCHMARK_BUYER_REF, Settings, get_settings, utcnow_iso

CONFIRMED_SOURCES = {
    "adsb_confirmed",
    "satellite_confirmed",
    "astronomical_confirmed",
    "synthetic",
}
VERDICTS = ("no_mundane_match", "mundane_identified", "insufficient_data")


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prereg_path(settings: Optional[Settings] = None) -> Path:
    settings = settings or get_settings()
    return settings.var_dir / "benchmark" / "prereg.json"


def compute_prereg_hash(prereg: dict) -> str:
    body = {k: v for k, v in prereg.items() if k != "prereg_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def run_prereg(settings: Optional[Settings] = None, conn=None) -> dict:
    settings = settings or get_settings()
    path = prereg_path(settings)
    with open(path, "r", encoding="utf-8") as fh:
        prereg = json.load(fh)
    prereg_hash = compute_prereg_hash(prereg)
    prereg["prereg_hash"] = prereg_hash
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(prereg, fh, sort_keys=True, indent=1)
    tmp.rename(path)
    if conn is not None:
        audit.append_event(conn, None, "operator", "benchmark_prereg",
                           {"prereg_hash": prereg_hash})
    return prereg


def load_prereg(settings: Optional[Settings] = None) -> dict:
    with open(prereg_path(settings), "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_seed_manifest(settings: Optional[Settings] = None) -> List[dict]:
    settings = settings or get_settings()
    path = settings.var_dir / "benchmark" / "seed" / "seed_manifest.json"
    with open(path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    if not isinstance(manifest, list) or len(manifest) < 10:
        raise ValueError("benchmark seed manifest must contain at least 10 cases")
    seen_overall = {entry.get("labels", {}).get("overall") for entry in manifest}
    for verdict in VERDICTS:
        if verdict not in seen_overall:
            raise ValueError(
                f"benchmark seeds must include at least one {verdict} case"
            )
    for entry in manifest:
        if entry.get("labels", {}).get("overall") == "no_mundane_match":
            if entry.get("label_source") != "synthetic":
                raise ValueError(
                    "no_mundane_match seeds must be synthetic "
                    "(ground truth by construction)"
                )
    return manifest


def _validation_set_ids(manifest: List[dict], seed_dir: Path):
    vsid = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    h = hashlib.sha256()
    for entry in manifest:
        fpath = seed_dir / entry["case_file"]
        if fpath.exists():
            h.update(hashlib.sha256(fpath.read_bytes()).hexdigest().encode())
        h.update(canonical_json(entry.get("labels", {})).encode())
    return vsid, h.hexdigest()


def compute_metrics(results: List[dict]) -> dict:
    """results: [{labels, label_source, battery:{cat: result}, verdict}]."""
    categories = sorted(
        {
            cat
            for r in results
            for cat in r["labels"].keys()
            if cat not in ("expected_insufficient", "overall")
        }
    )
    recall = {}
    for cat in categories:
        eligible = [
            r for r in results
            if r["label_source"] in CONFIRMED_SOURCES
            and r["labels"].get(cat) == "positive"
        ]
        if not eligible:
            recall[cat] = 1.0  # vacuous: nothing labelled positive
            continue
        hits = sum(1 for r in eligible if r["battery"].get(cat) == "positive")
        recall[cat] = round(hits / len(eligible), 6)

    mundane_labels = [
        r for r in results
        if r["label_source"] in CONFIRMED_SOURCES
        and r["labels"].get("overall") == "mundane_identified"
    ]
    if mundane_labels:
        false_nmm = sum(
            1 for r in mundane_labels if r["verdict"] == "no_mundane_match"
        )
        false_nmm_rate = round(false_nmm / len(mundane_labels), 6)
    else:
        false_nmm_rate = 0.0

    expected_pairs = []
    for r in results:
        for cat in r["labels"].get("expected_insufficient", []):
            expected_pairs.append((r, cat))
    if expected_pairs:
        detected = sum(
            1 for r, cat in expected_pairs if r["battery"].get(cat) == "insufficient"
        )
        insuf_rate = round(detected / len(expected_pairs), 6)
    else:
        insuf_rate = 1.0

    return {
        "per_category_recall": recall,
        "false_no_mundane_match_rate": false_nmm_rate,
        "insufficient_detection_rate": insuf_rate,
    }


def passed_predicate(metrics: dict, prereg: dict) -> bool:
    recall_ok = all(
        v >= float(prereg.get("min_per_category_recall", 0.7))
        for v in metrics["per_category_recall"].values()
    )
    fnmm_ok = metrics["false_no_mundane_match_rate"] <= float(
        prereg.get("max_false_no_mundane_match_rate", 0.15)
    )
    insuf_ok = metrics["insufficient_detection_rate"] >= float(
        prereg.get("min_insufficient_detection_rate", 0.8)
    )
    return bool(recall_ok and fnmm_ok and insuf_ok)


def build_calibration_bins(cases: List[dict]) -> List[dict]:
    """cases: [{raw_confidence, correct}]. 10 bins; empty bins linearly
    interpolated between nearest non-empty; all-empty -> probabilities 0."""
    bins = []
    for i in range(10):
        lo = round(i / 10.0, 1)
        hi = round((i + 1) / 10.0, 1)
        inclusive = i == 9
        members = [
            c for c in cases
            if c["raw_confidence"] >= lo
            and (c["raw_confidence"] < hi or (inclusive and c["raw_confidence"] <= hi))
        ]
        total = len(members)
        correct = sum(1 for c in members if c["correct"])
        bins.append(
            {
                "index": i,
                "lower_bound": lo,
                "upper_bound": hi,
                "upper_inclusive": inclusive,
                "total_cases": total,
                "correct": correct,
                "probability": round(correct / total, 6) if total else None,
            }
        )
    filled = [b for b in bins if b["probability"] is not None]
    if not filled:
        for b in bins:
            b["probability"] = 0.0
        return bins
    for b in bins:
        if b["probability"] is not None:
            continue
        left = None
        right = None
        for f in filled:
            if f["index"] < b["index"]:
                left = f
            if f["index"] > b["index"] and right is None:
                right = f
        if left and right:
            span = right["index"] - left["index"]
            frac = (b["index"] - left["index"]) / span
            b["probability"] = round(
                left["probability"] + frac * (right["probability"] - left["probability"]),
                6,
            )
        elif left:
            b["probability"] = left["probability"]
        elif right:
            b["probability"] = right["probability"]
    return bins


def run_benchmark(settings: Optional[Settings] = None, conn=None,
                  progress_cb=None) -> dict:
    """Execute the full pipeline on every seed case and record metrics."""
    from uapvf import db as dbmod
    from uapvf import intake as intakemod
    from uapvf import pipeline as pipelinemod

    settings = settings or get_settings()
    own_conn = conn is None
    if own_conn:
        dbmod.init_db(settings.var_dir)
        conn = dbmod.connect(settings.db_path)
    previous_archive_env = os.environ.get("UAPV_ARCHIVE_MIRROR_PATH")
    try:
        seed_dir = settings.var_dir / "benchmark" / "seed"
        manifest = load_seed_manifest(settings)
        prereg = load_prereg(settings)
        prereg_hash = prereg.get("prereg_hash") or compute_prereg_hash(prereg)
        # Point the astronomical adapter at the deterministic seed catalog
        # for the duration of the run (battery config re-reads env per case).
        catalog_fixture = seed_dir / "catalog_fixture.sqlite"
        if catalog_fixture.exists():
            os.environ["UAPV_ARCHIVE_MIRROR_PATH"] = str(catalog_fixture)
        vsid, vshash = _validation_set_ids(manifest, seed_dir)
        run_id = str(uuid.uuid4())
        started = utcnow_iso()
        conn.execute(
            "INSERT INTO benchmark_runs (run_id, prereg_hash, validation_set_id,"
            " validation_set_hash, started_at, mode) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, prereg_hash, vsid, vshash, started,
             "mock" if settings.mock_mode else "live"),
        )
        conn.commit()
        audit.append_event(conn, None, "system", "benchmark_started",
                           {"run_id": run_id, "seeds": len(manifest)})

        results = []
        calibration_cases = []
        for idx, entry in enumerate(manifest):
            media_path = seed_dir / entry["case_file"]
            fields = dict(entry.get("intake", {}))
            fields["terms_accepted"] = True
            fields["buyer_ref"] = BENCHMARK_BUYER_REF
            case = intakemod.create_case(media_path, fields, settings, conn)
            case_id = case["case_id"]
            final = pipelinemod.process_one_case(case_id, settings=settings, conn=conn)
            battery = {
                r["category"]: r["result"]
                for r in pipelinemod.last_battery_results(conn, case_id)
            }
            row = conn.execute(
                "SELECT verdict, uncertainty, uncertainty_calibrated FROM cases "
                "WHERE case_id = ?", (case_id,),
            ).fetchone()
            unc_out = final.get("stage_outputs", {}).get("uncertainty", {}) or {}
            results.append(
                {
                    "case_file": entry["case_file"],
                    "labels": entry.get("labels", {}),
                    "label_source": entry.get("label_source"),
                    "battery": battery,
                    "verdict": row["verdict"],
                }
            )
            if entry.get("label_source") in CONFIRMED_SOURCES:
                calibration_cases.append(
                    {
                        "raw_confidence": float(unc_out.get("raw_confidence", 0.0)),
                        "correct": row["verdict"] == entry["labels"].get("overall"),
                    }
                )
            if progress_cb:
                try:
                    progress_cb(idx + 1, len(manifest))
                except Exception:
                    pass

        metrics = compute_metrics(results)
        bins = build_calibration_bins(calibration_cases)
        calibration = {
            "validation_set_id": vsid,
            "validation_set_hash": vshash,
            "mode": "mock" if settings.mock_mode else "live",
            "bins": bins,
        }
        cal_path = settings.var_dir / "benchmark" / "calibration.json"
        tmp = cal_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(calibration, fh, sort_keys=True, indent=1)
        tmp.rename(cal_path)

        passed = passed_predicate(metrics, prereg)
        conn.execute(
            "UPDATE benchmark_runs SET completed_at = ?, per_category_recall_json = ?,"
            " false_no_mundane_match_rate = ?, insufficient_detection_rate = ?,"
            " passed = ? WHERE run_id = ?",
            (
                utcnow_iso(),
                json.dumps(metrics["per_category_recall"], sort_keys=True),
                metrics["false_no_mundane_match_rate"],
                metrics["insufficient_detection_rate"],
                1 if passed else 0,
                run_id,
            ),
        )
        conn.commit()
        audit.append_event(
            conn, None, "system", "benchmark_completed",
            {
                "run_id": run_id,
                "metrics": metrics,
                "passed": passed,
                "prereg_hash": prereg_hash,
            },
        )
        return {
            "run_id": run_id,
            "metrics": metrics,
            "passed": passed,
            "calibration_path": str(cal_path),
            "seeds": len(manifest),
        }
    finally:
        if previous_archive_env is None:
            os.environ.pop("UAPV_ARCHIVE_MIRROR_PATH", None)
        else:
            os.environ["UAPV_ARCHIVE_MIRROR_PATH"] = previous_archive_env
        if own_conn:
            conn.close()
