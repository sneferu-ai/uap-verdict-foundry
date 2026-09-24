"""Benchmark + calibration (AC-016, FR-014, S6)."""
from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from uapvf import benchmark


@pytest.fixture
def bench_env(settings, conn):
    """Fresh var dir already carries the benchmark seeds via conftest."""
    return settings, conn


class TestPrereg:
    def test_prereg_hash_deterministic(self, settings, conn):
        p1 = benchmark.run_prereg(settings, conn)
        # revert the file to its pre-hash state, hash again -> same value
        prereg = json.loads(benchmark.prereg_path(settings).read_text())
        prereg.pop("prereg_hash")
        benchmark.prereg_path(settings).write_text(json.dumps(prereg))
        p2 = benchmark.run_prereg(settings, conn)
        assert p1["prereg_hash"] == p2["prereg_hash"]
        assert len(p1["prereg_hash"]) == 64

    def test_prereg_threshold_change_changes_hash(self, settings, conn):
        p1 = benchmark.run_prereg(settings, conn)
        prereg = json.loads(benchmark.prereg_path(settings).read_text())
        prereg["min_per_category_recall"] = 0.9
        prereg.pop("prereg_hash")
        benchmark.prereg_path(settings).write_text(json.dumps(prereg))
        p2 = benchmark.run_prereg(settings, conn)
        assert p1["prereg_hash"] != p2["prereg_hash"]


class TestSeedManifest:
    def test_manifest_requires_ten_cases(self, settings, conn):
        path = settings.var_dir / "benchmark" / "seed" / "seed_manifest.json"
        manifest = json.loads(path.read_text())
        path.write_text(json.dumps(manifest[:9]))
        with pytest.raises(ValueError):
            benchmark.load_seed_manifest(settings)

    def test_manifest_requires_all_verdict_classes(self, settings, conn):
        path = settings.var_dir / "benchmark" / "seed" / "seed_manifest.json"
        manifest = json.loads(path.read_text())
        for entry in manifest:
            entry["labels"]["overall"] = "mundane_identified"
        path.write_text(json.dumps(manifest))
        with pytest.raises(ValueError):
            benchmark.load_seed_manifest(settings)

    def test_nmm_seeds_must_be_synthetic(self, settings, conn):
        path = settings.var_dir / "benchmark" / "seed" / "seed_manifest.json"
        manifest = json.loads(path.read_text())
        for entry in manifest:
            if entry["labels"]["overall"] == "no_mundane_match":
                entry["label_source"] = "adsb_confirmed"
        path.write_text(json.dumps(manifest))
        with pytest.raises(ValueError):
            benchmark.load_seed_manifest(settings)


class TestBenchmarkRun:
    def test_full_benchmark_passes_on_seeds(self, settings, conn, monkeypatch):
        monkeypatch.delenv("UAPV_ARCHIVE_MIRROR_PATH", raising=False)
        benchmark.run_prereg(settings, conn)
        result = benchmark.run_benchmark(settings, conn)
        assert result["seeds"] == 10
        assert result["passed"] is True
        recall = result["metrics"]["per_category_recall"]
        assert set(recall) == {"aircraft", "satellites", "lens_artifacts",
                               "astronomical"}
        assert all(v == 1.0 for v in recall.values())
        assert result["metrics"]["false_no_mundane_match_rate"] == 0.0
        assert result["metrics"]["insufficient_detection_rate"] == 1.0
        # verdict distribution 4/3/3
        rows = conn.execute(
            "SELECT verdict, COUNT(*) n FROM cases WHERE buyer_ref = ? "
            "GROUP BY verdict", ("__benchmark_seed__",)).fetchall()
        dist = {r["verdict"]: r["n"] for r in rows}
        assert dist == {"mundane_identified": 4, "no_mundane_match": 3,
                        "insufficient_data": 3}
        # benchmark cases exempt from spend accounting
        n = conn.execute(
            "SELECT COUNT(*) n FROM spend_entries").fetchone()["n"]
        assert n == 0
        # calibration file written with 10 bins
        calib = json.loads(
            (settings.var_dir / "benchmark" / "calibration.json").read_text())
        assert len(calib["bins"]) == 10
        assert calib["validation_set_id"]
        # benchmark_runs row recorded
        row = conn.execute("SELECT * FROM benchmark_runs").fetchone()
        assert row is not None and row["passed"] == 1
        assert row["mode"] == "mock"

    def test_benchmark_restores_archive_env(self, settings, conn, monkeypatch):
        monkeypatch.setenv("UAPV_ARCHIVE_MIRROR_PATH", "/custom/path.sqlite")
        benchmark.run_prereg(settings, conn)
        benchmark.run_benchmark(settings, conn)
        assert os.environ.get("UAPV_ARCHIVE_MIRROR_PATH") == "/custom/path.sqlite"


class TestMetrics:
    def test_recall_computation(self):
        results = [
            {"labels": {"aircraft": "positive"}, "label_source": "adsb_confirmed",
             "battery": {"aircraft": "positive"}, "verdict": "mundane_identified"},
            {"labels": {"aircraft": "positive"}, "label_source": "adsb_confirmed",
             "battery": {"aircraft": "negative"}, "verdict": "no_mundane_match"},
            {"labels": {"aircraft": "negative"}, "label_source": "operator_manual",
             "battery": {"aircraft": "positive"}, "verdict": "mundane_identified"},
        ]
        m = benchmark.compute_metrics(results)
        assert m["per_category_recall"]["aircraft"] == 0.5

    def test_operator_manual_excluded_from_recall(self):
        results = [
            {"labels": {"aircraft": "positive"}, "label_source": "operator_manual",
             "battery": {"aircraft": "negative"}, "verdict": "no_mundane_match"},
        ]
        m = benchmark.compute_metrics(results)
        assert m["per_category_recall"]["aircraft"] == 1.0  # vacuous

    def test_calibration_bin_interpolation(self):
        cases = [{"raw_confidence": 0.05, "correct": True},
                 {"raw_confidence": 0.95, "correct": False}]
        bins = benchmark.build_calibration_bins(cases)
        assert bins[0]["probability"] == 1.0
        assert bins[9]["probability"] == 0.0
        assert bins[4]["probability"] is not None  # interpolated
        # monotone interpolation between 1.0 and 0.0
        assert 0.0 < bins[4]["probability"] < 1.0

    def test_empty_calibration_all_zero(self):
        bins = benchmark.build_calibration_bins([])
        assert all(b["probability"] == 0.0 for b in bins)
