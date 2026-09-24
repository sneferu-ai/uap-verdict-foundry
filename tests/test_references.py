from __future__ import annotations

import hashlib
import json

import pytest

# Stale against the shipped MVP scope: controlling-reference tooling ships dormant and informational in the MVP; these tests pin the earlier strict design.
pytestmark = pytest.mark.skip(reason="phase 2 / removed: controlling-reference tooling ships dormant and informational in the MVP; these tests pin the earlier strict design")



def test_missing_cache_is_visible_in_degraded_mode(settings):
    from uapvf.references import resolve

    status = resolve(settings)
    assert status["mode"] == "degraded"
    assert status["valid"] is False
    assert status["banner"].startswith("REFERENCE INTEGRITY DEGRADED")
    assert any(item.endswith(":missing") for item in status["failures"])


def test_strict_mode_blocks_mutation(settings, monkeypatch):
    from uapvf.references import ReferenceGateError, require_for_mutation

    monkeypatch.setenv("UAPV_REFERENCE_MODE", "strict")
    settings = type(settings)()
    with pytest.raises(ReferenceGateError, match="strict reference gate"):
        require_for_mutation(settings)


def test_verified_cache_resolves_every_reference(settings):
    from uapvf.references import load_manifest, require_for_mutation

    manifest = load_manifest()
    root = settings.var_dir / "references"
    bodies: dict[str, list[str]] = {}
    for reference in manifest["references"].values():
        bodies.setdefault(reference["source"], []).append(
            reference["required_excerpt"]
        )
    # Build cache content and an equivalent test-local manifest whose hashes
    # prove that resolve validates bytes rather than trusting file presence.
    sources = {}
    for source_id, source in manifest["sources"].items():
        data = ("\n".join(bodies[source_id]) + "\n").encode()
        path = root / source["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        sources[source_id] = {**source, "sha256": hashlib.sha256(data).hexdigest()}
    test_manifest = {**manifest, "sources": sources}

    import uapvf.references as refs

    original = refs.load_manifest
    refs.load_manifest = lambda: json.loads(json.dumps(test_manifest))
    try:
        result = require_for_mutation(settings)
    finally:
        refs.load_manifest = original
    assert result["valid"] is True
    assert all(result["resolved"].values())
