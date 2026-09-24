"""SQLite storage layer (spec §5).

WAL mode, 5000 ms busy timeout, foreign keys on — set on *every* connection
by the connection factory. The schema is created by ``uapvf db init``
(no migration framework in MVP; backup/restore is the manual migration path).
The ``cases.deleted_at`` column deliberately does not exist: deletion is a
hard delete via ``ON DELETE CASCADE`` (spec §1 deletion semantics).
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  media_path TEXT,
  media_sha256 TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  location_text TEXT,
  fields_json TEXT NOT NULL,
  buyer_ref TEXT,
  payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK(payment_status IN ('unpaid','paid','comped')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','analyzing','verdict_ready','complete','failed','spend_capped','rerun_requested')),
  verdict TEXT CHECK(verdict IN ('no_mundane_match','mundane_identified','insufficient_data') OR verdict IS NULL),
  verdict_text TEXT,
  uncertainty REAL,
  uncertainty_calibrated INTEGER NOT NULL DEFAULT 0,
  uncertainty_reason TEXT,
  calibration_source TEXT,
  fiction_ready INTEGER NOT NULL DEFAULT 0,
  quality_score REAL,
  quality_gate_pass INTEGER,
  lineage_count INTEGER,
  agreement_fraction REAL,
  report_path TEXT,
  report_sha256 TEXT,
  report_json_sha256 TEXT,
  fiction_path TEXT,
  fiction_sha256 TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  run_version INTEGER NOT NULL DEFAULT 1,
  current_stage TEXT,
  error_detail TEXT
);

CREATE TABLE IF NOT EXISTS battery_results (
  case_id TEXT NOT NULL,
  category TEXT NOT NULL,
  run_version INTEGER NOT NULL,
  result TEXT NOT NULL CHECK(result IN ('positive','negative','insufficient')),
  evidence_citation TEXT,
  source_stamp_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY (case_id, category, run_version),
  FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS lineage_outputs (
  case_id TEXT NOT NULL,
  lineage_id TEXT NOT NULL,
  run_version INTEGER NOT NULL,
  hypothesis TEXT,
  classification TEXT,
  artifact_detected INTEGER,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY (case_id, lineage_id, run_version),
  FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pipeline_stage_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
  output_json TEXT,
  artifact_hash TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  error_detail TEXT,
  run_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(case_id, run_version, stage),
  FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT,
  actor TEXT NOT NULL CHECK(actor IN ('system','operator')),
  action TEXT NOT NULL,
  detail_json TEXT,
  prev_hash TEXT NOT NULL,
  entry_hash TEXT NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_runs (
  run_id TEXT PRIMARY KEY,
  prereg_hash TEXT NOT NULL,
  validation_set_id TEXT NOT NULL,
  validation_set_hash TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  per_category_recall_json TEXT,
  false_no_mundane_match_rate REAL,
  insufficient_detection_rate REAL,
  passed INTEGER,
  mode TEXT NOT NULL CHECK(mode IN ('live','mock'))
);

CREATE TABLE IF NOT EXISTS spend_entries (
  entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT,
  cost_usd REAL NOT NULL,
  description TEXT,
  recorded_at TEXT NOT NULL,
  FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  csrf_token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rigor_runs (
  rigor_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL,
  run_version INTEGER NOT NULL,
  runtime TEXT NOT NULL CHECK(runtime IN ('live','mock')),
  agreement_fraction REAL NOT NULL,
  population_variance REAL NOT NULL,
  plurality TEXT,
  plurality_count INTEGER NOT NULL,
  conditions_json TEXT NOT NULL,
  refutation_json TEXT NOT NULL,
  passed INTEGER NOT NULL,
  reason TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE(case_id, run_version),
  FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS case_coverage (
  case_id TEXT NOT NULL,
  run_version INTEGER NOT NULL,
  expected_categories_json TEXT NOT NULL,
  tested_categories_json TEXT NOT NULL,
  insufficient_categories_json TEXT NOT NULL,
  complete INTEGER NOT NULL,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY(case_id, run_version),
  FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS legal_holds (
  case_id TEXT PRIMARY KEY,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  released_at TEXT,
  FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS purge_runs (
  purge_run_id TEXT PRIMARY KEY,
  case_id TEXT,
  status TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tle_catalogs (
  catalog_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  substantive_hash TEXT,
  path TEXT,
  status TEXT NOT NULL,
  detail_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_created ON cases(created_at);
CREATE INDEX IF NOT EXISTS idx_stage_runs_case ON pipeline_stage_runs(case_id, run_version);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_id);
CREATE INDEX IF NOT EXISTS idx_spend_recorded ON spend_entries(recorded_at);
CREATE INDEX IF NOT EXISTS idx_tle_fetched ON tle_catalogs(fetched_at);

-- Existing MVP databases may have been created before the NOT NULL clauses
-- above were added.  SQLite cannot add that constraint in place, so these
-- idempotent triggers enforce the same invariant after an upgrade.
CREATE TRIGGER IF NOT EXISTS battery_results_case_id_not_null
BEFORE INSERT ON battery_results WHEN NEW.case_id IS NULL
BEGIN SELECT RAISE(ABORT, 'battery_results.case_id must not be NULL'); END;

CREATE TRIGGER IF NOT EXISTS lineage_outputs_case_id_not_null
BEFORE INSERT ON lineage_outputs WHEN NEW.case_id IS NULL
BEGIN SELECT RAISE(ABORT, 'lineage_outputs.case_id must not be NULL'); END;

CREATE TRIGGER IF NOT EXISTS pipeline_stage_runs_case_id_not_null
BEFORE INSERT ON pipeline_stage_runs WHEN NEW.case_id IS NULL
BEGIN SELECT RAISE(ABORT, 'pipeline_stage_runs.case_id must not be NULL'); END;
"""


