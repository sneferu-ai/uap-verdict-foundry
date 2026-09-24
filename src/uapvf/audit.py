"""Append-only hash-chained audit log (spec §5, FR-015).

Chain law:
    entry_hash = sha256(case_id || "||" || actor || "||" || action || "||" ||
                        canonical_detail_json || "||" || prev_hash || "||" ||
                        recorded_at)
The first event of a fresh database uses prev_hash = "GENESIS" (literal),
so `audit verify --all` can bootstrap on a clean install. `case_id` hashes
as the empty string when NULL. ``audit_events`` intentionally has no foreign
key on case_id: audit entries survive hard case deletion (spec §1).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import List, Optional

GENESIS = "GENESIS"


def canonical_detail(detail) -> str:
    """Canonical JSON: sorted keys, no whitespace, UTF-8. None -> ''."""
    if detail is None:
        return ""
    return json.dumps(detail, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(
    case_id: str,
    actor: str,
    action: str,
    detail_json: str,
    prev_hash: str,
    recorded_at: str,
) -> str:
    payload = "||".join(
        [case_id or "", actor, action, detail_json or "", prev_hash, recorded_at]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_event(
    conn: sqlite3.Connection,
    case_id: Optional[str],
    actor: str,
    action: str,
    detail=None,
    recorded_at: Optional[str] = None,
) -> str:
    """Append one event to the chain. Returns the new entry_hash.

    Serialized with BEGIN IMMEDIATE so concurrent writers cannot fork the
    chain. Actor must be 'system' or 'operator' (schema CHECK)."""
    from uapvf.config import utcnow_iso

    if actor not in ("system", "operator"):
        raise ValueError(f"invalid audit actor: {actor!r}")
    recorded_at = recorded_at or utcnow_iso()
    detail_json = canonical_detail(detail)
    # ``append_event`` is used both as a standalone write and as part of
    # larger state transitions.  Starting a second transaction on a
    # connection that already owns one raises ``cannot start a transaction
    # within a transaction`` and, worse, a blanket rollback would discard
    # the caller's unrelated work.  A savepoint composes safely with an
    # existing transaction; the outer caller remains responsible for its
    # eventual commit/rollback.
    nested = conn.in_transaction
    savepoint = f"uapvf_audit_{uuid.uuid4().hex}"
    if nested:
        conn.execute(f"SAVEPOINT {savepoint}")
    else:
        conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT entry_hash FROM audit_events ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        prev_hash = row["entry_hash"] if row else GENESIS
        entry_hash = compute_entry_hash(
            case_id or "", actor, action, detail_json, prev_hash, recorded_at
        )
        conn.execute(
            "INSERT INTO audit_events "
            "(case_id, actor, action, detail_json, prev_hash, entry_hash, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, actor, action, detail_json, prev_hash, entry_hash, recorded_at),
        )
        if nested:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            conn.commit()
    except Exception:
        if nested:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            conn.rollback()
        raise
    return entry_hash


def verify_chain(
    conn: sqlite3.Connection, case_id: Optional[str] = None
) -> dict:
    """Verify the global hash chain (and, optionally, one case's entries).

    Returns {ok, entries_checked, problems:[{event_id, kind, detail}],
    fork_points:[]}. A fork point is reported where prev_hash does not equal
    the preceding entry's hash (e.g. after a restore that re-links the chain).
    """
    rows = conn.execute(
        "SELECT * FROM audit_events ORDER BY event_id ASC"
    ).fetchall()
    problems: List[dict] = []
    fork_points: List[dict] = []
    prev_hash = GENESIS
    checked = 0
    case_set = None
    for r in rows:
        expect = compute_entry_hash(
            r["case_id"] or "",
            r["actor"],
            r["action"],
            r["detail_json"] or "",
            r["prev_hash"],
            r["recorded_at"],
        )
        relevant = case_id is None or r["case_id"] == case_id
        if r["entry_hash"] != expect and relevant:
            problems.append(
                {
                    "event_id": r["event_id"],
                    "kind": "hash_mismatch",
                    "detail": "stored entry_hash does not match recomputed hash",
                }
            )
        if r["prev_hash"] != prev_hash:
            fork_points.append(
                {
                    "event_id": r["event_id"],
                    "kind": "chain_break",
                    "detail": "prev_hash does not match preceding entry_hash",
                }
            )
            if relevant:
                problems.append(
                    {
                        "event_id": r["event_id"],
                        "kind": "chain_break",
                        "detail": "prev_hash does not match preceding entry_hash",
                    }
                )
        prev_hash = r["entry_hash"]
        if relevant:
            checked += 1
    return {
        "ok": not problems,
        "entries_checked": checked,
        "total_entries": len(rows),
        "problems": problems,
        "fork_points": fork_points,
    }


def events_for_case(conn: sqlite3.Connection, case_id: str) -> List[dict]:
    rows = conn.execute(
        "SELECT * FROM audit_events WHERE case_id = ? ORDER BY event_id ASC",
        (case_id,),
    ).fetchall()
    out = []
    for r in rows:
        detail = None
        if r["detail_json"]:
            try:
                detail = json.loads(r["detail_json"])
            except Exception:
                detail = r["detail_json"]
        out.append(
            {
                "event_id": r["event_id"],
                "case_id": r["case_id"],
                "actor": r["actor"],
                "action": r["action"],
                "detail": detail,
                "prev_hash": r["prev_hash"],
                "entry_hash": r["entry_hash"],
                "recorded_at": r["recorded_at"],
            }
        )
    return out
