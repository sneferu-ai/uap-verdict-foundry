"""DORMANT Phase 2 controlling-source tooling (round-5 blocker fix).

The product's controlling documents (classification taxonomy sources,
report formatting rules, label contracts) are frozen references listed in
``var/reference_manifest.json``. ``resolve`` reports whether every entry
is locally present with its declared SHA-256; ``fetch`` pulls them from
the operator-configured source base.

ROUND-5 CONTRACT: this module does NOT gate the MVP. An earlier revision
wired a strict-by-default ``require_for_mutation`` gate around case
intake; that was unspec'd phase-2 machinery and 503'd every submission
on a spec-conformant clean install (spec §7/J1: only
``UAPV_OPERATOR_TOKEN`` + ``SNEFERU_MOCK=1``, first submission must
succeed). The enforcement primitive was removed outright. What remains:

* CLI operator tooling: ``uapvf references status`` / ``references fetch``.
* Informational projections only: the report's ``phase2.references``
  block (pipeline.py) and the ``uapvf_reference_integrity`` metric
  (server.py). Neither blocks intake, processing, or readiness.

``UAPV_REFERENCE_SOURCE_BASE`` has NO default: ``fetch`` fails closed
with a configuration error unless the operator points it at a source.

Modes (``UAPV_REFERENCE_MODE``) now only label ``resolve`` output —
there is no enforcement path left for either mode to drive:
  strict   — unverified references are reported as failures.
  degraded — same report; historically the non-blocking label.

The manifest is never rewritten by this module; verification state is
derived from the local ``var/references/`` copies on every call, so a
tampered copy fails the next resolve even if it once verified.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

MANIFEST_NAME = "reference_manifest.json"
REFERENCES_DIR_NAME = "references"

_MODES = ("strict", "degraded")
DEFAULT_MODE = "strict"


class ReferenceGateError(Exception):
    """Reference tooling failure (missing manifest/config, fetch error).

    Informational only since round 5 — never raised on any intake,
    processing, or readiness path."""


def _manifest_path(settings) -> Path:
    return settings.var_dir / MANIFEST_NAME


def _references_dir(settings) -> Path:
    return settings.var_dir / REFERENCES_DIR_NAME


def _load_manifest(settings) -> Optional[dict]:
    path = _manifest_path(settings)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_entry(settings, entry: dict) -> dict:
    name = str(entry.get("name") or "")
    local = _references_dir(settings) / name
    declared_sha = entry.get("sha256") or _attested_sha(settings, name)
    if not local.is_file():
        return {"name": name, "verified": False,
                "reason": "local copy missing"}
    actual_sha = _sha256_file(local)
    if declared_sha:
        if actual_sha != declared_sha:
            return {"name": name, "verified": False,
                    "reason": "sha256 mismatch",
                    "sha256_actual": actual_sha}
        return {"name": name, "verified": True, "sha256": actual_sha,
                "reason": None}
    # No declared or attested hash: a copy exists but cannot be vouched.
    return {"name": name, "verified": False,
            "reason": "no declared sha256 in frozen manifest",
            "sha256_actual": actual_sha}


def _attested_sha(settings, name: str):
    """First-pull hash attestation recorded by ``fetch`` (the manifest
    itself stays frozen)."""
    state_path = _references_dir(settings) / "attestations.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    entry = state.get(name) or {}
    return entry.get("sha256")


def resolve(settings) -> dict:
    """Current integrity status of every frozen reference. Never raises
    for missing manifest/copies — those are reported as failures."""
    mode = str(getattr(settings, "UAPV_REFERENCE_MODE", DEFAULT_MODE)
               or DEFAULT_MODE).strip().lower()
    if mode not in _MODES:
        mode = DEFAULT_MODE
    manifest = _load_manifest(settings)
    if manifest is None:
        return {
            "valid": False,
            "mode": mode,
            "manifest_present": False,
            "references": [],
            "failures": ["reference_manifest_missing"],
        }
    entries = manifest.get("references") or []
    if not isinstance(entries, list) or not entries:
        return {
            "valid": False,
            "mode": mode,
            "manifest_present": True,
            "references": [],
            "failures": ["reference_manifest_empty"],
        }
    results = [_verify_entry(settings, entry) for entry in entries]
    failures = [
        f"{r['name']}: {r['reason']}" for r in results if not r["verified"]
    ]
    return {
        "valid": not failures,
        "mode": mode,
        "manifest_present": True,
        "references": results,
        "failures": failures,
    }


def fetch(settings) -> dict:
    """Pull every frozen reference from the configured source base and
    store verified local copies. Network failures are reported per entry,
    never silently ignored. Raises ``ReferenceGateError`` when the source
    base itself is unusable."""
    import httpx

    manifest = _load_manifest(settings)
    if manifest is None:
        raise ReferenceGateError("reference manifest missing")
    base = str(getattr(settings, "UAPV_REFERENCE_SOURCE_BASE", "") or "").rstrip("/")
    if not base:
        raise ReferenceGateError("UAPV_REFERENCE_SOURCE_BASE is not configured")
    entries = manifest.get("references") or []
    if not entries:
        raise ReferenceGateError("reference manifest has no entries")

    refs_dir = _references_dir(settings)
    refs_dir.mkdir(parents=True, exist_ok=True)
    results = []
    fetched = verified = 0
    for entry in entries:
        name = str(entry.get("name") or "")
        source_path = str(entry.get("source_path") or name)
        url = f"{base}/{source_path.lstrip('/')}"
        target = refs_dir / name
        try:
            response = httpx.get(url, timeout=15, follow_redirects=True)
            response.raise_for_status()
            payload = response.content
        except Exception as exc:
            results.append({"name": name, "fetched": False, "verified": False,
                            "reason": f"fetch failed: {type(exc).__name__}"})
            continue
        fetched += 1
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(payload)
        tmp.replace(target)
        declared_sha = entry.get("sha256")
        actual_sha = _sha256_file(target)
        if declared_sha and declared_sha != actual_sha:
            results.append({"name": name, "fetched": True, "verified": False,
                            "reason": "sha256 mismatch after fetch"})
            continue
        if not declared_sha:
            # First verified pull pins the hash into the local state file.
            _pin_hash(settings, name, actual_sha)
        results.append({"name": name, "fetched": True, "verified": True,
                        "sha256": actual_sha})
        verified += 1
    return {
        "fetched": fetched,
        "verified": verified,
        "total": len(entries),
        "source_base": base,
        "results": results,
    }


def _pin_hash(settings, name: str, sha256: str) -> None:
    """Record a first-pull hash attestation (manifest stays frozen)."""
    state_path = _references_dir(settings) / "attestations.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    state[name] = {"sha256": sha256}
    state_path.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n",
                          encoding="utf-8")
