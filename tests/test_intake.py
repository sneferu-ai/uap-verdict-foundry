"""Intake validation acceptance tests (AC-001 + FR-001/FR-016/FR-024)."""
from __future__ import annotations

import json
import pathlib

import pytest

from conftest import base_fields

from uapvf.intake import IntakeError, create_case, validate_fields


class TestTermsAcceptance:
    def test_missing_terms_rejected_before_row(self, settings, conn):
        fields = base_fields()
        del fields["terms_accepted"]
        with pytest.raises(IntakeError) as ei:
            create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"), fields,
                        settings, conn)
        assert ei.value.status_code == 400
        assert conn.execute("SELECT COUNT(*) n FROM cases").fetchone()["n"] == 0

    def test_terms_false_rejected(self, settings, conn):
        with pytest.raises(IntakeError) as ei:
            create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"),
                        base_fields(terms_accepted="false"), settings, conn)
        assert ei.value.status_code == 400

    def test_terms_true_string_accepted(self, settings, conn):
        result = create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"),
                             base_fields(terms_accepted="true"), settings, conn)
        assert result["status"] in ("queued", "spend_capped")


class TestFieldValidation:
    def test_required_fields(self):
        parsed, errors = validate_fields({})
        assert set(errors) >= {"observed_at", "latitude", "longitude"}

    def test_bad_latitude(self):
        _, errors = validate_fields(base_fields(latitude="95"))
        assert "latitude" in errors

    def test_bad_observed_at(self):
        _, errors = validate_fields(base_fields(observed_at="not-a-date"))
        assert "observed_at" in errors

    def test_observed_at_without_offset_rejected(self):
        _, errors = validate_fields(
            base_fields(observed_at="2026-01-15T21:15:00"))
        assert "observed_at" in errors

    def test_future_timestamp_rejected(self):
        _, errors = validate_fields(
            base_fields(observed_at="2099-01-01T00:00:00+00:00"))
        assert "observed_at" in errors

    def test_viewing_direction_range(self):
        _, errors = validate_fields(base_fields(viewing_direction="400,45"))
        assert "viewing_direction" in errors
        _, errors = validate_fields(base_fields(viewing_direction="90"))
        assert "viewing_direction" not in errors

    def test_notes_length_cap(self):
        _, errors = validate_fields(base_fields(behavior_notes="x" * 4001))
        assert "behavior_notes" in errors


class TestMediaRejection:
    def test_wrong_mime_rejected_415(self, settings, conn, tmp_path):
        fake = tmp_path / "fake.jpg"
        fake.write_bytes(b"this is not an image at all")
        with pytest.raises(IntakeError) as ei:
            create_case(fake, base_fields(), settings, conn)
        assert ei.value.status_code == 415

    def test_executable_rejected_415(self, settings, conn, tmp_path):
        exe = tmp_path / "evil.jpg"
        exe.write_bytes(b"MZ" + b"\x00" * 100)
        with pytest.raises(IntakeError) as ei:
            create_case(exe, base_fields(), settings, conn)
        assert ei.value.status_code == 415

    def test_corrupt_jpeg_rejected_422(self, settings, conn, tmp_path):
        bad = tmp_path / "corrupt.jpg"
        bad.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
        with pytest.raises(IntakeError) as ei:
            create_case(bad, base_fields(), settings, conn)
        assert ei.value.status_code == 422

    def test_oversize_image_rejected_413(self, settings, conn, tmp_path,
                                          monkeypatch):
        from PIL import Image

        big = tmp_path / "big.jpg"
        Image.new("RGB", (100, 100)).save(big, format="JPEG")
        import uapvf.intake as intake_mod

        monkeypatch.setattr(intake_mod, "IMAGE_MAX_BYTES", 10)
        with pytest.raises(IntakeError) as ei:
            create_case(big, base_fields(), settings, conn)
        assert ei.value.status_code == 413

    def test_video_duration_cap_is_fr001_60s(self, settings):
        """FR-001 mandates MP4/MOV/WebM ≤ 60 s. The round-4 blocker was a
        120 s cap that the SPA payload also advertised; pin the constant
        and the SPA-facing payload to the spec value so they can never
        drift apart again (payload derives from the intake constants)."""
        import uapvf.intake as intake_mod
        from uapvf.web_json import intake_config_payload

        assert intake_mod.VIDEO_MAX_DURATION_S == 60.0
        caps = intake_config_payload(settings)["media_caps"]
        assert caps["video_max_s"] == 60
        # Sibling caps must survive the derivation unchanged.
        assert caps["image_mb"] == 50
        assert caps["video_mb"] == 250
        assert caps["image_mp"] == 20

    def test_overlong_video_rejected_413_in_worker(self, tmp_path,
                                                   monkeypatch):
        """Behavioral half of the FR-001 cap, at the actual enforcement
        point (lineages/subprocess_worker.py). The round-4 blocker's exact
        counter-example — a 90 s video — must be rejected with 413 under
        the shipped 60 s cap. Deterministic: probe_video is patched, so no
        ffmpeg is required (the worker runs as a real subprocess, where a
        parent-side monkeypatch of the constant would not propagate)."""
        import uapvf.media_check as media_check
        from uapvf.lineages.subprocess_worker import _run_intake_job

        media = tmp_path / "media"
        media.mkdir()
        original = media / "original.mp4"
        original.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

        monkeypatch.setattr(
            media_check, "probe_video",
            lambda path: {"ok": True, "duration_s": 90.0,
                          "frame_count": 2700, "fps": 30.0,
                          "width": 1920, "height": 1080},
        )
        result = _run_intake_job({
            "job": "intake",
            "original_path": str(original),
            "case_dir": str(tmp_path),
            "kind": "video",
            "quality_config": None,
        })
        assert result["ok"] is False
        assert result["status_code"] == 413
        assert "exceeds 60s cap" in result["error"]