def connect(db_path: Optional[Path] = None, timeout: float = 10.0) -> sqlite3.Connection:
    """Open a connection with the spec-mandated PRAGMAs (spec §5)."""
    if db_path is None:
        from uapvf.config import get_settings

        db_path = get_settings().db_path
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Operator config templates shipped inside the package (spec §6 lists them
# among the files that constitute the runnable product). A wheel install has
# no repo var/ tree, so `db init` / server start materializes any missing
# template from the packaged copy — without this, every case on a clean
# install would die at the battery stage with "battery_config.yaml empty or
# missing" (spec §7 smallest deploy, AC-013).
DEFAULT_CONFIG_FILES = (
    "battery_config.yaml",
    "quality_config.json",
    "fiction_prompt_template.txt",
    "fiction_constraints.json",
    "mock_fixtures.json",
    "evidence_vocabulary.json",
    "refutation_rules.json",
    "rigor_config.json",
    "reference_manifest.json",
)


ADDITIVE_CASE_COLUMNS = {
    "media_kind": "TEXT",
    "lineage_runtime": "TEXT",
    "coverage_complete": "INTEGER",
    "sandbox_verified": "INTEGER",
    # Round 6: ``delivery_version`` was durable residue of the removed
    # buyer-delivery surface (spec.md:92 OUT / DIS-6). Nothing reads or
    # writes it; new installs no longer create it. Databases that already
    # carry the inert column keep it (SQLite ADD COLUMN is one-way) —
    # harmless default-0 dead weight, never surfaced.
}

ADDITIVE_LINEAGE_COLUMNS = {
    "status": "TEXT",
    "evidence_claims_json": "TEXT",
    "confidence": "REAL",
    "provenance_json": "TEXT",
    "abstention_reason": "TEXT",
}


def _additive_upgrade(conn: sqlite3.Connection) -> None:
    """Idempotently add columns that SQLite cannot express with
    ``CREATE TABLE IF NOT EXISTS`` for databases created by the MVP."""
    for table, columns in (
        ("cases", ADDITIVE_CASE_COLUMNS),
        ("lineage_outputs", ADDITIVE_LINEAGE_COLUMNS),
    ):
        present = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for name, declaration in columns.items():
            if name not in present:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                )
    conn.commit()


def seed_default_configs(var_dir: Path) -> list:
    """Copy packaged default config files into ``var_dir`` when absent.

    Returns the list of file names actually written. Never raises: if the
    packaged copies are unavailable (pruned install), the normal load-time
    errors remain in place and surface loudly at the battery stage.
    Existing operator files are never overwritten.
    """
    copied: list = []
    try:
        from importlib import resources

        defaults_root = resources.files("uapvf") / "defaults"
    except Exception:
        return copied
    for name in DEFAULT_CONFIG_FILES:
        dst = Path(var_dir) / name
        if dst.exists():
            continue
        tmp = dst.with_name(dst.name + ".seed-tmp")
        try:
            # Atomic (temp + rename): a concurrent config loader must never
            # observe a half-written template.
            tmp.write_bytes((defaults_root / name).read_bytes())
            os.replace(tmp, dst)
            copied.append(name)
        except Exception:
            with contextlib.suppress(Exception):
                if tmp.exists():
                    tmp.unlink()
            continue
    return copied


def init_db(var_dir: Path) -> Path:
    """Create the var directory tree and the full schema. Idempotent."""
    var_dir = Path(var_dir)
    var_dir.mkdir(parents=True, exist_ok=True)
    (var_dir / "cases").mkdir(exist_ok=True)
    (var_dir / "benchmark").mkdir(exist_ok=True)
    (var_dir / "backup").mkdir(exist_ok=True)
    seed_default_configs(var_dir)
    db_path = var_dir / "uapvf.db"
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _additive_upgrade(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


def get_case(conn: sqlite3.Connection, case_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM cases WHERE case_id = ?", (case_id,)
    ).fetchone()


def update_case(conn: sqlite3.Connection, case_id: str, **fields) -> int:
    """Update case columns; returns affected row count."""
    if not fields:
        return 0
    from uapvf.config import utcnow_iso

    fields = dict(fields)
    fields["updated_at"] = utcnow_iso()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(
        f"UPDATE cases SET {assignments} WHERE case_id = ?",
        (*fields.values(), case_id),
    )
    conn.commit()
    return cur.rowcount
