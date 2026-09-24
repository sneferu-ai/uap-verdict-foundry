"""Quality gate (AC-002, FR-002) + battery adapter behavior (AC-003,
AC-023, FR-006)."""
from __future__ import annotations

import json
import pathlib

import pytest
from PIL import Image

from conftest import SEEDS, base_fields

from uapvf.battery import run_battery
from uapvf.quality_gate import load_quality_config, run_quality_gate, score_video
from uapvf.adapters import BatteryConfigError


def make_jpeg(path, size, exif=True, quality=85):
    im = Image.new("RGB", size, (50, 60, 70))
    px = im.load()
    for i in range(0, size[0], max(1, size[0] // 8)):
        for j in range(size[1]):
            px[i, j] = ((i * 5) % 256, (j * 7) % 256, 100)
    kwargs = {"quality": quality}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "TEST"
        kwargs["exif"] = ex.tobytes()
    im.save(path, format="JPEG", **kwargs)


class TestQualityGate:
    def test_high_quality_passes(self, tmp_path, settings):
        p = tmp_path / "hi.jpg"
        make_jpeg(p, (1920, 1080), exif=True)
        result = run_quality_gate(p, "image", load_quality_config(settings))
        assert result["quality_gate_pass"] is True
        assert result["quality_score"] >= 0.35

    def test_low_quality_fails_floor_but_case_proceeds(self, tmp_path, settings):
        p = tmp_path / "lo.jpg"
        make_jpeg(p, (320, 240), exif=False)
        result = run_quality_gate(p, "image", load_quality_config(settings))
        assert result["quality_gate_pass"] is False
        assert result["quality_score"] < 0.35

    def test_floor_configurable(self, tmp_path, settings):
        p = tmp_path / "lo.jpg"
        make_jpeg(p, (320, 240), exif=False)
        cfg = load_quality_config(settings)
        cfg["vision_quality_floor"] = 0.1
        result = run_quality_gate(p, "image", cfg)
        assert result["quality_gate_pass"] is True

    def test_video_bitrate_score_uses_spec_bytes_per_second(self, monkeypatch):
        """FR-002: bitrate_score = min(1.0, (file_size_bytes /
        duration_seconds) / 5_000_000.0) — bytes/s, not bits/s (an 8x
        divergence moved every video quality_score)."""
        from uapvf import quality_gate

        probe = {"ok": True, "width": 1920, "height": 1080,
                 "duration_s": 10.0, "fps": 30.0, "frame_count": 300,
                 "size_bytes": 50_000_000}
        monkeypatch.setattr(quality_gate, "probe_video",
                            lambda path: dict(probe))
        # 50 MB / 10 s = 5 MB/s == reference -> 1.0 (bits/s would cap at
        # 1.0 too, so also check the sub-reference point).
        result = score_video("ignored.mp4")
        assert result["components"]["bitrate_score"] == 1.0
        probe["size_bytes"] = 25_000_000  # 2.5 MB/s -> exactly 0.5
        result = score_video("ignored.mp4")
        assert result["components"]["bitrate_score"] == 0.5
        # quality_score = 0.40*1.0 + 0.30*1.0 + 0.30*0.5 = 0.85
        assert result["quality_score"] == 0.85

    def test_low_quality_media_reaches_insufficient_lens(self, tmp_path,
                                                         settings, conn,
                                                         process):
        from uapvf.intake import create_case

        p = tmp_path / "lo.jpg"
        make_jpeg(p, (320, 240), exif=False)
        created = create_case(p, base_fields(), settings, conn)
        process(created["case_id"])
        rows = conn.execute(
            "SELECT category, result, source_stamp_json FROM battery_results "
            "WHERE case_id=?", (created["case_id"],)).fetchall()
        by_cat = {r["category"]: r for r in rows}
        assert by_cat["lens_artifacts"]["result"] == "insufficient"
        stamp = json.loads(by_cat["lens_artifacts"]["source_stamp_json"])
        assert stamp["reason"] == "media quality below vision threshold"
        # metadata-driven categories still run
        assert by_cat["aircraft"]["result"] in ("positive", "negative", "insufficient")


class TestBatteryAircraft:
    def meta(self, **over):
        m = {
            "observed_at": "2026-01-15T20:30:00+00:00",
            "latitude": 34.05, "longitude": -118.24,
            "viewing_direction": (90.0, 45.0),
        }
        m.update(over)
        return m

    def _cat(self, results, name):
        return next(r for r in results if r["category"] == name)

    def test_mock_track_match_positive(self, settings):
        results = run_battery("t", SEEDS / "aircraft1.jpg", self.meta(), [])
        r = self._cat(results, "aircraft")
        assert r["result"] == "positive"
        assert "MOCK123" in r["evidence_citation"]
        assert r["source_stamp"]["source_id"] == "adsb_mock_fixture"

    def test_no_match_negative_inside_coverage(self, settings):
        results = run_battery("t", SEEDS / "nmm1.jpg",
                              self.meta(observed_at="2026-01-15T21:15:00+00:00",
                                        latitude=34.5, longitude=-118.0), [])
        assert self._cat(results, "aircraft")["result"] == "negative"

    def test_outside_coverage_insufficient(self, settings):
        results = run_battery("t", SEEDS / "nmm1.jpg",
                              self.meta(latitude=40.0, longitude=-120.0), [])
        assert self._cat(results, "aircraft")["result"] == "insufficient"

    def test_forced_positive_hook(self, settings, monkeypatch):
        monkeypatch.setenv("UAPV_MOCK_ADSB_POSITIVE", "1")
        results = run_battery("t", SEEDS / "nmm1.jpg",
                              self.meta(latitude=40.0, longitude=-120.0), [])
        assert self._cat(results, "aircraft")["result"] == "positive"

    def test_live_mode_unconfigured_source_insufficient(self, settings,
                                                        monkeypatch):
        monkeypatch.setenv("SNEFERU_MOCK", "0")
        results = run_battery("t", SEEDS / "aircraft1.jpg", self.meta(), [])
        r = self._cat(results, "aircraft")
        assert r["result"] == "insufficient"
        assert r["source_stamp"]["content_hash"] == "unavailable"

    def test_missing_coordinates_are_insufficient_not_exception(self, settings):
        results = run_battery(
            "t", SEEDS / "nmm1.jpg", self.meta(latitude=None, longitude=None), []
        )
        assert self._cat(results, "aircraft")["result"] == "insufficient"

    def test_adsbexchange_requires_operator_key(self, settings, monkeypatch):
        monkeypatch.setenv("SNEFERU_MOCK", "0")
        monkeypatch.setenv(
            "ADSB_SOURCE_URL", "https://www.adsbexchange.com/api/aircraft/v2"
        )
        result = run_battery("t", SEEDS / "aircraft1.jpg", self.meta(), [])
        aircraft = self._cat(result, "aircraft")
        assert aircraft["result"] == "insufficient"
        assert "API key" in aircraft["source_stamp"]["reason"]

    def test_opensky_real_schema_matches_state_vector(self, settings, monkeypatch):
        from uapvf.adapters import adsb_adapter
        import httpx

        epoch = 1_768_500_000
        monkeypatch.setenv("SNEFERU_MOCK", "0")
        monkeypatch.setenv(
            "ADSB_SOURCE_URL", "https://opensky-network.org/api/states/all"
        )
        monkeypatch.setenv("UAPV_EGRESS_ALLOWLIST", "opensky-network.org")
        monkeypatch.setattr("uapvf.egress.validate_url", lambda *a, **k: None)

        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {
                    "time": epoch,
                    "states": [["abc123", "TEST123 ", "US", epoch - 10,
                                epoch - 5, -118.24, 34.05]],
                }

        monkeypatch.setattr(httpx, "get", lambda *a, **k: Response())
        observed = __import__("datetime").datetime.fromtimestamp(
            epoch, tz=__import__("datetime").timezone.utc
        ).isoformat()
        result = run_battery(
            "t", SEEDS / "aircraft1.jpg", self.meta(observed_at=observed), []
        )
        aircraft = self._cat(result, "aircraft")
        assert aircraft["result"] == "positive"
        assert "TEST123" in aircraft["evidence_citation"]

    def test_opensky_rejects_unserved_historical_time(self, settings, monkeypatch):
        import httpx

        monkeypatch.setenv("SNEFERU_MOCK", "0")
        monkeypatch.setenv(
            "ADSB_SOURCE_URL", "https://opensky-network.org/api/states/all"
        )
        monkeypatch.setenv("UAPV_EGRESS_ALLOWLIST", "opensky-network.org")
        monkeypatch.setattr("uapvf.egress.validate_url", lambda *a, **k: None)

        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {"time": 1_800_000_000, "states": []}

        monkeypatch.setattr(httpx, "get", lambda *a, **k: Response())
        result = run_battery("t", SEEDS / "aircraft1.jpg", self.meta(), [])
        aircraft = self._cat(result, "aircraft")
        assert aircraft["result"] == "insufficient"
        assert "requested observed_at" in aircraft["source_stamp"]["reason"]


class TestBatterySatellites:
    def meta(self, **over):
        m = {
            "observed_at": "2026-01-15T20:31:00+00:00",
            "latitude": 34.2, "longitude": -118.1,
            "viewing_direction": (180.0, 45.0),
        }
        m.update(over)
        return m

    def _cat(self, results, name):
        return next(r for r in results if r["category"] == name)

    def test_pass_match_positive(self, settings):
        results = run_battery("t", SEEDS / "satellite1.jpg", self.meta(), [])
        r = self._cat(results, "satellites")
        assert r["result"] == "positive"
        assert "ISS-MOCK" in r["evidence_citation"]

    def test_no_direction_insufficient(self, settings):
        results = run_battery("t", SEEDS / "satellite1.jpg",
                              self.meta(viewing_direction=None), [])
        assert self._cat(results, "satellites")["result"] == "insufficient"

    def test_no_match_negative(self, settings):
        results = run_battery("t", SEEDS / "satellite1.jpg",
                              self.meta(viewing_direction=(90.0, 45.0)), [])
        assert self._cat(results, "satellites")["result"] == "negative"

    def test_stale_tle_insufficient(self, settings):
        fixtures = json.loads((settings.var_dir / "mock_fixtures.json").read_text())
        fixtures["satellites"]["tle_epoch"] = "2025-01-01T00:00:00+00:00"
        (settings.var_dir / "mock_fixtures.json").write_text(json.dumps(fixtures))
        results = run_battery("t", SEEDS / "satellite1.jpg", self.meta(), [])
        assert self._cat(results, "satellites")["result"] == "insufficient"

    def test_missing_coordinates_are_insufficient_not_exception(self, settings):
        results = run_battery(
            "t",
            SEEDS / "satellite1.jpg",
            self.meta(latitude=None, longitude=None),
            [],
        )
        assert self._cat(results, "satellites")["result"] == "insufficient"


class TestBatteryAstronomical:
    @pytest.fixture(autouse=True)
    def _no_mirror(self, monkeypatch):
        monkeypatch.delenv("UAPV_ARCHIVE_MIRROR_PATH", raising=False)

    def _with_catalog(self, monkeypatch, settings):
        monkeypatch.setenv("UAPV_ARCHIVE_MIRROR_PATH",
                           str(settings.var_dir / "benchmark" / "seed" /
                               "catalog_fixture.sqlite"))

    def _cat(self, results, name):
        return next(r for r in results if r["category"] == name)

    def test_catalog_match_positive(self, settings, monkeypatch):
        self._with_catalog(monkeypatch, settings)
        results = run_battery(
            "t", SEEDS / "astro1.jpg",
            {"observed_at": "2026-01-15T20:45:00+00:00", "latitude": 34.3,
             "longitude": -117.9, "viewing_direction": (45.0, 60.0)}, [],
        )
        r = self._cat(results, "astronomical")
        assert r["result"] == "positive"
        assert "HD-MOCK-1001" in r["evidence_citation"]

    def test_magnitude_filter_excludes_faint(self, settings, monkeypatch):
        self._with_catalog(monkeypatch, settings)
        # nmm1 line-of-sight hosts only a mag 7.0 object -> negative (AC-023)
        results = run_battery(
            "t", SEEDS / "nmm1.jpg",
            {"observed_at": "2026-01-15T21:15:00+00:00", "latitude": 34.5,
             "longitude": -118.0, "viewing_direction": (90.0, 45.0)}, [],
        )
        assert self._cat(results, "astronomical")["result"] == "negative"

    def test_no_catalog_configured_insufficient(self, settings):
        results = run_battery(
            "t", SEEDS / "nmm1.jpg",
            {"observed_at": "2026-01-15T21:15:00+00:00", "latitude": 34.5,
             "longitude": -118.0, "viewing_direction": (90.0, 45.0)}, [],
        )
        assert self._cat(results, "astronomical")["result"] == "insufficient"

    def test_no_direction_insufficient(self, settings):
        results = run_battery(
            "t", SEEDS / "nmm1.jpg",
            {"observed_at": "2026-01-15T21:15:00+00:00", "latitude": 34.5,
             "longitude": -118.0, "viewing_direction": None}, [],
        )
        assert self._cat(results, "astronomical")["result"] == "insufficient"

    def test_missing_coordinates_are_insufficient_not_exception(
        self, settings, monkeypatch
    ):
        self._with_catalog(monkeypatch, settings)
        results = run_battery(
            "t",
            SEEDS / "nmm1.jpg",
            {
                "observed_at": "2026-01-15T21:15:00+00:00",
                "latitude": None,
                "longitude": None,
                "viewing_direction": (90.0, 45.0),
            },
            [],
        )
        assert self._cat(results, "astronomical")["result"] == "insufficient"


class TestBatteryLineages:
    def _cat(self, results, name):
        return next(r for r in results if r["category"] == name)

    META = {"observed_at": "2026-01-15T21:15:00+00:00", "latitude": 34.5,
            "longitude": -118.0, "viewing_direction": (90.0, 45.0)}

    def _lineage(self, classification="unknown", artifact=False, i=1):
        return {"lineage_id": f"lineage-{i}", "classification": classification,
                "artifact_detected": artifact,
                "hypothesis": "h", "model_version": "v1"}

    def test_zero_lineages_insufficient(self, settings):
        results = run_battery("t", SEEDS / "nmm1.jpg", self.META, [])
        r = self._cat(results, "lens_artifacts")
        assert r["result"] == "insufficient"
        assert r["source_stamp"]["reason"] == "no vision lineages available"

    def test_single_lineage_insufficient(self, settings):
        results = run_battery("t", SEEDS / "nmm1.jpg", self.META,
                              [self._lineage()])
        assert self._cat(results, "lens_artifacts")["result"] == "insufficient"

    def test_artifact_majority_positive(self, settings):
        lin = [self._lineage("lens_artifact", True, i) for i in (1, 2, 3)]
        results = run_battery("t", SEEDS / "nmm1.jpg", self.META, lin)
        r = self._cat(results, "lens_artifacts")
        assert r["result"] == "positive"

    def test_no_artifacts_negative(self, settings):
        lin = [self._lineage("unknown", False, i) for i in (1, 2, 3)]
        results = run_battery("t", SEEDS / "nmm1.jpg", self.META, lin)
        assert self._cat(results, "lens_artifacts")["result"] == "negative"

    def test_no_taxonomy_decision_is_not_a_negative(self, settings):
        # Round 4: drones is NOT part of the four-category FR-006 default
        # battery (spec §1 resolution 13), so this §5 plugin adapter's
        # consumption contract is exercised directly instead of through
        # run_battery.
        from uapvf.adapters.taxonomy_adapter import TaxonomyAdapter

        adapter = TaxonomyAdapter()
        lin = [
            {**self._lineage(None, False, i), "status": "ok"}
            for i in (1, 2, 3)
        ]
        out = adapter.run(SEEDS / "nmm1.jpg", self.META, lin,
                          {"classifications": ["drone"], "min_comparable": 2})
        assert out["result"] == "insufficient"
        assert "taxonomy-capable" in out["source_stamp"]["reason"]

    def test_explicit_taxonomy_coverage_supports_negative(self, settings):
        # Round 4: same as above — plugin-scope unit test, the default
        # battery never runs a drones category.
        from uapvf.adapters.taxonomy_adapter import TaxonomyAdapter

        adapter = TaxonomyAdapter()
        lin = [
            {
                **self._lineage("unknown", False, i),
                "status": "ok",
                "provenance": {"taxonomy_coverage": ["drone"]},
            }
            for i in (1, 2, 3)
        ]
        out = adapter.run(SEEDS / "nmm1.jpg", self.META, lin,
                          {"classifications": ["drone"], "min_comparable": 2})
        assert out["result"] == "negative"

    def test_noncomparable_lineages_do_not_claim_artifact_review(self, settings):
        lin = [
            {**self._lineage(None, False, 1), "status": "ok"},
            {**self._lineage(None, False, 2), "status": "non_comparable"},
            {**self._lineage(None, False, 3), "status": "unavailable"},
        ]
        results = run_battery("t", SEEDS / "nmm1.jpg", self.META, lin)
        lens = self._cat(results, "lens_artifacts")
        assert lens["result"] == "insufficient"
        assert "1 available" in lens["source_stamp"]["reason"]


class TestBatteryConfig:
    def test_missing_config_unrecoverable(self, settings):
        (settings.var_dir / "battery_config.yaml").unlink()
        with pytest.raises(BatteryConfigError) as ei:
            run_battery("t", SEEDS / "nmm1.jpg", {}, [])
        assert "empty or missing" in str(ei.value)

    def test_empty_categories_unrecoverable(self, settings):
        (settings.var_dir / "battery_config.yaml").write_text(
            "version: 1\ncategories: []\n")
        with pytest.raises(BatteryConfigError):
            run_battery("t", SEEDS / "nmm1.jpg", {}, [])

    def test_bad_adapter_path_unrecoverable(self, settings):
        (settings.var_dir / "battery_config.yaml").write_text(
            "version: 1\ncategories:\n"
            "  - name: x\n    adapter: no.such.module.X\n    params: {}\n")
        with pytest.raises(BatteryConfigError) as ei:
            run_battery("t", SEEDS / "nmm1.jpg", {}, [])
        assert "configuration error" in str(ei.value)

    def test_env_substitution(self, settings, monkeypatch):
        monkeypatch.setenv("UAPV_ARCHIVE_MIRROR_PATH", "/tmp/some-mirror.sqlite")
        from uapvf.adapters.battery_config_loader import (
            build_adapters, load_battery_config)
        adapters = build_adapters(load_battery_config())
        archive = next(a for n, a, _ in adapters if n == "astronomical")
        assert archive.params["mirror_path"] == "/tmp/some-mirror.sqlite"
