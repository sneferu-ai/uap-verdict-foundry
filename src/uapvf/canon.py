"""Optional, non-evidence canon projection for recurring story continuity.

Canon is disabled by default and never feeds lineages, battery adapters,
rigor, uncertainty, or verdict synthesis. Activation only enables projection;
deactivation preserves previously projected nodes and edges.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from uapvf.config import Settings, utcnow_iso


NODE_TYPES = ("planet", "technology", "culture", "visual_motif")


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _config_path(settings: Settings) -> Path:
    return settings.var_dir / "canon_config.json"


def is_enabled(settings: Settings) -> bool:
    try:
        persisted = json.loads(_config_path(settings).read_text(encoding="utf-8"))
        return bool(persisted.get("enabled"))
    except Exception:
        return bool(int(settings.UAPV_CANON_ENABLED or 0))


def set_enabled(settings: Settings, enabled: bool) -> dict:
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({
        "schema_version": 1,
        "enabled": bool(enabled),
        "evidence_eligible": False,
        "updated_at": utcnow_iso(),
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return {"enabled": bool(enabled), "path": str(path),
            "preserves_existing_data": True}


def status(conn, settings: Settings) -> dict:
    return {
        "enabled": is_enabled(settings),
        "evidence_eligible": False,
        "node_count": conn.execute(
            "SELECT COUNT(*) AS n FROM canon_nodes").fetchone()["n"],
        "edge_count": conn.execute(
            "SELECT COUNT(*) AS n FROM canon_edges").fetchone()["n"],
        "node_types": list(NODE_TYPES),
    }


def _stage(output: dict, name: str) -> dict:
    for item in output.get("stages") or []:
        if item.get("stage") == name:
            return item.get("output") or {}
    return {}


def project_case(conn, settings: Settings, case_id: str,
                 require_enabled: bool = True) -> dict:
    if require_enabled and not is_enabled(settings):
        return {"case_id": case_id, "projected": False, "reason": "canon_disabled"}
    case = conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if case is None:
        raise ValueError("case not found")
    xeno = conn.execute(
        "SELECT * FROM xenoscience_runs WHERE case_id=? ORDER BY run_version DESC LIMIT 1",
        (case_id,)).fetchone()
    if xeno is None:
        return {"case_id": case_id, "projected": False,
                "reason": "no_speculative_research"}
    output = json.loads(xeno["output_json"])
    planet = _stage(output, "planetary_context")
    kinematics = _stage(output, "kinematic_hypotheses")
    materials = _stage(output, "materials_constraints")
    control = _stage(output, "control_hypotheses")
    asset_rows = conn.execute(
        "SELECT * FROM story_assets WHERE case_id=? AND run_version=? ORDER BY created_at",
        (case_id, xeno["run_version"])).fetchall()
    definitions = [
        ("planet", str(planet.get("world_seed") or f"world-{case_id[:8]}"), planet),
        ("technology", str(kinematics.get("candidate") or "unresolved technology"), {
            "kinematics": kinematics, "materials": materials, "control": control}),
        ("culture", f"archive-{case_id[:8]}", {
            "role": "case-derived fictional archive culture",
            "boundary": output.get("label"), "evidence_eligible": False}),
    ]
    for asset in asset_rows:
        definitions.append(("visual_motif", f"story-{asset['asset_id'][:8]}", {
            "sha256": asset["sha256"], "alt_text": asset["alt_text"],
            "similarity": json.loads(asset["similarity_json"]),
            "label_verified": bool(asset["label_verified"])}))
    nodes = []
    for node_type, name, data in definitions:
        payload = {"schema_version": 1, "node_type": node_type, "name": name,
                   "data": data, "evidence_eligible": False}
        digest = _hash(payload)
        node_id = f"{node_type}:{digest[:24]}"
        # Same named concept with different content is retained and explicitly
        # connected as a contradiction instead of overwriting history.
        conflicts = conn.execute(
            "SELECT * FROM canon_nodes WHERE node_type=? AND name=? AND content_hash<>?",
            (node_type, name, digest)).fetchall()
        conn.execute(
            "INSERT OR IGNORE INTO canon_nodes "
            "(node_id, case_id, node_type, name, content_hash, data_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (node_id, case_id, node_type, name, digest, _canonical(payload), utcnow_iso()))
        nodes.append(node_id)
        for conflict in conflicts:
            edge_payload = {"reason": "same canon identity has divergent content",
                            "evidence_eligible": False}
            edge_id = "edge:" + _hash([conflict["node_id"], node_id,
                                        "contradicts"])[:24]
            conn.execute(
                "INSERT OR IGNORE INTO canon_edges "
                "(edge_id, source_node_id, target_node_id, relation, contradiction,"
                " data_json, created_at) VALUES (?, ?, ?, 'contradicts', 1, ?, ?)",
                (edge_id, conflict["node_id"], node_id,
                 _canonical(edge_payload), utcnow_iso()))
    # Connect the four concept families within this case.
    if nodes:
        for target in nodes[1:]:
            edge_id = "edge:" + _hash([nodes[0], target, "coexists_with"])[:24]
            conn.execute(
                "INSERT OR IGNORE INTO canon_edges "
                "(edge_id, source_node_id, target_node_id, relation, contradiction,"
                " data_json, created_at) VALUES (?, ?, ?, 'coexists_with', 0, ?, ?)",
                (edge_id, nodes[0], target,
                 _canonical({"case_id": case_id, "evidence_eligible": False}),
                 utcnow_iso()))
    conn.commit()
    return {"case_id": case_id, "projected": True, "nodes": nodes,
            "evidence_eligible": False}


def rebuild(conn, settings: Settings) -> dict:
    rows = conn.execute(
        "SELECT DISTINCT case_id FROM xenoscience_runs ORDER BY case_id").fetchall()
    results = [project_case(conn, settings, row["case_id"], require_enabled=False)
               for row in rows]
    return {"ok": True, "cases": results, **status(conn, settings)}
