"""Report + fiction rendering and labelling (AC-020, AC-025, AC-032,
S4/S5, FR-011/FR-012)."""
from __future__ import annotations

import json
import pathlib

from conftest import base_fields

from uapvf.render.fiction import (
    FICTION_BANNER,
    FICTION_PREFIX,
    FICTION_STATEMENT,
    build_fiction_text,
    validate_fiction_text,
)
from uapvf.render.linter import lint_all, lint_report_html
from uapvf.render.report_json import build_report_dict


class TestReportContent:
    def _completed(self, complete_case, conn):
        case_id, _ = complete_case("nmm1.jpg")
        row = conn.execute("SELECT * FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        html = pathlib.Path(row["report_path"]).read_text()
        report = json.loads(
            (pathlib.Path(row["report_path"]).parent / "report.json")
            .read_text())
        return case_id, row, html, report

    def test_html_lints_clean(self, complete_case, conn):
        _, _, html, _ = self._completed(complete_case, conn)
        result = lint_report_html(html)
        assert result["ok"], result["violations"]

    def test_html_neutral_lineage_headline(self, complete_case, conn):
        """B13 MVP report must not claim a seven-lineage phase-2 contract."""
        _, _, html, _ = self._completed(complete_case, conn)
        assert "Seven-lineage" not in html
        assert "Media-analysis lineage summary" in html

    def test_json_sidecar_fields(self, complete_case, conn):
        case_id, row, _, report = self._completed(complete_case, conn)
        assert report["case_id"] == case_id
        assert report["verdict"] == row["verdict"]
        assert report["verdict_text"] == row["verdict_text"]
        assert report["software_version"]
        # Round 4: the four-category FR-006 default battery.
        assert len(report["battery_results"]) == 4
        assert report["analysis_contract"]["expected_lineages"] == 3
        # B13 MVP uses 2-3 vision lineages (spec §1 deferred table); mock
        # fixtures return three. The expected count is taken from config.
        assert report["analysis_contract"]["observed_lineages"] == len(
            report["lineage_outputs"])
        # §5 report.json schema carries no speculation content: fiction is
        # quarantined (S5) and never embedded in the verdict record.
        assert "interpretation_section" not in report
        assert "xenoscience" not in report
        assert "story_assets" not in report
        for br in report["battery_results"]:
            assert br["result"] in ("positive", "negative", "insufficient")
            stamp = br["source_stamp"]
            for key in ("source_id", "query_params", "utc_timestamp",
                        "content_hash", "source_version"):
                assert key in stamp
        assert "uncertainty" in report
        assert "provenance" in report

    def _sectioned_html(self, interpretation_text):
        return (
            "<html><head><title>x</title></head><body>"
            '<div data-section="disclaimer">No output claims origin.</div>'
            '<div data-section="evidence">battery results here</div>'
            f'<div data-section="interpretation">{interpretation_text}</div>'
            "</body></html>"
        )

    def test_linter_catches_claim_language(self):
        bad = self._sectioned_html(
            "This object was confirmed alien in origin.")
        result = lint_report_html(bad)
        assert not result["ok"]

    def test_linter_allows_negated_phrases(self):
        good = self._sectioned_html(
            "The analysis does not claim extraterrestrial origin for the "
            "object.")
        result = lint_report_html(good)
        assert result["ok"], result["violations"]

    def test_linter_scans_nested_alt_text(self):
        bad = (
            "<html><head><title>x</title></head><body>"
            '<div data-section="disclaimer">No output claims origin.</div>'
            '<div data-section="evidence">battery results here'
            '<img src="x.jpg" alt="Confirmed alien craft overhead"></div>'
            '<div data-section="interpretation">The object remains unexplained.</div>'
            "</body></html>"
        )
        result = lint_report_html(bad)
        assert not result["ok"]
        assert any("alien craft" in v for v in result["violations"])

    def test_linter_scans_nested_title_text(self):
        bad = (
            "<html><head><title>x</title></head><body>"
            '<div data-section="disclaimer">No output claims origin.</div>'
            '<div data-section="evidence">battery results here'
            '<span title="Evidence of extraterrestrial origin">hover</span></div>'
            '<div data-section="interpretation">The object remains unexplained.</div>'
            "</body></html>"
        )
        result = lint_report_html(bad)
        assert not result["ok"]
        assert any("extraterrestrial origin" in v for v in result["violations"])

    def test_size_caps_enforced(self, complete_case, conn, settings,
                                monkeypatch):
        import uapvf.pipeline as pipe

        monkeypatch.setattr(pipe, "REPORT_HTML_MAX_BYTES", 100)
        case_id, _ = complete_case("aircraft1.jpg",
                                   base_fields(observed_at="2026-01-15T20:30:00+00:00",
                                               latitude="34.05", longitude="-118.24"))
        row = conn.execute("SELECT status, error_detail FROM cases WHERE "
                           "case_id=?", (case_id,)).fetchone()
        assert row["status"] == "failed"
        assert "size cap" in row["error_detail"]


class TestFictionLayout:
    def test_build_and_validate_roundtrip(self):
        text = build_fiction_text("case-x", ["a" * 60, "b" * 60])
        ok, reasons = validate_fiction_text(text)
        assert ok, reasons
        lines = text.split("\n")
        assert lines[0] == "---"
        assert FICTION_STATEMENT in text
        assert FICTION_BANNER in text

    def test_short_paragraph_rejected(self):
        text = build_fiction_text("case-x", ["too short"])
        ok, reasons = validate_fiction_text(text)
        assert not ok
        assert any("40" in r for r in reasons)

    def test_missing_banner_rejected(self):
        text = build_fiction_text("case-x", ["a" * 60])
        ok, _ = validate_fiction_text(text)
        assert ok
        broken = text.replace(FICTION_BANNER, "some other banner")
        ok, reasons = validate_fiction_text(broken)
        assert not ok

    def test_10kb_cap_trimmed(self):
        text = build_fiction_text("case-x", ["x" * 4000] * 5)
        assert len(text.encode("utf-8")) <= 10 * 1024
        ok, reasons = validate_fiction_text(text)
        assert ok, reasons


class TestFictionGeneration:
    def test_invalid_first_attempt_retries_strict(self, complete_case, conn,
                                                  monkeypatch):
        monkeypatch.setenv("UAPV_MOCK_FICTION_INVALID", "1")
        case_id, _ = complete_case("nmm1.jpg")
        row = conn.execute("SELECT fiction_path FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        assert row["fiction_path"]
        from uapvf import audit as audit_mod

        events = audit_mod.events_for_case(conn, case_id)
        gen = next(e for e in events if e["action"] == "fiction_generated")
        attempts = gen["detail"]["attempts"]
        assert attempts[0]["ok"] is False and attempts[1]["ok"] is True

    def test_both_attempts_invalid_uses_fallback(self, complete_case, conn,
                                                 monkeypatch):
        monkeypatch.setenv("UAPV_MOCK_FICTION_FAIL", "1")
        case_id, _ = complete_case("nmm1.jpg")
        row = conn.execute("SELECT fiction_path, fiction_ready, status FROM "
                           "cases WHERE case_id=?", (case_id,)).fetchone()
        assert row["fiction_path"]  # fallback formatter produces a valid seed
        assert row["fiction_ready"] == 1
        assert row["status"] == "complete"
        fic = pathlib.Path(row["fiction_path"]).read_text()
        ok, reasons = validate_fiction_text(fic)
        assert ok, reasons
        spend_rows = conn.execute(
            "SELECT description FROM spend_entries WHERE case_id=?",
            (case_id,)).fetchall()
        assert any("fiction:fallback" in r["description"] for r in spend_rows)

    def test_unfixable_fiction_cleared_not_failed(self, complete_case, conn,
                                                  monkeypatch):
        monkeypatch.setenv("UAPV_MOCK_FICTION_UNFIXABLE", "1")
        case_id, _ = complete_case("nmm1.jpg")
        row = conn.execute("SELECT fiction_path, fiction_ready, status FROM "
                           "cases WHERE case_id=?", (case_id,)).fetchone()
        assert row["fiction_path"] is None
        assert row["fiction_ready"] == 1
        assert row["status"] == "complete"
        from uapvf import audit as audit_mod

        events = audit_mod.events_for_case(conn, case_id)
        assert any(e["action"] == "fiction_label_validation_failed"
                   for e in events)

    def test_mundane_case_gets_no_fiction(self, complete_case, conn):
        case_id, _ = complete_case(
            "aircraft1.jpg",
            base_fields(observed_at="2026-01-15T20:30:00+00:00",
                        latitude="34.05", longitude="-118.24"))
        row = conn.execute("SELECT verdict, fiction_path FROM cases WHERE "
                           "case_id=?", (case_id,)).fetchone()
        assert row["verdict"] == "mundane_identified"
        assert row["fiction_path"] is None

    def test_rerun_deletes_stale_fiction_for_resolved_case(
            self, complete_case, conn, settings, monkeypatch):
        # first run unresolved -> fiction exists
        monkeypatch.delenv("UAPV_ARCHIVE_MIRROR_PATH", raising=False)
        case_id, _ = complete_case("nmm1.jpg")
        row = conn.execute("SELECT fiction_path, verdict FROM cases WHERE "
                           "case_id=?", (case_id,)).fetchone()
        assert row["verdict"] == "insufficient_data"
        assert row["fiction_path"]
        # force a mundane verdict on rerun via the positive hook
        monkeypatch.setenv("UAPV_MOCK_ADSB_POSITIVE", "1")
        from uapvf import pipeline

        pipeline.rerun_case(conn, case_id)
        pipeline.process_one_case(case_id, settings=settings, conn=conn)
        row2 = conn.execute("SELECT fiction_path, verdict FROM cases WHERE "
                            "case_id=?", (case_id,)).fetchone()
        assert row2["verdict"] == "mundane_identified"
        assert row2["fiction_path"] is None
        assert not pathlib.Path(row["fiction_path"]).exists()
