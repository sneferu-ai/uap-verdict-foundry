"""Operator-collected evaluation set recording (spec §5 calibration /
independence eval sets).

`record_operator_eval` installs an operator collection manifest into the
var directory where the calibration and independence gates read it. The
manifest is validated structurally before installation; a malformed
operator set fails closed (``OperatorEvalError``) and never silently
falls back to synthetic sets.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

_KIND_DIRS = {
    "independence": "operator/eval_set",
    "calibration": "operator/calibration_eval_set",
}
_MANIFEST_NAME = "manifest.json"


class OperatorEvalError(Exception):
    """Operator evaluation manifest missing or invalid."""


def _eval_dir(settings, kind: str) -> Path:
    return settings.var_dir / Path(_KIND_DIRS[kind])


def record_operator_eval(manifest, settings) -> dict:
    """Validate and install an operator eval-set manifest. Returns the
    recorded location and verification facts."""
    source = Path(str(manifest)).expanduser()
    if not source.is_file():
        raise OperatorEvalError(f"manifest not found: {source}")
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise OperatorEvalError(f"manifest unreadable: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise OperatorEvalError(
            f"manifest unparseable: {type(exc).__name__}") from exc
    if not isinstance(document, dict):
        raise OperatorEvalError("manifest root must be a JSON object")
    kind = document.get("eval_set")
    if kind not in _KIND_DIRS:
        raise OperatorEvalError(
            f"manifest eval_set must be one of "
            f"{sorted(_KIND_DIRS)}, got {kind!r}")
    items = document.get("items")
    if not isinstance(items, list) or not items:
        raise OperatorEvalError('manifest must contain a non-empty "items" list')
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise OperatorEvalError(f"item {position} is not an object")

    sha256 = hashlib.sha256(raw).hexdigest()
    target_dir = _eval_dir(settings, kind)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _MANIFEST_NAME
    tmp = target.with_suffix(".json.tmp")
    shutil.copyfile(source, tmp)
    tmp.replace(target)
    return {
        "recorded": str(target),
        "eval_set": kind,
        "items": len(items),
        "sha256": sha256,
        "marker": None,
        "fallback": False,
    }
