"""Operator-supplied CNN model weight attestation (spec §5 model weights).

The four operator artifacts (ONNX weights, taxonomy mapping, install
receipt, trusted public key) are verified before any ResNet50 inference:
the receipt's declared hashes must match the actual files, and the
receipt's Ed25519 signature must verify under the operator's trusted
public key. Missing or unverified artifacts fail closed (``unavailable``)
— an untrusted model is never substituted.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path
from typing import Optional

_MODELS_DIR_NAME = "models"
_WEIGHTS_NAME = "resnet50.onnx"
_MANIFEST_NAME = "resnet50_manifest.json"


class ModelArtifactError(Exception):
    """Model artifact missing, malformed, or unverifiable."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _require_file(value, label: str) -> Path:
    if not value:
        raise ModelArtifactError(f"{label} path is not configured")
    path = Path(str(value)).expanduser()
    if not path.is_file():
        raise ModelArtifactError(f"{label} file missing: {path}")
    return path


def _canonical_receipt_body(receipt: dict) -> bytes:
    body = {k: v for k, v in receipt.items()
            if k not in ("signature", "signature_algorithm")}
    return json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _public_key_bytes(value):
    text = str(value).strip()
    if not text:
        raise ModelArtifactError("trusted public key is not configured")
    raw = None
    for decoder in (base64.b64decode, lambda s: bytes.fromhex(s)):
        try:
            candidate = decoder(text)
        except Exception:
            continue
        if len(candidate) == 32:
            raw = candidate
            break
    if raw is None:
        raise ModelArtifactError(
            "trusted public key is not 32-byte Ed25519 (base64 or hex)")
    return raw


def verify_resnet50(weights=None, mapping=None, receipt=None,
                    trusted_public_key=None, receipt_path=None) -> dict:
    """Verify weights+mapping hashes against the signed receipt. Raises
    ``ModelArtifactError`` for missing/malformed artifacts; returns a
    status dict whose ``verified`` flag is the only authority."""
    weights_path = _require_file(weights, "weights")
    mapping_path = _require_file(mapping, "mapping")
    receipt_file = _require_file(receipt or receipt_path, "receipt")
    try:
        receipt_doc = json.loads(receipt_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModelArtifactError(
            f"receipt unparseable: {type(exc).__name__}") from exc
    if not isinstance(receipt_doc, dict):
        raise ModelArtifactError("receipt must be a JSON object")

    weights_sha = _sha256_file(weights_path)
    mapping_sha = _sha256_file(mapping_path)
    fingerprint = None
    try:
        pub_bytes = _public_key_bytes(trusted_public_key)
        fingerprint = hashlib.sha256(pub_bytes).hexdigest()[:16]
    except ModelArtifactError:
        pub_bytes = None

    declared_weights = receipt_doc.get("weights_sha256")
    declared_mapping = receipt_doc.get("mapping_sha256")
    signature = receipt_doc.get("signature")

    result = {
        "verified": False,
        "model_id": receipt_doc.get("model_id"),
        "weights_sha256": weights_sha,
        "mapping_sha256": mapping_sha,
        "operator_public_key_fingerprint": fingerprint,
        "reason": None,
    }
    if declared_weights and declared_weights != weights_sha:
        result["reason"] = "weights sha256 does not match receipt"
        return result
    if declared_mapping and declared_mapping != mapping_sha:
        result["reason"] = "mapping sha256 does not match receipt"
        return result
    if not declared_weights or not declared_mapping:
        result["reason"] = "receipt missing declared content hashes"
        return result
    if not signature:
        result["reason"] = "receipt is unsigned"
        return result
    if pub_bytes is None:
        result["reason"] = "trusted public key unavailable"
        return result
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except Exception:
        result["reason"] = "ed25519 support unavailable"
        return result
    try:
        key = Ed25519PublicKey.from_public_bytes(pub_bytes)
    except Exception:
        result["reason"] = "trusted public key is not valid Ed25519"
        return result
    try:
        signature_bytes = base64.b64decode(str(signature))
    except Exception:
        try:
            signature_bytes = bytes.fromhex(str(signature))
        except Exception:
            result["reason"] = "receipt signature is not base64 or hex"
            return result
    try:
        key.verify(signature_bytes, _canonical_receipt_body(receipt_doc))
    except InvalidSignature:
        result["reason"] = "receipt signature failed verification"
        return result
    except Exception:
        result["reason"] = "signature verification error"
        return result
    result["verified"] = True
    return result


def install_resnet50(file_path, settings) -> dict:
    """Install a pre-downloaded ONNX weight file into the var model
    directory and record an UNVERIFIED manifest (verification still
    requires the operator receipt + trusted key). Raises
    ``ModelArtifactError`` for unusable input."""
    source = _require_file(file_path, "model file")
    if source.stat().st_size <= 0:
        raise ModelArtifactError("model file is empty")
    models_dir = settings.var_dir / _MODELS_DIR_NAME
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / _WEIGHTS_NAME
    tmp = target.with_suffix(".onnx.tmp")
    shutil.copyfile(source, tmp)
    tmp.replace(target)
    sha = _sha256_file(target)
    manifest = {
        "filename": _WEIGHTS_NAME,
        "sha256": sha,
        "size_bytes": target.stat().st_size,
        "installed_at": _utcnow(),
        "status": "installed_unverified",
        "note": ("verification requires UAPV_RESNET50_RECEIPT and "
                 "UAPV_RESNET50_TRUSTED_PUBLIC_KEY"),
    }
    manifest_path = models_dir / _MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2)
                             + "\n", encoding="utf-8")
    return {"installed": str(target), "sha256": sha,
            "status": "installed_unverified"}


def _utcnow() -> str:
    from uapvf.config import utcnow_iso

    return utcnow_iso()
