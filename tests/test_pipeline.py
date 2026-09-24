"""Pipeline lifecycle, durability, and operator-action tests (AC-004,
AC-010..AC-013, AC-017/AC-018, AC-022, AC-024, AC-026..AC-029, AC-031,
§3 restart contract)."""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from conftest import SEEDS, base_fields

from uapvf import audit, pipeline
from uapvf.config import BENCHMARK_BUYER_REF, get_settings, utcnow_iso


class TestFullLifecycle:
    def test_aircraft_case_mundane_no_fiction(self, complete_case, conn):
        case_id, result = complete_case(
            "aircraft1.jpg",
            base_fields(observed_at="2026-01-15T20:30:00+00:00",
                        latitude="34.05", longitude="-118.24"))
        assert result["status"] == "complete"
        row = conn.execute("SELECT * FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        assert row["verdict"] == "mundane_identified"
        assert row["fiction_path"] is None
        assert row["fiction_ready"] == 1
        assert row["report_path"] and pathlib.Path(row["report_path"]).exists()

    def test_nmm_case_with_catalog_is_no_mundane_match_with_fiction(
            self, complete_case, conn, monkeypatch, settings):
        monkeypatch.setenv("UAPV_ARCHIVE_MIRROR_PATH",
                           str(settings.var_dir / "benchmark" / "seed" /
                               "catalog_fixture.sqlite"))
        case_id, result = complete_case("nmm1.jpg", base_fields(weather="clear"))
        assert result["status"] == "complete"
        row = conn.execute("SELECT * FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        assert row["verdict"] == "no_mundane_match"
        # AC-004 / FR-005 mock contract: the deterministic fixture (3
        # lineages, all "unknown") stores agreement_fraction = 1.0, so
        # the flagship journey never wears the spec-impossible
        # low-agreement state (FR-008 low_confidence stays False and the
        # report carries no low-agreement warning).
        assert row["lineage_count"] == 3
        assert row["agreement_fraction"] == 1.0
        report_json = json.loads(
            pathlib.Path(row["report_path"]).with_name(
                "report.json").read_text())
        assert report_json["agreement_fraction"] == 1.0
        assert report_json["uncertainty"]["low_confidence"] is False
        assert row["fiction_path"]
        fic = pathlib.Path(row["fiction_path"]).read_text()
        assert "[SPECULATIVE FICTION" in fic
        assert "NOT FORENSIC EVIDENCE" in fic

    def test_nmm_case_without_catalog_is_insufficient(self, complete_case,
                                                      conn):
        case_id, result = complete_case("nmm1.jpg")
        row = conn.execute("SELECT verdict FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        assert row["verdict"] == "insufficient_data"

    def test_low_quality_case_insufficient_lens(self, complete_case, conn):
        case_id, result = complete_case(
            "insuf1.jpg",
            base_fields(observed_at="2026-01-15T21:30:00+00:00"))
        assert result["status"] == "complete"
        rows = conn.execute(
            "SELECT category, result FROM battery_results WHERE case_id=?",
            (case_id,)).fetchall()
        by_cat = {r["category"]: r["result"] for r in rows}
        assert by_cat["lens_artifacts"] == "insufficient"
        row = conn.execute("SELECT verdict FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        assert row["verdict"] == "insufficient_data"

    def test_stage_rows_all_completed(self, complete_case, conn):
        case_id, _ = complete_case("nmm1.jpg")
        rows = conn.execute(
            "SELECT stage, status FROM pipeline_stage_runs WHERE case_id=? "
            "AND run_version=1 ORDER BY run_id", (case_id,)).fetchall()
        assert [r["stage"] for r in rows] == pipeline.STAGES
        assert all(r["status"] == "completed" for r in rows)

    def test_stage_order_fr004_report_then_seed(self):
        # FR-004: ... -> verdict -> uncertainty -> report -> seed if
        # unresolved. The report renders first (verdict_ready) and the
        # fiction seed completes the case (§3 state machine).
        assert pipeline.STAGES[-2:] == ["report", "fiction"]
        assert pipeline.STAGES.index("report") < pipeline.STAGES.index("fiction")
        # Deferred phase-2 stages (spec.md:80/83) are not part of the MVP.
        assert "xenoscience" not in pipeline.STAGES
        assert "story_assets" not in pipeline.STAGES

    def test_report_stage_commits_verdict_ready_then_fiction_completes(
            self, complete_case, conn):
        case_id, _ = complete_case("nmm1.jpg")
        events = conn.execute(
            "SELECT action FROM audit_events WHERE case_id=? ORDER BY event_id",
            (case_id,)).fetchall()
        actions = [e["action"] for e in events]
        assert "report_rendered" in actions
        assert actions.count("case_complete") == 1
        assert actions.index("report_rendered") < actions.index("case_complete")

    def test_spend_entries_recorded(self, complete_case, conn):
        case_id, _ = complete_case("nmm1.jpg")
        rows = conn.execute(
            "SELECT description, cost_usd FROM spend_entries WHERE case_id=?",
            (case_id,)).fetchall()
        descriptions = [r["description"] for r in rows]
        assert any(d.startswith("lineage:") for d in descriptions)
        assert any(d == "fiction:seed" for d in descriptions)


class TestFailureAndRetry:
    def test_mock_fail_next_marks_failed_then_retry_succeeds(
            self, submit, process, conn, settings, monkeypatch):
        created = submit("nmm1.jpg")
        (settings.var_dir / "mock_fail_next").write_text("")
        result = process(created["case_id"])
        assert result["status"] == "failed"
        row = conn.execute("SELECT error_detail FROM cases WHERE case_id=?",
                           (created["case_id"],)).fetchone()
        assert "mock induced stage failure" in row["error_detail"]
        # the hook file consumed
        assert not (settings.var_dir / "mock_fail_next").exists()
        assert pipeline.retry_case(conn, created["case_id"])["ok"]
        result2 = process(created["case_id"])
        assert result2["status"] == "complete"

    def test_retry_resumes_from_failed_stage_only(self, submit, conn,
                                                  settings):
        created = submit("nmm1.jpg")
        r1 = pipeline.process_one_case(created["case_id"], settings=settings,
                                       conn=conn)
        assert r1["status"] == "complete"
        # Simulate a crash that lost the report stage's commit: delete its
        # row, requeue, and verify the pipeline reuses the earlier committed
        # stages and re-runs only report + fiction.
        conn.execute(
            "DELETE FROM pipeline_stage_runs WHERE case_id=? AND stage='report'",
            (created["case_id"],))
        conn.execute(
            "DELETE FROM pipeline_stage_runs WHERE case_id=? AND stage='fiction'",
            (created["case_id"],))
        conn.execute(
            "UPDATE cases SET status='queued', fiction_ready=0,"
            " fiction_path=NULL, report_path=NULL WHERE case_id=?",
            (created["case_id"],))
        conn.commit()
        r2 = pipeline.process_one_case(created["case_id"], settings=settings,
                                       conn=conn)
        assert r2["status"] == "complete"
        stages = conn.execute(
            "SELECT stage FROM pipeline_stage_runs WHERE case_id=? AND "
            "status='completed'", (created["case_id"],)).fetchall()
        assert {s["stage"] for s in stages} == set(pipeline.STAGES)

    def test_corrupt_stage_output_re_executed(self, submit, conn, settings):
        created = submit("nmm1.jpg")
        pipeline.process_one_case(created["case_id"], settings=settings,
                                  conn=conn)
        # Simulate a crash whose DB write corrupted one stage's output and
        # lost the later stages entirely (a failed case never reaches them).
        conn.execute(
            "UPDATE pipeline_stage_runs SET output_json='NOT JSON{' WHERE "
            "case_id=? AND stage='quality_gate'", (created["case_id"],))
        conn.execute(
            "DELETE FROM pipeline_stage_runs WHERE case_id=? AND stage IN "
            "('report', 'fiction')", (created["case_id"],))
        conn.execute(
            "UPDATE cases SET status='queued', fiction_ready=0,"
            " report_path=NULL, fiction_path=NULL WHERE case_id=?",
            (created["case_id"],))
        conn.commit()
        r2 = pipeline.process_one_case(created["case_id"], settings=settings,
                                       conn=conn)
        assert r2["status"] == "complete"
        events = audit.events_for_case(conn, created["case_id"])
        assert any(e["action"] == "stage_resume_corrupt" for e in events)

    def test_retry_cap_with_force(self, submit, conn):
        created = submit("nmm1.jpg")
        conn.execute(
            "UPDATE cases SET status='failed', retry_count=5 WHERE case_id=?",
            (created["case_id"],))
        conn.commit()
        assert pipeline.retry_case(conn, created["case_id"])["ok"] is False
        assert pipeline.retry_case(conn, created["case_id"], force=True)["ok"]
        row = conn.execute("SELECT retry_count FROM cases WHERE case_id=?",
                           (created["case_id"],)).fetchone()
        assert row["retry_count"] == 0

    def test_unrecoverable_battery_config_failure(self, submit, process,
                                                  conn, settings):
        created = submit("nmm1.jpg")
        (settings.var_dir / "battery_config.yaml").unlink()
        result = process(created["case_id"])
        assert result["status"] == "failed"
        row = conn.execute("SELECT error_detail FROM cases WHERE case_id=?",
                           (created["case_id"],)).fetchone()
        assert "battery_config.yaml empty or missing" in row["error_detail"]


class TestRerun:
    def test_rerun_bumps_version_archives_and_supersedes(
            self, complete_case, conn, settings):
        case_id, _ = complete_case("nmm1.jpg")
        old_report = settings.cases_dir / case_id / "report.html"
        assert old_report.exists()
        assert pipeline.rerun_case(conn, case_id)["ok"]
        r2 = pipeline.process_one_case(case_id, settings=settings, conn=conn)
        assert r2["status"] == "complete"
        row = conn.execute("SELECT * FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        assert row["run_version"] == 2
        archived = settings.cases_dir / case_id / f"report_{case_id}_v1.html"
        assert archived.exists()
        events = audit.events_for_case(conn, case_id)
        assert any(e["action"] == "verdict_superseded" for e in events)
        # old battery rows purged, new ones present
        v1 = conn.execute(
            "SELECT COUNT(*) n FROM battery_results WHERE case_id=? AND "
            "run_version=1", (case_id,)).fetchone()["n"]
        v2 = conn.execute(
            "SELECT COUNT(*) n FROM battery_results WHERE case_id=? AND "
            "run_version=2", (case_id,)).fetchone()["n"]
        # Round 4: one battery row per configured category — the
        # four-category FR-006 default battery.
        assert v1 == 0 and v2 == 4
        # stage rows exist for BOTH versions
        per_version = conn.execute(
            "SELECT run_version, COUNT(*) n FROM pipeline_stage_runs WHERE "
            "case_id=? GROUP BY run_version", (case_id,)).fetchall()
        expected = {1: len(pipeline.STAGES), 2: len(pipeline.STAGES)}
        assert {r["run_version"]: r["n"] for r in per_version} == expected

    def test_rerun_only_from_complete(self, submit, conn):
        created = submit("nmm1.jpg")
        assert pipeline.rerun_case(conn, created["case_id"])["ok"] is False

    def test_duplicate_spend_marked_on_rerun(self, complete_case, conn,
                                             settings):
        case_id, _ = complete_case("nmm1.jpg")
        pipeline.rerun_case(conn, case_id)
        pipeline.process_one_case(case_id, settings=settings, conn=conn)
        rows = conn.execute(
            "SELECT description FROM spend_entries WHERE case_id=?",
            (case_id,)).fetchall()
        descriptions = [r["description"] for r in rows]
        assert any(d.startswith("duplicate_call_recovery:") for d in
                   descriptions)


class TestSpendCapLifecycle:
    def test_claim_time_recheck_blocks_and_rollover_releases(
            self, submit, conn, settings):
        created = submit("nmm1.jpg")
        settings.UAPV_SPEND_CAP_USD = 0.0
        result = pipeline.process_one_case(created["case_id"],
                                           settings=settings, conn=conn)
        row = conn.execute("SELECT status FROM cases WHERE case_id=?",
                           (created["case_id"],)).fetchone()
        assert row["status"] == "spend_capped"
        events = audit.events_for_case(conn, created["case_id"])
        assert any(e["action"] == "spend_cap_blocked" for e in events)
        # cap lifted (new month / raised cap) -> released to queued
        settings.UAPV_SPEND_CAP_USD = 100.0
        released = pipeline.release_spend_capped_if_rollover(conn, settings)
        assert released == 1
        row = conn.execute("SELECT status FROM cases WHERE case_id=?",
                           (created["case_id"],)).fetchone()
        assert row["status"] == "queued"


class TestRestartContract:
    def test_analyzing_reset_to_queued_on_startup(self, submit, conn):
        created = submit("nmm1.jpg")
        conn.execute("UPDATE cases SET status='analyzing' WHERE case_id=?",
                     (created["case_id"],))
        conn.commit()
        n = pipeline.reset_analyzing_on_startup(conn)
        assert n == 1
        row = conn.execute("SELECT status FROM cases WHERE case_id=?",
                           (created["case_id"],)).fetchone()
        assert row["status"] == "queued"

    def test_verdict_ready_crash_orphan_recovers_on_startup(
            self, complete_case, conn):
        """A crash between the report commit (verdict_ready) and the fiction
        stage must not strand the case: verdict_ready is strictly transient,
        so startup re-queues it and the resume contract re-runs only the
        pending fiction stage."""
        case_id, _ = complete_case("nmm1.jpg")
        # Simulate the crash: status rolled back to verdict_ready, fiction
        # stage row and artifact gone.
        conn.execute(
            "DELETE FROM pipeline_stage_runs WHERE case_id=? AND stage='fiction'",
            (case_id,))
        conn.execute(
            "UPDATE cases SET status='verdict_ready', fiction_ready=0,"
            " fiction_path=NULL, fiction_sha256=NULL WHERE case_id=?",
            (case_id,))
        conn.commit()
        assert pipeline.reset_analyzing_on_startup(conn) == 1
        row = conn.execute("SELECT status FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        assert row["status"] == "queued"
        events = audit.events_for_case(conn, case_id)
        assert any(e["action"] == "startup_reset_to_queued"
                   and (e.get("detail") or {}).get("previous_status")
                   == "verdict_ready"
                   for e in events)

    def test_orphan_tmp_cleanup(self, settings, submit):
        created = submit("nmm1.jpg")
        case_dir = settings.cases_dir / created["case_id"]
        orphan = case_dir / "report.html.tmp"
        orphan.write_text("orphan")
        removed = pipeline.cleanup_orphan_tmp_files(settings)
        assert removed >= 1
        assert not orphan.exists()

    def test_uncommitted_case_directory_cleanup(self, settings, conn):
        orphan = settings.cases_dir / "06b6e3e5-24bb-476d-a38d-2c37ec8bd6f2"
        orphan.mkdir(parents=True)
        (orphan / "hostile-upload.bin").write_bytes(b"orphan")
        assert pipeline.cleanup_uncommitted_case_dirs(settings, conn) == 1
        assert not orphan.exists()


class TestOperatorActions:
    def test_delete_hard_deletes_but_audit_survives(self, complete_case,
                                                    conn, settings):
        case_id, _ = complete_case("nmm1.jpg")
        assert pipeline.delete_case(conn, case_id, settings)["ok"]
        assert conn.execute("SELECT COUNT(*) n FROM cases WHERE case_id=?",
                            (case_id,)).fetchone()["n"] == 0
        assert not (settings.cases_dir / case_id).exists()
        events = conn.execute(
            "SELECT action FROM audit_events WHERE case_id=?",
            (case_id,)).fetchall()
        actions = [e["action"] for e in events]
        assert "case_deleted" in actions
        assert "case_created" in actions
        # cascade deletes
        assert conn.execute(
            "SELECT COUNT(*) n FROM battery_results WHERE case_id=?",
            (case_id,)).fetchone()["n"] == 0
        # spend entries keep cost with NULL case ref
        spend_rows = conn.execute(
            "SELECT case_id FROM spend_entries WHERE description LIKE "
            "'lineage:%'").fetchall()
        assert all(r["case_id"] is None for r in spend_rows)

    def test_payment_status_transitions_audited(self, complete_case, conn):
        case_id, _ = complete_case("nmm1.jpg")
        assert pipeline.set_payment(conn, case_id, "paid")["ok"]
        row = conn.execute("SELECT payment_status FROM cases WHERE case_id=?",
                           (case_id,)).fetchone()
        assert row["payment_status"] == "paid"
        events = audit.events_for_case(conn, case_id)
        assert any(e["action"] == "payment_status_changed" and
                   e["detail"]["new"] == "paid" for e in events)
        assert pipeline.set_payment(conn, case_id, "bogus")["ok"] is False

    def test_mock_hooks_drone_and_artifact(self, submit, process, conn,
                                           settings, monkeypatch):
        # Round 4 (AC-033): a drone CLASSIFICATION is an untested mundane
        # category under the four-category FR-006 battery, so it must force
        # insufficient_data via the uncovered-mundane amendment — the old
        # eleven-category battery wired a drones category whose positive
        # silently defeated that amendment (the round-3 reopen).
        monkeypatch.setenv("UAPV_MOCK_DRONE", "1")
        created = submit("nmm1.jpg")
        process(created["case_id"])
        row = conn.execute("SELECT verdict, verdict_text FROM cases "
                           "WHERE case_id=?", (created["case_id"],)).fetchone()
        assert row["verdict"] == "insufficient_data"
        # The amendment's verdict text names the untested classification
        # and states the insufficient_data-instead-of-no_mundane_match rule.
        assert "drone" in row["verdict_text"]
        assert "insufficient_data rather than no_mundane_match" in (
            row["verdict_text"])
        stage_out = conn.execute(
            "SELECT output_json FROM pipeline_stage_runs WHERE case_id=? "
            "AND stage='verdict'", (created["case_id"],)).fetchone()
        import json as json_mod
        assert json_mod.loads(stage_out["output_json"]).get("reason") == (
            "uncovered_mundane_classification")
        events = audit.events_for_case(conn, created["case_id"])
        assert any(e["action"] == "verdict_set"
                   and e["detail"]["reason"]
                   == "uncovered_mundane_classification" for e in events)

    def test_mock_artifact_hook_identifies_lens_artifacts(self, submit,
                                                          process, conn,
                                                          settings,
                                                          monkeypatch):
        # The FR-006 lens_artifacts category (artifact group
        # {lens_artifact, sensor_artifact}) is the battery home for
        # artifact detections.
        monkeypatch.setenv("UAPV_MOCK_ARTIFACT", "1")
        created = submit("nmm1.jpg")
        process(created["case_id"])
        row = conn.execute("SELECT verdict, verdict_text FROM cases WHERE "
                           "case_id=?", (created["case_id"],)).fetchone()
        assert row["verdict"] == "mundane_identified"
        assert "lens_artifacts" in row["verdict_text"]

    def test_zero_lineages_path(self, submit, process, conn, settings,
                                monkeypatch):
        monkeypatch.setenv("UAPV_MOCK_ZERO_LINEAGES", "1")
        created = submit("nmm1.jpg", base_fields(weather="clear"))
        process(created["case_id"])
        row = conn.execute(
            "SELECT lineage_count, agreement_fraction FROM cases WHERE "
            "case_id=?", (created["case_id"],)).fetchone()
        assert row["lineage_count"] == 0
        assert row["agreement_fraction"] == 0.0
        rows = conn.execute(
            "SELECT result, source_stamp_json FROM battery_results WHERE "
            "case_id=? AND category='lens_artifacts'",
            (created["case_id"],)).fetchall()
        assert rows[0]["result"] == "insufficient"
        assert json.loads(rows[0]["source_stamp_json"])["reason"] == (
            "no vision lineages available")


# ---------------------------------------------------------------------------
# Round 5 (FIX-509/TDAD): live-path wiring of evaluate_operator_gates into
# compute_concordance at the pipeline call site (spec §3.3/§3.4). The
# seven-lineage fixtures and certification-artifact writers are shared with
# test_rigor.py so the pipeline call site exercises exactly the documents
# `lineages validate` / `lineages calibrate` produce.
# ---------------------------------------------------------------------------

from test_rigor import (  # noqa: E402
    _write_calibration_records,
    _write_independence_report,
    unanimous,
)


def _contract_dicts():
    return [o.to_dict() for o in unanimous()]


class TestLineageRigorCallSite:
    def test_legacy_lineage_dicts_return_none(self, settings):
        legacy = [{"lineage_id": "lineage-1", "classification": "unknown",
                   "artifact_detected": False, "hypothesis": "h",
                   "model_version": "mock-vision-v1"}]
        assert pipeline.compute_lineage_rigor(legacy, settings) is None
        assert pipeline.compute_lineage_rigor([], settings) is None

    def test_live_fails_closed_without_operator_artifacts(self, settings):
        out = pipeline.compute_lineage_rigor(_contract_dicts(), settings)
        assert out is not None
        assert out["runtime"] == "live"
        assert out["passed"] is False
        assert out["conditions"]["4"] is False
        assert out["conditions"]["5"] is False
        assert "4" in out["reason"] and "5" in out["reason"]
        gates = out["operator_gates"]
        assert gates["independence_validated"] is False
        assert gates["independence_reason"] == "independence_report_missing"
        assert gates["calibration_valid"] is False
        assert gates["calibration_reason"].startswith("calibration_missing:")

    def test_live_passes_with_operator_certification(self, settings):
        _write_independence_report(settings.var_dir)
        _write_calibration_records(settings.var_dir)
        out = pipeline.compute_lineage_rigor(_contract_dicts(), settings)
        assert out["passed"] is True
        assert out["conditions"]["4"] is True
        assert out["conditions"]["5"] is True
        assert out["operator_gates"]["independence_validated"] is True
        assert out["operator_gates"]["calibration_valid"] is True
        assert out["agreement_fraction"] == pytest.approx(17.0 / 21.0)

    def test_removed_simulation_runtime_fails_closed_with_named_error(
            self, settings, monkeypatch):
        """Round 4: the spec's mode model is exactly two modes (mock vs
        live engine). The removed ``simulation`` runtime silently routed
        to the paid live engine under a stale label, so selecting it now
        fails closed with a named error instead of running anything."""
        from uapvf.config import LineageRuntimeRemovedError

        monkeypatch.setenv("UAPV_LINEAGE_RUNTIME", "simulation")
        with pytest.raises(LineageRuntimeRemovedError) as ei:
            get_settings().lineage_runtime
        assert "removed runtime" in str(ei.value)
        with pytest.raises(LineageRuntimeRemovedError):
            pipeline.compute_lineage_rigor(
                _contract_dicts(), get_settings())

    def test_mock_runtime_blocked(self, settings, monkeypatch):
        monkeypatch.setenv("UAPV_LINEAGE_RUNTIME", "mock")
        out = pipeline.compute_lineage_rigor(_contract_dicts(), get_settings())
        assert out["runtime"] == "mock"
        assert out["passed"] is False
        assert out["reason"] == "runtime_blocked"
        assert out["required_conditions"] == "blocked"

    def test_unknown_runtime_fails_closed_to_live(self, settings, monkeypatch):
        monkeypatch.setenv("UAPV_LINEAGE_RUNTIME", "bogus")
        s = get_settings()
        assert s.lineage_runtime == "live"
        out = pipeline.compute_lineage_rigor(_contract_dicts(), s)
        assert out["runtime"] == "live"
        assert out["passed"] is False
        assert out["conditions"]["4"] is False


class TestStageRigorWiring:
    def _run_stage(self, submit, conn, settings, monkeypatch,
                   lineages=None):
        created = submit("nmm1.jpg", base_fields(weather="clear"))
        case = conn.execute("SELECT * FROM cases WHERE case_id=?",
                            (created["case_id"],)).fetchone()
        monkeypatch.setattr(
            pipeline, "run_lineages",
            lambda case_id, media_path, metadata, st: (
                lineages if lineages is not None else _contract_dicts()))
        context = {"stage_outputs": {"quality_gate": {"quality_gate_pass": True}}}
        out = pipeline._stage_lineage_analysis(conn, case, settings, context)
        return created["case_id"], out

    def test_stage_wires_rigor_fail_closed_and_audits(
            self, submit, conn, settings, monkeypatch):
        case_id, out = self._run_stage(submit, conn, settings, monkeypatch)
        assert out["rigor"] is not None
        assert out["rigor"]["runtime"] == "live"
        assert out["rigor"]["passed"] is False
        # §3.3 concordance supersedes the majority-vote fraction.
        assert out["agreement_fraction"] == round(
            out["rigor"]["agreement_fraction"], 6)
        row = conn.execute(
            "SELECT agreement_fraction FROM cases WHERE case_id=?",
            (case_id,)).fetchone()
        assert row["agreement_fraction"] == out["agreement_fraction"]
        events = audit.events_for_case(conn, case_id)
        rigor_events = [e for e in events if e["action"] == "rigor_evaluated"]
        assert len(rigor_events) == 1
        assert rigor_events[0]["detail"]["passed"] is False
        assert (rigor_events[0]["detail"]["operator_gates"]
                ["independence_reason"] == "independence_report_missing")

    def test_stage_wires_certified_rigor_pass(
            self, submit, conn, settings, monkeypatch):
        _write_independence_report(settings.var_dir)
        _write_calibration_records(settings.var_dir)
        case_id, out = self._run_stage(submit, conn, settings, monkeypatch)
        assert out["rigor"]["passed"] is True
        events = audit.events_for_case(conn, case_id)
        assert any(e["action"] == "rigor_evaluated"
                   and e["detail"]["passed"] is True for e in events)

    def test_stage_legacy_lineages_keep_major_vote_and_no_rigor(
            self, submit, conn, settings, monkeypatch):
        legacy = [
            {"lineage_id": f"lineage-{i}", "classification": "aircraft",
             "artifact_detected": False, "hypothesis": "h",
             "model_version": "mock-vision-v1"}
            for i in range(1, 4)
        ]
        case_id, out = self._run_stage(submit, conn, settings, monkeypatch,
                                       lineages=legacy)
        assert out["rigor"] is None
        assert out["agreement_fraction"] == 1.0  # 3/3 majority vote
        events = audit.events_for_case(conn, case_id)
        assert not any(e["action"] == "rigor_evaluated" for e in events)

    def test_unknown_labels_count_as_plurality_per_fr005(
            self, submit, conn, settings, monkeypatch):
        # FR-005 worked example: agreement_fraction is the plurality
        # ratio over ALL classification labels — 3 lineages labelled
        # ["unknown", "unknown", "unknown"] score 3/3 = 1.0. That is the
        # mock contract (AC-004 stores 1.0 for the flagship journey);
        # the earlier "unknown can never create agreement" redefinition
        # stored 0.0 and forced a spec-impossible low_confidence state.
        legacy = [
            {"lineage_id": f"lineage-{i}", "classification": "unknown",
             "artifact_detected": False, "hypothesis": "no supported class",
             "model_version": "fixture"}
            for i in range(1, 4)
        ]
        _case_id, out = self._run_stage(
            submit, conn, settings, monkeypatch, lineages=legacy
        )
        assert out["rigor"] is None
        assert out["agreement_fraction"] == 1.0
        # FR-005: with a single lineage there is no agreement to
        # measure — the fraction is defined as 0.0.
        _case_id, out = self._run_stage(
            submit, conn, settings, monkeypatch, lineages=legacy[:1]
        )
        assert out["rigor"] is None
        assert out["agreement_fraction"] == 0.0

    def test_verdict_stage_consumes_rigor_qualification(
            self, submit, conn, settings, monkeypatch):
        # Blocked live rigor + all-negative battery -> no_mundane_match is
        # qualified provisional in the persisted verdict_text.
        monkeypatch.setenv(
            "UAPV_ARCHIVE_MIRROR_PATH",
            str(settings.var_dir / "benchmark" / "seed" /
                "catalog_fixture.sqlite"))
        created = submit("nmm1.jpg", base_fields(weather="clear"))
        monkeypatch.setattr(pipeline, "run_lineages",
                            lambda case_id, media_path, metadata, st:
                            _contract_dicts())
        result = pipeline.process_one_case(created["case_id"],
                                           settings=settings, conn=conn)
        assert result["status"] == "complete"
        row = conn.execute("SELECT verdict, verdict_text FROM cases WHERE "
                           "case_id=?", (created["case_id"],)).fetchone()
        assert row["verdict"] == "no_mundane_match"
        assert "provisional" in row["verdict_text"]
        events = audit.events_for_case(conn, created["case_id"])
        assert any(e["action"] == "verdict_set"
                   and "rigor_not_certified" in e["detail"]["qualifications"]
                   for e in events)

    def test_verdict_stage_unqualified_when_certified(
            self, submit, conn, settings, monkeypatch):
        monkeypatch.setenv(
            "UAPV_ARCHIVE_MIRROR_PATH",
            str(settings.var_dir / "benchmark" / "seed" /
                "catalog_fixture.sqlite"))
        _write_independence_report(settings.var_dir)
        _write_calibration_records(settings.var_dir)
        created = submit("nmm1.jpg", base_fields(weather="clear"))
        monkeypatch.setattr(pipeline, "run_lineages",
                            lambda case_id, media_path, metadata, st:
                            _contract_dicts())
        result = pipeline.process_one_case(created["case_id"],
                                           settings=settings, conn=conn)
        assert result["status"] == "complete"
        row = conn.execute("SELECT verdict, verdict_text FROM cases WHERE "
                           "case_id=?", (created["case_id"],)).fetchone()
        assert row["verdict"] == "no_mundane_match"
        assert "provisional" not in row["verdict_text"]

    def test_verdict_stage_consumes_coverage_qualification(
            self, submit, conn, settings):
        created = submit("nmm1.jpg")
        case = conn.execute("SELECT * FROM cases WHERE case_id=?",
                            (created["case_id"],)).fetchone()
        context = {
            "stage_outputs": {
                "battery": {
                    "results": [
                        {"category": "aircraft", "result": "negative",
                         "evidence_citation": "checked",
                         "source_stamp": {}},
                    ],
                    "coverage": {"weather": "sparse"},
                },
                "lineage_analysis": {"lineages": []},
            },
        }
        verdict = pipeline._stage_verdict(conn, case, settings, context)
        assert verdict["verdict"] == "no_mundane_match"
        assert "coverage_sparse" in verdict["qualifications"]
        assert "coverage is sparse" in verdict["verdict_text"]
        events = audit.events_for_case(conn, created["case_id"])
        assert any(e["action"] == "verdict_set"
                   and "coverage_sparse" in e["detail"].get("qualifications", [])
                   for e in events)


class TestLiveLineagesRouteThroughSneferuAdapter:
    """FR-005: EVERY non-mock lineage analysis goes through the Sneferu
    adapter (engine submission); no local/sandboxed lineage execution
    path remains in the pipeline (reopened blockers 80a8e3181d21 /
    10af4c51241a / 623fa1d563e2; subprocess isolation spec.md:85 is
    phase 2)."""

    def _run_stage(self, submit, conn, monkeypatch, lineages=None, exc=None):
        monkeypatch.setenv("SNEFERU_MOCK", "0")
        settings = get_settings()
        created = submit("nmm1.jpg", base_fields(weather="clear"))
        case = conn.execute("SELECT * FROM cases WHERE case_id=?",
                            (created["case_id"],)).fetchone()
        calls = []

        def fake_run_lineages(case_id, media_path, metadata, st):
            calls.append({"case_id": case_id, "media_path": media_path,
                          "metadata": metadata})
            if exc is not None:
                raise exc
            return lineages

        monkeypatch.setattr(pipeline, "run_lineages", fake_run_lineages)
        context = {"stage_outputs": {
            "quality_gate": {"quality_gate_pass": True}}}
        out = pipeline._stage_lineage_analysis(conn, case, settings, context)
        return created["case_id"], out, calls

    def test_live_stage_calls_adapter_with_metadata(self, submit, conn,
                                                    monkeypatch):
        engine = [
            {"lineage_id": f"engine-lineage-{i}",
             "classification": "unknown", "artifact_detected": False,
             "hypothesis": "h", "model_version": "engine"}
            for i in range(1, 3)
        ]
        case_id, out, calls = self._run_stage(submit, conn, monkeypatch,
                                              lineages=engine)
        assert len(calls) == 1
        assert calls[0]["metadata"]["latitude"] == 34.5
        assert out["lineage_count"] == 2
        assert out["sandbox"] is None
        row = conn.execute(
            "SELECT sandbox_verified, lineage_runtime FROM cases "
            "WHERE case_id=?", (case_id,)).fetchone()
        assert row["sandbox_verified"] == 0
        assert row["lineage_runtime"] == "live"

    def test_live_adapter_timeout_is_transient(self, submit, conn,
                                               monkeypatch):
        from uapvf.adapters.sneferu_adapter import AdapterTimeout

        with pytest.raises(pipeline.StageTransientError):
            self._run_stage(submit, conn, monkeypatch,
                            exc=AdapterTimeout("engine down"))

    def test_live_adapter_schema_error_is_unrecoverable(self, submit, conn,
                                                        monkeypatch):
        from uapvf.adapters.sneferu_adapter import AdapterSchemaError

        with pytest.raises(pipeline.StageUnrecoverableError):
            self._run_stage(submit, conn, monkeypatch,
                            exc=AdapterSchemaError("bad engine schema"))


class TestEngineLineageSubmission:
    """The adapter's live path submits case media + field dictionary to
    the Sneferu engine and validates the FR-005 response schema strictly
    — it never fabricates lineage outputs (FR-005, A-013)."""

    def _live_settings(self, monkeypatch):
        monkeypatch.setenv("SNEFERU_MOCK", "0")
        return get_settings()

    def test_submission_body_url_and_validation(self, var_dir, monkeypatch,
                                                tmp_path):
        import base64
        import hashlib

        import httpx

        import uapvf.adapters.sneferu_adapter as sa

        settings = self._live_settings(monkeypatch)
        media = tmp_path / "normalized.jpg"
        media.write_bytes(b"fake-media-bytes")
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"lineages": [
                    {"classification": "drone", "artifact_detected": False,
                     "hypothesis": "small quadcopter"},
                    {"classification": "drone"},
                ]}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["body"] = json
            captured["timeout"] = timeout
            return FakeResponse()

        monkeypatch.setattr(httpx, "post", fake_post)
        out = sa.run_lineages("case-x", media, {"latitude": "34.5"},
                              settings)
        assert captured["url"] == (
            settings.SNEFERU_SDK_URL.rstrip("/") + sa.LINEAGE_ENGINE_PATH)
        assert captured["timeout"] == sa.LINEAGE_ENGINE_TIMEOUT_S
        body = captured["body"]
        assert body["case_id"] == "case-x"
        assert body["fields"] == {"latitude": "34.5"}
        assert body["media_base64"] == base64.b64encode(
            b"fake-media-bytes").decode("ascii")
        assert body["media_sha256"] == hashlib.sha256(
            b"fake-media-bytes").hexdigest()
        assert [l["classification"] for l in out] == ["drone", "drone"]
        assert out[0]["lineage_id"] == "engine-lineage-1"
        assert out[0]["hypothesis"] == "small quadcopter"

    def test_out_of_enum_classification_is_unrecoverable(self, var_dir,
                                                         monkeypatch,
                                                         tmp_path):
        import httpx

        import uapvf.adapters.sneferu_adapter as sa

        settings = self._live_settings(monkeypatch)
        media = tmp_path / "normalized.jpg"
        media.write_bytes(b"fake-media-bytes")

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"lineages": [{"classification": "alien craft"}]}

        monkeypatch.setattr(httpx, "post",
                            lambda *a, **k: FakeResponse())
        with pytest.raises(sa.AdapterSchemaError):
            sa.run_lineages("case-x", media, {}, settings)

    def test_transport_failure_is_transient(self, var_dir, monkeypatch,
                                          tmp_path):
        import httpx

        import uapvf.adapters.sneferu_adapter as sa

        settings = self._live_settings(monkeypatch)
        media = tmp_path / "normalized.jpg"
        media.write_bytes(b"fake-media-bytes")

        def boom(*args, **kwargs):
            raise httpx.ConnectError("engine unreachable")

        monkeypatch.setattr(httpx, "post", boom)
        with pytest.raises(sa.AdapterTimeout):
            sa.run_lineages("case-x", media, {}, settings)

    def test_engine_rejection_is_unrecoverable(self, var_dir, monkeypatch,
                                               tmp_path):
        import httpx

        import uapvf.adapters.sneferu_adapter as sa

        settings = self._live_settings(monkeypatch)
        media = tmp_path / "normalized.jpg"
        media.write_bytes(b"fake-media-bytes")

        class FakeResponse:
            status_code = 422

            def json(self):
                return {}

        monkeypatch.setattr(httpx, "post",
                            lambda *a, **k: FakeResponse())
        with pytest.raises(sa.AdapterSchemaError):
            sa.run_lineages("case-x", media, {}, settings)

    def test_removed_simulation_runtime_blocked_before_engine_call(
            self, var_dir, monkeypatch, tmp_path):
        """Round 4: the removed simulation runtime must fail closed before any
        engine spend, in both the lineage and fiction entry points."""
        import uapvf.adapters.sneferu_adapter as sa
        from uapvf.config import LineageRuntimeRemovedError

        monkeypatch.setenv("SNEFERU_MOCK", "0")
        monkeypatch.setenv("UAPV_LINEAGE_RUNTIME", "simulation")
        settings = get_settings()
        media = tmp_path / "normalized.jpg"
        media.write_bytes(b"fake-media-bytes")
        with pytest.raises(LineageRuntimeRemovedError):
            sa.run_lineages("case-x", media, {}, settings)
        with pytest.raises(LineageRuntimeRemovedError):
            sa.generate_fiction("case-x", {}, settings)


class TestEngineFictionSubmission:
    """FR-011: live fiction generation is a single-model text-generation
    call through the Sneferu adapter returning
    ``{"paragraphs": [...], "call_id": "..."}`` — never the static local
    template. The FR-011 retry-once ladder engages on failure, the
    deterministic fallback formatter is the LAST rung only, and FR-016
    books spend for real SDK calls only."""

    ENGINE_PARAGRAPHS = [
        "Engine paragraph one carries the observation across the ridge "
        "without ever asserting what made it.",
        "Engine paragraph two keeps the watchers' account exactly as "
        "recorded, strange and unresolved.",
        "Engine paragraph three leaves the night a question no checklist "
        "on the ground could answer.",
    ]

    def _live_settings(self, monkeypatch):
        monkeypatch.setenv("SNEFERU_MOCK", "0")
        return get_settings()

    def test_live_generate_fiction_calls_engine_with_prompt_contract(
            self, var_dir, monkeypatch):
        import httpx

        import uapvf.adapters.sneferu_adapter as sa

        settings = self._live_settings(monkeypatch)
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"paragraphs": list(self.paragraphs),
                        "call_id": "engine-call-1"}

        FakeResponse.paragraphs = self.ENGINE_PARAGRAPHS

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["body"] = json
            captured["timeout"] = timeout
            return FakeResponse()

        monkeypatch.setattr(httpx, "post", fake_post)
        out = sa.generate_fiction(
            "case-x",
            {"case_id": "case-x", "observed_at": "2026-01-15T21:15:00+00:00",
             "location_text": "Antelope Valley", "shape": "light"},
            settings)
        assert captured["url"] == (
            settings.SNEFERU_SDK_URL.rstrip("/") + sa.FICTION_ENGINE_PATH)
        assert captured["timeout"] == sa.FICTION_ENGINE_TIMEOUT_S
        body = captured["body"]
        assert body["case_id"] == "case-x"
        # FR-011 prompt: case metadata + the non-assertion constraint.
        assert "Antelope Valley" in body["prompt"]
        assert "do not assert extraterrestrial origin" in body["prompt"].lower()
        assert body["max_tokens"] == 2000
        assert body["temperature"] == 0.7
        assert out["paragraphs"] == self.ENGINE_PARAGRAPHS
        assert out["call_id"] == "engine-call-1"
        assert out["source"] == "engine"

    def test_live_invalid_engine_payload_is_schema_error(
            self, var_dir, monkeypatch):
        import httpx

        import uapvf.adapters.sneferu_adapter as sa

        settings = self._live_settings(monkeypatch)

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"paragraphs": []}  # empty + missing call_id

        monkeypatch.setattr(httpx, "post",
                            lambda *a, **k: FakeResponse())
        with pytest.raises(sa.AdapterSchemaError):
            sa.generate_fiction("case-x", {}, settings)

    def test_live_transport_failure_is_transient(self, var_dir, monkeypatch):
        import httpx

        import uapvf.adapters.sneferu_adapter as sa

        settings = self._live_settings(monkeypatch)

        def boom(*args, **kwargs):
            raise httpx.ConnectError("engine unreachable")

        monkeypatch.setattr(httpx, "post", boom)
        with pytest.raises(sa.AdapterTimeout):
            sa.generate_fiction("case-x", {}, settings)

    def test_live_engine_success_books_fiction_seed_spend(
            self, submit, conn, monkeypatch):
        """The live path writes the ENGINE paragraphs and books exactly
        one fiction:seed entry — a real SDK call (FR-016)."""
        import httpx

        import uapvf.adapters.sneferu_adapter as sa

        created = submit("nmm1.jpg")
        case = conn.execute("SELECT * FROM cases WHERE case_id=?",
                            (created["case_id"],)).fetchone()
        settings = self._live_settings(monkeypatch)

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"paragraphs": list(TestEngineFictionSubmission
                                           .ENGINE_PARAGRAPHS),
                        "call_id": "engine-call-2"}

        monkeypatch.setattr(httpx, "post",
                            lambda *a, **k: FakeResponse())
        out = pipeline._stage_fiction(conn, case, settings, {})
        assert out["skipped"] is False
        fic = pathlib.Path(out["fiction_path"]).read_text(encoding="utf-8")
        assert self.ENGINE_PARAGRAPHS[0] in fic
        rows = conn.execute(
            "SELECT description FROM spend_entries WHERE case_id=?",
            (created["case_id"],)).fetchall()
        descriptions = [r["description"] for r in rows]
        assert descriptions.count("fiction:seed") == 1
        assert not any("fiction:fallback" in d for d in descriptions)
        assert sa.FICTION_ENGINE_PATH and out["fiction_sha256"]

    def test_live_engine_failure_falls_back_without_spend(
            self, submit, conn, monkeypatch):
        """Both engine attempts fail -> the FR-011 fallback formatter
        still produces a valid labelled seed, and FR-016 books NOTHING:
        no SDK call succeeded, so no compute cost exists to charge."""
        import httpx

        created = submit("nmm1.jpg")
        case = conn.execute("SELECT * FROM cases WHERE case_id=?",
                            (created["case_id"],)).fetchone()
        settings = self._live_settings(monkeypatch)

        def boom(*args, **kwargs):
            raise httpx.ConnectError("engine unreachable")

        monkeypatch.setattr(httpx, "post", boom)
        out = pipeline._stage_fiction(conn, case, settings, {})
        assert out["skipped"] is False
        assert out["fiction_path"]
        fic = pathlib.Path(out["fiction_path"]).read_text(encoding="utf-8")
        from uapvf.render.fiction import validate_fiction_text

        ok, reasons = validate_fiction_text(fic)
        assert ok, reasons
        rows = conn.execute(
            "SELECT description FROM spend_entries WHERE case_id=?",
            (created["case_id"],)).fetchall()
        assert [r["description"] for r in rows] == []
