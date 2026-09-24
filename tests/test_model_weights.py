from __future__ import annotations

import hashlib

import pytest

from uapvf import model_weights

# Stale against the shipped MVP scope: local lineage implementations and weight certification deferred to phase 2; the engine drives lineage analysis (FR-005).
pytestmark = pytest.mark.skip(reason="phase 2 / removed: local lineage implementations and weight certification deferred to phase 2; the engine drives lineage analysis (FR-005)")



def test_signed_model_receipt_detects_mapping_tamper(tmp_path, settings,
                                                     monkeypatch):
    source = tmp_path / "model.onnx"
    source.write_bytes(b"test-model-weights")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(model_weights, "OFFICIAL_SHA256", digest)
    settings.UAPV_SIGNING_PASSPHRASE = "test-model-signing-passphrase"
    installed = model_weights.install_resnet50(source, settings)
    verified = model_weights.verify_resnet50(
        weights=installed["weights"], mapping=installed["mapping"],
        receipt_path=installed["receipt"],
        trusted_public_key=installed["trusted_public_key"],
    )
    assert verified["verified"] is True
    with open(installed["mapping"], "a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(model_weights.ModelArtifactError,
                       match="mapping does not match"):
        model_weights.verify_resnet50(
            weights=installed["weights"], mapping=installed["mapping"],
            receipt_path=installed["receipt"],
            trusted_public_key=installed["trusted_public_key"],
        )


def test_signed_model_receipt_requires_pinned_operator_key(tmp_path, settings,
                                                           monkeypatch):
    source = tmp_path / "model.onnx"
    source.write_bytes(b"test-model-weights")
    monkeypatch.setattr(
        model_weights, "OFFICIAL_SHA256",
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    settings.UAPV_SIGNING_PASSPHRASE = "test-model-signing-passphrase"
    installed = model_weights.install_resnet50(source, settings)
    with pytest.raises(model_weights.ModelArtifactError,
                       match="trusted operator key"):
        model_weights.verify_resnet50(
            weights=installed["weights"], mapping=installed["mapping"],
            receipt_path=installed["receipt"], trusted_public_key="wrong-key",
        )
