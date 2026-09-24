"""Manual case deletion + legal holds (spec §5 retention, FR-021
hard-delete semantics, S11).

Case data is retained until the operator manually deletes it. ``purge_case``
is the hard-delete primitive behind ``uapvf case delete`` / S11 (row cascade
+ filesystem + backup scrub + durable restore proof). Legal holds block
purges; audit entries survive deletion by design (no FK on
audit_events.case_id).

Automatic retention enforcement (TTL deletion job) is deferred to phase 2
(spec §1 deferred table) — spec §5: ``RETENTION_DAYS`` is a manual reminder
threshold displayed as an S2 warning badge only; **no auto-deletion**.
There is therefore no retention scheduler, no TTL configuration file, and
no TTL application path in the MVP.
"""
from __future__ import annotations

import json
import shutil
import tarfile
import uuid
from pathlib import Path
from typing import Optional

PROOF_DIR_NAME = "retention"
_BACKUP_DIR_NAME = "backup"


def _active_hold(conn, case_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM legal_holds WHERE case_id = ? AND released_at IS NULL",
        (case_id,),
    ).fetchone()
    return row is not None


def set_legal_hold(conn, case_id: str, reason: Optional[str]) -> None:
    from uapvf import audit
    from uapvf.config import utcnow_iso

    text = str(reason or "").strip()
    if not text:
        raise ValueError("legal hold requires a reason")
    conn.execute(
        "INSERT INTO legal_holds (case_id, reason, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT(case_id) DO UPDATE SET reason = excluded.reason,"
        " created_at = excluded.created_at, released_at = NULL",
        (case_id, text, utcnow_iso()),
    )
    conn.commit()
    audit.append_event(conn, case_id, "operator", "legal_hold_set",
                       {"reason": text})


def release_legal_hold(conn, case_id: str) -> None:
    from uapvf import audit
    from uapvf.config import utcnow_iso

    conn.execute(
        "UPDATE legal_holds SET released_at = ? WHERE case_id = ?"
        " AND released_at IS NULL",
        (utcnow_iso(), case_id),
    )
    conn.commit()
    audit.append_event(conn, case_id, "operator", "legal_hold_released", {})


def purge_case(conn, settings, case_id: str,
               include_backups: bool = True) -> dict:
    """Hard-delete one case: DB cascade, case directory, backup scrub, and
    a durable restore proof. Returns {"purged", "status", "restore_proof",
    ...}. Legal holds are honored (status "held")."""
    from uapvf import audit
    from uapvf.config import utcnow_iso

    row = conn.execute("SELECT case_id FROM cases WHERE case_id = ?",
                       (case_id,)).fetchone()
    if row is None:
        return {"purged": False, "status": "not_found",
                "restore_proof": None}
    if _active_hold(conn, case_id):
        return {"purged": False, "status": "held", "restore_proof": None}

    purge_run_id = f"purge-{uuid.uuid4().hex[:12]}"
    started = utcnow_iso()
    conn.execute(
        "INSERT INTO purge_runs (purge_run_id, case_id, status, detail_json,"
        " started_at) VALUES (?, ?, 'running', '{}', ?)",
        (purge_run_id, case_id, started),
    )
    conn.commit()

    case_dir = settings.cases_dir / case_id
    removed_files = []
    if case_dir.is_dir():
        for path in sorted(case_dir.rglob("*")):
            if path.is_file():
                removed_files.append(str(path.relative_to(case_dir)))
        shutil.rmtree(case_dir, ignore_errors=True)
    files_removed = not case_dir.exists()

    backups_scrubbed = 0
    backup_failures = []
    if include_backups:
        backups_scrubbed, backup_failures = _scrub_backups(
            settings, case_id)

    # Row deletion LAST: the restore proof records what existed before.
    conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
    row_gone = conn.execute(
        "SELECT 1 FROM cases WHERE case_id = ?", (case_id,)).fetchone() is None
    conn.commit()

    proof = {
        "purge_run_id": purge_run_id,
        "case_id": case_id,
        "purged_at": utcnow_iso(),
        "files_removed": removed_files,
        "case_dir_removed": files_removed,
        "db_row_removed": row_gone,
        "backups_scrubbed": backups_scrubbed,
        "backup_failures": backup_failures,
        "audit_retained": True,
    }
    if not (files_removed and row_gone and not backup_failures):
        conn.execute(
            "UPDATE purge_runs SET status = 'failed', detail_json = ?,"
            " completed_at = ? WHERE purge_run_id = ?",
            (json.dumps(proof, sort_keys=True), utcnow_iso(), purge_run_id),
        )
        conn.commit()
        return {"purged": False, "status": "verification_failed",
                "restore_proof": None, "detail": proof}

    proof_dir = settings.var_dir / PROOF_DIR_NAME
    proof_dir.mkdir(parents=True, exist_ok=True)
    proof_path = proof_dir / f"purge_proof_{case_id}.json"
    tmp = proof_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(proof, sort_keys=True, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(proof_path)

    conn.execute(
        "UPDATE purge_runs SET status = 'complete', detail_json = ?,"
        " completed_at = ? WHERE purge_run_id = ?",
        (json.dumps(proof, sort_keys=True), utcnow_iso(), purge_run_id),
    )
    conn.commit()
    audit.append_event(conn, case_id, "system", "case_purged",
                       {"purge_run_id": purge_run_id,
                        "files_removed": len(removed_files),
                        "backups_scrubbed": backups_scrubbed})
    return {"purged": True, "status": "purged",
            "restore_proof": str(proof_path),
            "files_removed": len(removed_files),
            "backups_scrubbed": backups_scrubbed}


def _scrub_backups(settings, case_id: str):
    """Rewrite every backup tarball without the purged case's directory.
    Returns (scrubbed_count, failures)."""
    failures = []
    scrubbed = 0
    backup_dir = settings.var_dir / _BACKUP_DIR_NAME
    if not backup_dir.is_dir():
        return 0, []
    prefix = f"cases/{case_id}/"
    for tarball in sorted(backup_dir.glob("*.tar.gz")):
        try:
            tmp_path = tarball.with_suffix(tarball.suffix + ".scrub.tmp")
            removed_any = False
            with tarfile.open(tarball, "r:gz") as src, \
                    tarfile.open(tmp_path, "w:gz") as dst:
                for member in src.getmembers():
                    name = member.name.lstrip("./")
                    if name.startswith(prefix) or name == f"cases/{case_id}":
                        removed_any = True
                        continue
                    if member.isfile():
                        handle = src.extractfile(member)
                        if handle is not None:
                            dst.addfile(member, handle)
                    else:
                        dst.addfile(member)
            if removed_any:
                tmp_path.replace(tarball)
                scrubbed += 1
            else:
                tmp_path.unlink(missing_ok=True)
        except Exception as exc:
            failures.append(f"{tarball.name}: {type(exc).__name__}")
    return scrubbed, failures