class TestCleanInstallIntake:
    """Round-5 blocker: an unspec'd strict-by-default reference gate 503'd
    EVERY submission on a spec §7 clean install (only UAPV_OPERATOR_TOKEN +
    SNEFERU_MOCK=1, no UAPV_REFERENCE_* config, no fetched references). The
    suite previously hid this by setting UAPV_REFERENCE_MODE=degraded in
    conftest. The gate is now removed, the bypass is gone, and these tests
    pin that the shipped default path actually accepts a case."""

    def test_reference_defaults_are_unconfigured_strict(self, settings):
        """The shipped defaults reproduce the clean-install condition:
        strict mode, no source base, and no local reference manifest."""
        from uapvf import references

        assert settings.UAPV_REFERENCE_MODE == "strict"
        assert settings.UAPV_REFERENCE_SOURCE_BASE == ""
        status = references.resolve(settings)
        # No manifest seeded into the test var dir → reported invalid.
        assert status["valid"] is False
        assert status["mode"] == "strict"

    def test_first_submission_succeeds_on_clean_install(self, settings, conn,
                                                        submit):
        """Spec §7/J1 step 3: the first case submission succeeds with no
        reference configuration, even though resolve() reports invalid.
        Pre-fix this raised IntakeError(503 'reference integrity gate
        failed') under the strict default."""
        result = submit("nmm1.jpg")
        assert result["case_id"]
        assert result["status"] in ("queued", "spend_capped")
        row = conn.execute("SELECT COUNT(*) n FROM cases").fetchone()
        assert row["n"] == 1

    def test_no_enforcement_primitive_remains(self):
        """The gate's enforcement primitive was deleted outright, not
        reconfigured — nothing in uapvf.references can block a mutation."""
        from uapvf import references

        assert not hasattr(references, "require_for_mutation")


class TestCaseCreation:
    def test_case_files_and_hashes(self, settings, conn, submit):
        result = submit("nmm1.jpg")
        row = conn.execute("SELECT * FROM cases WHERE case_id=?",
                           (result["case_id"],)).fetchone()
        case_dir = settings.cases_dir / result["case_id"]
        assert row["media_sha256"]
        assert row["media_kind"] == "image"
        assert (case_dir / "media" / "original.jpg").exists()
        assert (case_dir / "media" / "working.jpg").exists()
        audit_row = conn.execute(
            "SELECT detail_json FROM audit_events WHERE case_id=? AND "
            "action='case_created'", (result["case_id"],)).fetchone()
        detail = json.loads(audit_row["detail_json"])
        assert detail["media_sha256"] == row["media_sha256"]
        assert detail["working_sha256"]

    def test_estimated_cost_upper_bound(self, settings, conn, submit):
        result = submit("nmm1.jpg")
        # FR-023: lineage_cost x expected_lineage_count + fiction_cost,
        # always including fiction (verdict unknown at intake). Mock
        # defaults pin this at $0.01 x 3 + $0.01 = $0.04.
        assert result["estimated_cost_usd"] == (
            settings.lineage_cost_usd * 3 + settings.fiction_cost_usd
        )
        assert result["estimated_cost_usd"] == pytest.approx(0.04)

    def test_real_silent_video_normalizes_to_mp4(
            self, settings, conn, tmp_path):
        """Regression: the muxer must see an .mp4 temp suffix and audio is
        optional. This exercises ffprobe + ffmpeg, not a mocked subprocess."""
        import shutil
        import subprocess

        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            pytest.skip("ffmpeg/ffprobe unavailable")
        source = tmp_path / "capture.mp4"
        made = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "testsrc2=size=320x240:rate=30", "-t", "1", "-an",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        assert made.returncode == 0, made.stderr.decode("utf-8", "replace")
        result = create_case(source, base_fields(), settings, conn)
        row = conn.execute(
            "SELECT media_kind FROM cases WHERE case_id=?", (result["case_id"],)
        ).fetchone()
        assert row["media_kind"] == "video"
        working = settings.cases_dir / result["case_id"] / "media" / "working.mp4"
        assert working.exists() and working.stat().st_size > 0
        assert not working.with_name("working.tmp.mp4").exists()

    def test_null_island_warning_recorded(self, settings, conn, submit):
        result = submit("nmm1.jpg", base_fields(latitude="0", longitude="0"))
        row = conn.execute(
            "SELECT detail_json FROM audit_events WHERE case_id=? AND "
            "action='intake_warnings'", (result["case_id"],)).fetchone()
        assert row is not None
        detail = json.loads(row["detail_json"])
        assert any(w["type"] == "null_island" for w in detail["warnings"])


