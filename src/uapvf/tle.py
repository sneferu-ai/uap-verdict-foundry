"""TLE/OMM catalog persistence + staleness (FR-006 satellites category).

Automatic catalog refresh is deferred to phase 2 (spec §1 deferred table;
A-012): in the MVP the operator refreshes the catalog manually —
``uapvf tle refresh`` performs ONE operator-initiated fetch of
``TLE_CATALOG_URL`` via ``refresh_catalog``. There is no background
scheduler. Case analysis never refreshes on demand: it consumes only the
newest persisted catalog row (``latest_catalog``), and staleness is judged
by consumers against ``UAPV_TLE_MAX_AGE_DAYS`` (stale/absent catalog →
honest ``insufficient``, A-012).

Accepted payload formats: classic three-line TLE text and current OMM JSON
(lists of objects carrying either TLE line pairs or OMM element sets).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

_TLE_DIR_NAME = "tle"
_CATALOG_BASENAME = "catalog"


def _tle_dir(settings) -> Path:
    path = settings.var_dir / _TLE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_catalog(conn, settings) -> Optional[dict]:
    """Newest successfully fetched catalog row, or None."""
    row = conn.execute(
        "SELECT * FROM tle_catalogs WHERE status = 'ok'"
        " ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    try:
        detail = json.loads(row["detail_json"] or "{}")
    except Exception:
        detail = {}
    return {
        "path": row["path"],
        "fetched_at": row["fetched_at"],
        "source_url": row["source_url"],
        "format": detail.get("fmt"),
        "sha256": detail.get("sha256"),
        "substantive_hash": row["substantive_hash"],
        "catalog_number_hash": detail.get("catalog_number_hash"),
        "status": row["status"],
    }


def refresh_catalog(conn, settings) -> Optional[dict]:
    """Fetch, parse, persist, and record the catalog. Never raises for
    unreachable sources — records a failed row and returns None."""
    import httpx

    from uapvf.config import utcnow_iso

    url = str(getattr(settings, "TLE_CATALOG_URL", "") or "")
    if not url:
        return None
    fetched_at = utcnow_iso()

    def record_failed(reason: str) -> None:
        conn.execute(
            "INSERT INTO tle_catalogs (fetched_at, source_url, path,"
            " substantive_hash, status, detail_json)"
            " VALUES (?, ?, NULL, NULL, 'failed', ?)",
            (fetched_at, url, json.dumps({"reason": reason})),
        )
        conn.commit()

    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        payload = response.content
    except Exception as exc:
        record_failed(f"fetch error: {type(exc).__name__}")
        return None
    try:
        fmt, records = parse_catalog(payload)
    except Exception as exc:
        record_failed(f"unparseable catalog: {type(exc).__name__}")
        return None
    if not records:
        record_failed("catalog contained no records")
        return None

    sha = hashlib.sha256(payload).hexdigest()
    substantive = hashlib.sha256(
        json.dumps(_substantive(records), sort_keys=True).encode("utf-8")
    ).hexdigest()
    numbers = sorted(str(r.get("catalog_number") or r.get("name") or "")
                     for r in records)
    catalog_number_hash = hashlib.sha256(
        "|".join(numbers).encode("utf-8")).hexdigest()

    stamp = fetched_at.replace(":", "-").replace("+", "Z")
    suffix = ".json" if fmt == "omm_json" else ".txt"
    path = _tle_dir(settings) / f"{_CATALOG_BASENAME}_{stamp}{suffix}"
    tmp = path.with_suffix(suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)

    conn.execute(
        "INSERT INTO tle_catalogs (fetched_at, source_url, path,"
        " substantive_hash, status, detail_json)"
        " VALUES (?, ?, ?, ?, 'ok', ?)",
        (fetched_at, url, str(path), substantive,
         json.dumps({"fmt": fmt, "sha256": sha,
                     "catalog_number_hash": catalog_number_hash,
                     "record_count": len(records)}, sort_keys=True)),
    )
    conn.commit()
    return latest_catalog(conn, settings)


def _substantive(records: List[dict]) -> list:
    """Order-independent substantive content: sorted (number, line1, line2)
    triples or OMM element sets."""
    out = []
    for record in records:
        out.append({
            "n": record.get("catalog_number") or record.get("name"),
            "l1": record.get("line1"),
            "l2": record.get("line2"),
            "omm": record.get("omm"),
        })
    return sorted(out, key=lambda r: str(r.get("n")))


def parse_catalog(payload: bytes) -> Tuple[str, List[dict]]:
    """Detect and parse TLE text or OMM JSON payloads."""
    text = payload.decode("utf-8", "replace").strip()
    if text.startswith("[") or text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            data = data.get("omm") or data.get("records") or [data]
        if isinstance(data, list) and data:
            records = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                records.append(_normalize_omm(item))
            if records:
                return "omm_json", records
    return "tle", _parse_tle(text)


def _normalize_omm(item: dict) -> dict:
    line1 = item.get("TLE_LINE1") or item.get("line1") or item.get("LINE1")
    line2 = item.get("TLE_LINE2") or item.get("line2") or item.get("LINE2")
    number = (item.get("OBJECT_ID") or item.get("NORAD_CAT_ID")
              or item.get("OBJECT_NUMBER") or item.get("name"))
    return {
        "name": str(item.get("OBJECT_NAME") or item.get("name") or number or ""),
        "catalog_number": str(number or ""),
        "line1": str(line1 or ""),
        "line2": str(line2 or ""),
        "omm": {k: v for k, v in item.items()
                if k not in ("TLE_LINE1", "TLE_LINE2", "line1", "line2")},
    }


_TLE_NAME_RE = re.compile(r"^[A-Za-z0-9 \-_.()+/]{1,80}$")


def _parse_tle(text: str) -> List[dict]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    records: List[dict] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("1 ") and i + 1 < len(lines) \
                and lines[i + 1].startswith("2 "):
            line1, line2 = lines[i], lines[i + 1]
            try:
                number = line1[2:7].strip()
            except Exception:
                number = ""
            records.append({"name": "", "catalog_number": number,
                            "line1": line1, "line2": line2})
            i += 2
            continue
        # A name line precedes a TLE pair when the next two lines match.
        if i + 2 < len(lines) and lines[i + 1].startswith("1 ") \
                and lines[i + 2].startswith("2 ") \
                and _TLE_NAME_RE.match(lines[i]):
            name = lines[i]
            line1, line2 = lines[i + 1], lines[i + 2]
            try:
                number = line1[2:7].strip()
            except Exception:
                number = ""
            records.append({"name": name, "catalog_number": number,
                            "line1": line1, "line2": line2})
            i += 3
            continue
        i += 1
    return records


def load_catalog(path) -> Tuple[str, List[dict]]:
    """Read and parse a persisted catalog file. Raises for missing or
    unparseable files — callers convert that into an honest insufficient."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"catalog missing: {target}")
    payload = target.read_bytes()
    fmt, records = parse_catalog(payload)
    if not records:
        raise ValueError("catalog contained no records")
    return fmt, records
