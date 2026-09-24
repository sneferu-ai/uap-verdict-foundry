from __future__ import annotations

import pytest

from uapvf.adapters.sneferu_adapter import (
    AdapterSchemaError,
    run_xenoscience,
    sdk_reachable,
)
from uapvf.xenoscience import (
    LABEL,
    STAGES,
    build_multi_model_xenoscience,
    build_xenoscience,
    validate_boundary,
)


def _engine_payload():
    return {
        "engine_run_id": "engine-run-1",
        "model_runs": [
            {"model_id": "research-a", "run_id": "r1", "provider": "p1"},
            {"model_id": "research-b", "run_id": "r2", "provider": "p2"},
        ],
        "stages": [
            {
                "stage": name,
                "output": {"statement": f"bounded output for {name}"},
                "contributing_model_ids": ["research-a", "research-b"],
            }
            for name in STAGES
        ],
        "consistency_judgment": {
            "judge_model_id": "judge-c",
            "score": 0.82,
            "conflicts": [],
            "adjudications": [],
        },
    }


def test_deterministic_fallback_is_explicitly_descoped():
    output = build_xenoscience("case-1", "insufficient_data", {}, [], {})
    assert output["label"] == LABEL
    assert output["single_model_descope"] is True
    assert output["evidence_eligible"] is False
    assert validate_boundary(output) == []


def test_multi_model_wrapper_preserves_boundary():
    output = build_multi_model_xenoscience("case-1", _engine_payload())
    assert output["engine"] == "sneferu-multi-model"
    assert output["single_model_descope"] is False
    assert len(output["model_runs"]) == 2
    assert validate_boundary(output) == []


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _judgment(body, lineages=None, **overrides):
    payload = {
        "agreed": True,
        "verdict": "affirmed",
        "claim_id": body["claim_id"],
        "lineages_judged": lineages or ["research-a", "research-b"],
        "per_lineage": {
            "research-a": {"verdict": "affirmed"},
            "research-b": {"verdict": "affirmed"},
        },
        "rationale": "The claim remains bounded and internally consistent.",
        "degraded": False,
        "is_mock": False,
    }
    payload.update(overrides)
    return payload


def test_adapter_uses_public_judge_contract(monkeypatch, settings):
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return _Response(_judgment(json))

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    result = run_xenoscience(
        "case-1", "insufficient_data", {}, [], {}, settings,
    )
    assert len(calls) == 7
    assert all(url.endswith("/sdk/judge") for url, _, _ in calls)
    assert all(body["mock"] is False for _, body, _ in calls)
    assert all(body["require_agreement"] == "majority" for _, body, _ in calls)
    assert all("roles" not in body for _, body, _ in calls)
    assert len(result["model_runs"]) == 2
    assert result["consistency_judgment"]["score"] == 1.0
    assert result["consistency_judgment"]["conflicts"] == []


def test_adapter_preserves_judgment_disagreement(monkeypatch, settings):
    def post(url, json, timeout):
        disagree = json["claim_id"].endswith("energy_constraints")
        return _Response(_judgment(
            json,
            agreed=not disagree,
            verdict="conflicted" if disagree else "affirmed",
        ))

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    result = run_xenoscience(
        "case-1", "insufficient_data", {}, [], {}, settings,
    )
    assert result["consistency_judgment"]["score"] == pytest.approx(6 / 7)
    assert result["consistency_judgment"]["conflicts"][0]["stage"] == "energy_constraints"


def test_adapter_rejects_single_judging_lineage(monkeypatch, settings):
    def post(url, json, timeout):
        payload = _judgment(json, lineages=["research-a"])
        payload["per_lineage"] = {"research-a": {"verdict": "affirmed"}}
        return _Response(payload)

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(AdapterSchemaError, match="at least two distinct lineages"):
        run_xenoscience("case-1", "insufficient_data", {}, [], {}, settings)


@pytest.mark.parametrize("field,value,match", [
    ("is_mock", True, "mock or unlabelled"),
    ("degraded", True, "degraded"),
])
def test_adapter_rejects_nonproduction_judgment(
        monkeypatch, settings, field, value, match):
    def post(url, json, timeout):
        return _Response(_judgment(json, **{field: value}))

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(AdapterSchemaError, match=match):
        run_xenoscience("case-1", "insufficient_data", {}, [], {}, settings)


def test_adapter_rejects_missing_per_lineage_trace(monkeypatch, settings):
    def post(url, json, timeout):
        return _Response(_judgment(json, per_lineage=None))

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(AdapterSchemaError, match="per-lineage traces"):
        run_xenoscience("case-1", "insufficient_data", {}, [], {}, settings)


def test_adapter_rejects_non_object_response(monkeypatch, settings):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    import httpx
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())
    with pytest.raises(AdapterSchemaError, match="must be an object"):
        run_xenoscience("case-1", "insufficient_data", {}, [], {}, settings)


def test_sdk_readiness_probes_public_judge_contract(monkeypatch, settings):
    settings = settings.model_copy(update={"SNEFERU_MOCK": 0})
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"agreed": True, "is_mock": True}

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    assert sdk_reachable(settings) is True
    assert calls[0][0].endswith("/sdk/judge")
    assert calls[0][1]["mock"] is True
    assert calls[0][1]["claim_id"] == "uapvf-readiness-probe"


def test_sdk_readiness_rejects_non_contract_response(monkeypatch, settings):
    settings = settings.model_copy(update={"SNEFERU_MOCK": 0})
    class Response:
        status_code = 200

        def json(self):
            return {"status": "ok"}

    import httpx
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Response())
    assert sdk_reachable(settings) is False



def test_judge_request_body_matches_live_contract(monkeypatch, settings):
    """Every field sent to /sdk/judge has the type the live engine's
    JudgeRequest declares. context is a string: an object gets HTTP 422 from
    the engine (observed against a real Sneferu server, checkout 07174496)."""
    import json as _json

    bodies = []

    def post(url, json, timeout):
        bodies.append(json)
        return _Response(_judgment(json))

    import httpx
    monkeypatch.setattr(httpx, "post", post)
    run_xenoscience("case-1", "insufficient_data", {}, [], {}, settings)
    assert len(bodies) == 7
    for body in bodies:
        assert isinstance(body["claim"], str)
        assert isinstance(body["context"], str)
        assert _json.loads(body["context"])["case_id"] == "case-1"
        assert isinstance(body["claim_id"], str)

    try:
        from claudopus import JudgeRequest
    except ImportError:
        return  # the SDK ships with Sneferu, not on PyPI; the type checks above still ran
    for body in bodies:
        JudgeRequest.model_validate(body)
