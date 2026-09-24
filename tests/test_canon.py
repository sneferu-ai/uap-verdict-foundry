from __future__ import annotations

import pytest

# Stale against the shipped MVP scope: canon graph removed with its DDL in round 4 (README: What runs per case).
pytestmark = pytest.mark.skip(reason="phase 2 / removed: canon graph removed with its DDL in round 4 (README: What runs per case)")



def test_canon_disabled_by_default_and_non_destructive(settings, conn, complete_case):
    from uapvf.canon import is_enabled, project_case, set_enabled, status

    case_id, _ = complete_case("nmm1.jpg")
    assert is_enabled(settings) is False
    assert project_case(conn, settings, case_id)["reason"] == "canon_disabled"
    set_enabled(settings, True)
    projected = project_case(conn, settings, case_id)
    assert projected["projected"] is True
    assert projected["evidence_eligible"] is False
    assert len(projected["nodes"]) >= 4
    before = status(conn, settings)
    assert before["node_count"] >= 4
    set_enabled(settings, False)
    after = status(conn, settings)
    assert after["enabled"] is False
    assert after["node_count"] == before["node_count"]
    # The evidence-side report is untouched by post-run canon projection.
    report = (settings.cases_dir / case_id / "report.json").read_text()
    assert '"evidence_eligible": false' in report


def test_enabled_pipeline_projects_canon(settings, conn, submit, process):
    from uapvf.canon import set_enabled

    set_enabled(settings, True)
    created = submit("nmm1.jpg")
    result = process(created["case_id"])
    assert result["status"] == "complete"
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM canon_nodes WHERE case_id=?",
        (created["case_id"],)).fetchone()
    assert row["n"] >= 4
