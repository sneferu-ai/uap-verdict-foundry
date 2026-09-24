"""Audit chain integrity (AC-009, FR-015)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from uapvf import audit


class TestChain:
    def test_chain_verifies_after_case(self, complete_case, conn):
        case_id, _ = complete_case("nmm1.jpg")
        result = audit.verify_chain(conn)
        assert result["ok"]
        assert result["entries_checked"] > 5

    def test_genesis_prev_hash(self, conn):
        audit.append_event(conn, None, "operator", "test_action", {"a": 1})
        row = conn.execute(
            "SELECT prev_hash FROM audit_events ORDER BY event_id LIMIT 1"
        ).fetchone()
        assert row["prev_hash"] == "GENESIS"

    def test_tamper_detection(self, complete_case, conn):
        case_id, _ = complete_case("nmm1.jpg")
        conn.execute(
            "UPDATE audit_events SET detail_json = ? WHERE event_id = ("
            "SELECT MIN(event_id) FROM audit_events WHERE case_id = ?)",
            (json.dumps({"tampered": True}), case_id),
        )
        conn.commit()
        result = audit.verify_chain(conn)
        assert not result["ok"]
        assert any(p["kind"] == "hash_mismatch" for p in result["problems"])

    def test_chain_break_detection(self, conn):
        audit.append_event(conn, None, "operator", "a1", {})
        audit.append_event(conn, None, "operator", "a2", {})
        conn.execute(
            "UPDATE audit_events SET prev_hash = 'WRONG' WHERE event_id = ("
            "SELECT MAX(event_id) FROM audit_events)")
        conn.commit()
        result = audit.verify_chain(conn)
        assert any(p["kind"] == "chain_break" for p in result["problems"])

    def test_invalid_actor_rejected(self, conn):
        with pytest.raises(ValueError):
            audit.append_event(conn, None, "attacker", "x", {})

    def test_append_composes_with_outer_transaction(self, conn):
        conn.execute("BEGIN")
        audit.append_event(conn, None, "operator", "nested", {"ok": True})
        assert conn.in_transaction
        conn.rollback()
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE action = 'nested'"
        ).fetchone()["n"]
        assert count == 0

    def test_events_survive_case_delete(self, conn, settings):
        from uapvf import pipeline
        from uapvf.intake import create_case
        from conftest import base_fields

        created = create_case(__import__("pathlib").Path(
            "var/benchmark/seed/nmm1.jpg"), base_fields(), settings, conn)
        case_id = created["case_id"]
        pipeline.delete_case(conn, case_id, settings)
        n = conn.execute(
            "SELECT COUNT(*) n FROM audit_events WHERE case_id=?",
            (case_id,)).fetchone()["n"]
        assert n >= 2


class TestCaseOwnershipConstraints:
    @pytest.mark.parametrize(
        "statement, values",
        [
            (
                "INSERT INTO battery_results "
                "(case_id, category, run_version, result, source_stamp_json, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (None, "aircraft", 1, "negative", "{}", "now"),
            ),
            (
                "INSERT INTO lineage_outputs "
                "(case_id, lineage_id, run_version, recorded_at) VALUES (?, ?, ?, ?)",
                (None, "lineage-1", 1, "now"),
            ),
            (
                "INSERT INTO pipeline_stage_runs "
                "(case_id, stage, status, started_at) VALUES (?, ?, ?, ?)",
                (None, "quality", "pending", "now"),
            ),
        ],
    )
    def test_child_rows_require_case_id(self, conn, statement, values):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement, values)
        conn.rollback()