class TestRateLimit:
    def test_21st_submission_same_hour_rejected(self, settings, conn):
        for i in range(20):
            create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"),
                        base_fields(), settings, conn)
        with pytest.raises(IntakeError) as ei:
            create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"),
                        base_fields(), settings, conn)
        assert ei.value.status_code == 429

    def test_benchmark_seed_exempt_from_rate_limit(self, settings, conn):
        for i in range(25):
            create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"),
                        base_fields(buyer_ref="__benchmark_seed__"),
                        settings, conn)


class TestSpendCap:
    def test_exact_decimal_cap_boundary_is_allowed(self, settings, conn):
        from uapvf import spend
        from uapvf.config import utcnow_iso

        settings.UAPV_SPEND_CAP_USD = 0.3
        spend.record_spend(conn, None, 0.1, "test", utcnow_iso())
        assert spend.spend_allows_case(conn, settings, 0.2)

    def test_ac022_boundary_arithmetic_with_restored_estimate(
            self, settings, conn):
        """AC-022: pre-seed spend $0.08 + estimate $0.04 (mock defaults)
        = $0.12. Cap $0.10 -> capped (0.12 > 0.10); cap $0.12 -> allowed
        (equal is OK); cap $0.11 -> capped (0.12 > 0.11)."""
        from uapvf import spend
        from uapvf.config import utcnow_iso

        estimate = settings.estimated_case_cost_usd()
        assert estimate == pytest.approx(0.04)
        spend.record_spend(conn, None, 0.08, "preseed", utcnow_iso())
        settings.UAPV_SPEND_CAP_USD = 0.10
        assert not spend.spend_allows_case(conn, settings, estimate)
        settings.UAPV_SPEND_CAP_USD = 0.12
        assert spend.spend_allows_case(conn, settings, estimate)
        settings.UAPV_SPEND_CAP_USD = 0.11
        assert not spend.spend_allows_case(conn, settings, estimate)

    def test_over_cap_gets_spend_capped_status(self, settings, conn):
        settings.UAPV_SPEND_CAP_USD = 0.01
        result = create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"),
                             base_fields(), settings, conn)
        assert result["status"] == "spend_capped"

    def test_benchmark_seed_exempt_from_cap(self, settings, conn):
        settings.UAPV_SPEND_CAP_USD = 0.0
        result = create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"),
                             base_fields(buyer_ref="__benchmark_seed__"),
                             settings, conn)
        assert result["status"] == "queued"

    def test_global_month_total_blocks_next_case(self, settings, conn):
        settings.UAPV_SPEND_CAP_USD = 0.01
        # a spend entry already recorded this month -> next case capped
        from uapvf.config import utcnow_iso

        conn.execute(
            "INSERT INTO spend_entries (case_id, cost_usd, description,"
            " recorded_at) VALUES (NULL, 1.0, 'test', ?)", (utcnow_iso(),))
        conn.commit()
        result = create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"),
                             base_fields(buyer_ref="BUY-A"), settings, conn)
        assert result["status"] == "spend_capped"

    def test_old_month_spend_not_counted(self, settings, conn):
        settings.UAPV_SPEND_CAP_USD = 1.0
        conn.execute(
            "INSERT INTO spend_entries (case_id, cost_usd, description,"
            " recorded_at) VALUES (NULL, 999.0, 'old', '2020-01-15T00:00:00.000000Z')")
        conn.commit()
        result = create_case(pathlib.Path("var/benchmark/seed/nmm1.jpg"),
                             base_fields(buyer_ref="BUY-A"), settings, conn)
        assert result["status"] == "queued"
