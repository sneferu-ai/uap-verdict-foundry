"""Sneferu orchestration-engine adapter (FR-005, FR-011, FR-018).

Two call paths:
  run_lineages(...)      — vision lineage analysis (skipped entirely for
                           quality-rejected media; FR-005).
  generate_fiction(...)  — single-model narrative-seed text generation for
                           unresolved cases (allowed even for
                           quality-rejected cases; FR-011 / J3 / OBL-25).

Mock mode (SNEFERU_MOCK=1) returns deterministic fixtures and logs every
call to var/mock_call_log.jsonl with a call_id (AC-007/AC-024 evidence).
Mock hooks (all mock-mode only):
  UAPV_MOCK_ZERO_LINEAGES=1    -> zero lineages (no call logged)
  UAPV_MOCK_DRONE=1            -> one lineage classifies "drone" (AC-033)
  UAPV_MOCK_ARTIFACT=1         -> all lineages detect a lens artifact
  UAPV_MOCK_CLASSIFICATION=<x> -> override all lineage classifications
  UAPV_MOCK_FICTION_INVALID=1  -> first attempt invalid, retry valid
  UAPV_MOCK_FICTION_FAIL=1     -> both attempts invalid (fallback formatter)
  UAPV_MOCK_FICTION_UNFIXABLE=1-> every attempt including fallback invalid

Both live paths submit to the Sneferu orchestration engine: vision lineage
analysis to ``/sdk/uapvf/lineages`` (FR-005, the engine drives at least
two vision lineages) and fiction text generation to ``/sdk/uapvf/fiction``
(FR-011, a single-model call returning ``{"paragraphs": [...],
"call_id": "..."}``). Per A-013 the engine's existence/conformance is
unverified, so both live paths validate the response schema strictly and
fail loud (never degrade to synthetic lineage outputs; a failed fiction
call engages the FR-011 retry-once ladder and only then the deterministic
fallback formatter).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import List, Optional

from uapvf.config import Settings, get_settings, utcnow_iso

CLASSIFICATION_ENUM = [
    "aircraft", "satellite", "astronomical_object", "lens_artifact",
    "sensor_artifact", "drone", "bird", "weather_phenomenon", "unknown",
    "unidentifiable",
]

# Engine wire contract for vision lineage submission (FR-005). The adapter
# owns this contract on the product side; A-013 carries the assumption that
# the engine implements it (live mode requires engine verification before
# deployment).
LINEAGE_ENGINE_PATH = "/sdk/uapvf/lineages"
LINEAGE_ENGINE_TIMEOUT_S = 300.0

# Engine wire contract for fiction text generation (FR-011): a
# single-model call returning {"paragraphs": [...], "call_id": "..."}.
FICTION_ENGINE_PATH = "/sdk/uapvf/fiction"
FICTION_ENGINE_TIMEOUT_S = 120.0

# FR-011 generation parameters (defaults when the operator-editable
# var/fiction_constraints.json is absent/unreadable).
FICTION_DEFAULT_MAX_TOKENS = 2000
FICTION_DEFAULT_TEMPERATURE = 0.7

DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "defaults"

MOCK_HYPOTHESIS = (
    "No identifiable mundane object was determined from the available "
    "visual evidence under this lineage's model."
)


class AdapterError(Exception):
    """Base adapter failure."""


class AdapterTimeout(AdapterError):
    """Transient: the engine did not answer in time."""


class AdapterSchemaError(AdapterError):
    """Unrecoverable: the engine answered with an invalid schema."""


def _mock_call_log_path(settings: Settings):
    return settings.var_dir / "mock_call_log.jsonl"


def log_mock_call(settings: Settings, kind: str, case_id: str, call_id: str,
                  detail: Optional[dict] = None) -> None:
    if not settings.mock_mode:
        return
    entry = {
        "ts": utcnow_iso(),
        "kind": kind,
        "case_id": case_id,
        "call_id": call_id,
        "detail": detail or {},
    }
    try:
        settings.var_dir.mkdir(parents=True, exist_ok=True)
        with open(_mock_call_log_path(settings), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:
        pass


def _mock_lineages(case_id: str, settings: Settings) -> List[dict]:
    if os.environ.get("UAPV_MOCK_ZERO_LINEAGES") == "1":
        return []
    classification_override = os.environ.get("UAPV_MOCK_CLASSIFICATION", "").strip()
    drone = os.environ.get("UAPV_MOCK_DRONE") == "1"
    artifact = os.environ.get("UAPV_MOCK_ARTIFACT") == "1"
    lineages = []
    for i in range(1, 4):
        if artifact:
            classification, detected = "lens_artifact", True
        elif classification_override and classification_override in CLASSIFICATION_ENUM:
            classification, detected = classification_override, False
        else:
            classification, detected = "unknown", False
        if drone and i == 3:
            classification, detected = "drone", False
        lineages.append(
            {
                "lineage_id": f"lineage-{i}",
                "classification": classification,
                "artifact_detected": detected,
                "hypothesis": MOCK_HYPOTHESIS,
                "model_version": "mock-vision-v1",
                "status": "ok",
                "provenance": {
                    "fixture": True,
                    "taxonomy_coverage": [
                        "drone", "bird", "insect", "meteor", "balloon",
                        "sensor_artifact",
                    ],
                },
            }
        )
    call_id = str(uuid.uuid4())
    log_mock_call(settings, "lineage", case_id, call_id,
                  {"lineages": len(lineages)})
    return lineages


def _fail_closed_runtime_guard(settings: Settings) -> None:
    """Fail-closed guard for the removed ``simulation`` runtime.

    Accessing :attr:`Settings.lineage_runtime` raises
    :class:`LineageRuntimeRemovedError` when a removed runtime is still
    configured. This must happen BEFORE any spend path so a stale label can
    never silently route to the paid live engine.
    """
    # Intentionally side-effect-only: the property raises for removed values.
    _ = settings.lineage_runtime


def run_lineages(case_id: str, media_path, metadata: dict,
                 settings: Optional[Settings] = None) -> List[dict]:
    """Run vision lineage analysis through the Sneferu orchestration
    engine (FR-005).

    Mock mode returns deterministic fixtures; every other mode submits the
    normalized media + field dictionary to the engine and validates the
    response strictly against the FR-005 lineage schema. Failures surface
    as ``AdapterTimeout`` (transient — engine unreachable/5xx/timeout) or
    ``AdapterSchemaError`` (unrecoverable — engine rejected the submission
    or answered with an invalid schema). The adapter never fabricates
    lineage outputs.
    """
    settings = settings or get_settings()
    _fail_closed_runtime_guard(settings)
    if settings.mock_mode:
        return _mock_lineages(case_id, settings)
    return _engine_lineages(case_id, media_path, metadata, settings)


def _engine_lineages(case_id: str, media_path, metadata: dict,
                     settings: Settings) -> List[dict]:
    """Submit case media + fields to the engine's lineage endpoint."""
    import httpx

    path = Path(str(media_path))
    if not path.is_file():
        raise AdapterSchemaError(
            f"normalized media missing for engine submission: {path.name}")
    media = path.read_bytes()
    body = {
        "case_id": case_id,
        "media_base64": base64.b64encode(media).decode("ascii"),
        "media_sha256": hashlib.sha256(media).hexdigest(),
        "fields": metadata,
    }
    url = settings.SNEFERU_SDK_URL.rstrip("/") + LINEAGE_ENGINE_PATH
    try:
        response = httpx.post(url, json=body, timeout=LINEAGE_ENGINE_TIMEOUT_S)
    except httpx.TimeoutException as exc:
        raise AdapterTimeout(
            f"engine lineage analysis timed out after "
            f"{LINEAGE_ENGINE_TIMEOUT_S:.0f}s: {type(exc).__name__}") from exc
    except httpx.HTTPError as exc:
        # Transport failure (engine down/unreachable): transient — the
        # pipeline retries, then fails the case with this reason.
        raise AdapterTimeout(
            f"engine lineage submission failed: "
            f"{type(exc).__name__}: {exc}") from exc
    if response.status_code >= 500:
        raise AdapterTimeout(
            f"engine lineage analysis errored: HTTP {response.status_code}")
    if response.status_code >= 400:
        raise AdapterSchemaError(
            f"engine rejected lineage submission: HTTP {response.status_code}")
    try:
        payload = response.json()
    except Exception as exc:
        raise AdapterSchemaError(
            f"engine lineage response was not JSON: "
            f"{type(exc).__name__}") from exc
    return _validate_engine_lineages(payload)


def _validate_engine_lineages(payload) -> List[dict]:
    """Validate an engine lineage response against the FR-005 contract.

    Accepts ``{"lineages": [...]}`` or a bare list. Every lineage must
    carry a classification inside ``CLASSIFICATION_ENUM``; anything else
    is an unrecoverable schema error (never coerced, never invented)."""
    items = payload.get("lineages") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise AdapterSchemaError(
            "engine lineage response must be a list of lineage outputs")
    lineages: List[dict] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise AdapterSchemaError(
                f"lineage #{index} is not an object")
        classification = item.get("classification")
        if classification not in CLASSIFICATION_ENUM:
            raise AdapterSchemaError(
                f"lineage #{index} classification {classification!r} is "
                f"outside the FR-005 enum")
        lineages.append({
            "lineage_id": str(item.get("lineage_id")
                              or f"engine-lineage-{index}"),
            "classification": classification,
            "artifact_detected": bool(item.get("artifact_detected")),
            "hypothesis": str(item.get("hypothesis") or ""),
            "model_version": str(item.get("model_version") or "engine"),
            "status": str(item.get("status") or "ok"),
            "evidence_claims": item.get("evidence_claims") or {},
            "confidence": item.get("confidence"),
            "provenance": item.get("provenance") or {"engine": True},
            "abstention_reason": item.get("abstention_reason"),
        })
    return lineages


def _fallback_paragraphs(metadata: dict) -> List[str]:
    location = metadata.get("location_text") or "the recorded observation site"
    observed = metadata.get("observed_at") or "the recorded time"
    shape = metadata.get("shape") or "an unresolved shape"
    return [
        (
            f"Above {location} at {observed}, witnesses traced {shape} "
            "against the darkening sky, and every ordinary explanation they "
            "could name slipped away one by one like heat from the evening."
        ),
        (
            "The field notes describe a slow, deliberate drift that no wind "
            "in the valley could account for, and the watchers found "
            "themselves whispering, as though the night itself were leaning "
            "in to listen to their guesses."
        ),
        (
            "What stayed with them afterward was not fear but a stubborn, "
            "quiet wonder — the sense that the sky had briefly shown them a "
            "question no one on the ground was equipped to answer that night."
        ),
    ]


def _fiction_prompt_template(settings: Settings) -> str:
    """The operator-editable fiction prompt (FR-011): the seeded
    ``var/fiction_prompt_template.txt`` wins; the packaged default is the
    fallback. Both carry the ``{case_metadata}`` placeholder and the hard
    constraint 'do not assert extraterrestrial origin or non-human
    intelligence'."""
    for path in (settings.var_dir / "fiction_prompt_template.txt",
                 DEFAULTS_DIR / "fiction_prompt_template.txt"):
        try:
            text = Path(path).read_text(encoding="utf-8")
        except Exception:
            continue
        if text.strip():
            return text
    raise AdapterSchemaError(
        "fiction prompt template missing (var/fiction_prompt_template.txt "
        "and the packaged default are both unreadable)")


def _fiction_generation_params(settings: Settings) -> tuple:
    """FR-011 generation parameters from ``fiction_constraints.json``
    (var/ wins over the packaged default): max 2000 tokens, temperature
    0.7. Malformed values fall back to the spec defaults per key — never
    an invented number."""
    max_tokens = FICTION_DEFAULT_MAX_TOKENS
    temperature = FICTION_DEFAULT_TEMPERATURE
    constraints = {}
    for path in (settings.var_dir / "fiction_constraints.json",
                 DEFAULTS_DIR / "fiction_constraints.json"):
        try:
            constraints = json.loads(Path(path).read_text(encoding="utf-8"))
            break
        except Exception:
            continue
    if isinstance(constraints, dict):
        try:
            candidate = int(constraints.get("max_tokens"))
            if candidate > 0:
                max_tokens = candidate
        except (TypeError, ValueError):
            pass
        try:
            candidate_t = float(constraints.get("temperature"))
            if 0.0 <= candidate_t <= 2.0:
                temperature = candidate_t
        except (TypeError, ValueError):
            pass
    return max_tokens, temperature


def _engine_fiction(case_id: str, metadata: dict, settings: Settings) -> dict:
    """Submit the FR-011 single-model text-generation call to the engine.

    The prompt is the operator-editable template with the case metadata
    substituted; the engine must answer with exactly
    ``{"paragraphs": [...], "call_id": "..."}``. Failures surface as
    ``AdapterTimeout`` (transient — engine unreachable/5xx/timeout) or
    ``AdapterSchemaError`` (unrecoverable — engine rejected the submission
    or answered with an invalid schema); the pipeline's FR-011 ladder
    retries once and then demotes to the deterministic fallback formatter.
    The adapter never fabricates narrative text in live mode.
    """
    import httpx

    prompt = _fiction_prompt_template(settings)
    prompt = prompt.replace(
        "{case_metadata}",
        json.dumps(metadata, sort_keys=True, ensure_ascii=False, indent=1),
    )
    max_tokens, temperature = _fiction_generation_params(settings)
    body = {
        "case_id": case_id,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    url = settings.SNEFERU_SDK_URL.rstrip("/") + FICTION_ENGINE_PATH
    try:
        response = httpx.post(url, json=body, timeout=FICTION_ENGINE_TIMEOUT_S)
    except httpx.TimeoutException as exc:
        raise AdapterTimeout(
            f"engine fiction generation timed out after "
            f"{FICTION_ENGINE_TIMEOUT_S:.0f}s: {type(exc).__name__}") from exc
    except httpx.HTTPError as exc:
        # Transport failure (engine down/unreachable): transient — the
        # pipeline retries once, then falls back (FR-011).
        raise AdapterTimeout(
            f"engine fiction submission failed: "
            f"{type(exc).__name__}: {exc}") from exc
    if response.status_code >= 500:
        raise AdapterTimeout(
            f"engine fiction generation errored: HTTP {response.status_code}")
    if response.status_code >= 400:
        raise AdapterSchemaError(
            f"engine rejected fiction submission: HTTP {response.status_code}")
    try:
        payload = response.json()
    except Exception as exc:
        raise AdapterSchemaError(
            f"engine fiction response was not JSON: "
            f"{type(exc).__name__}") from exc
    return _validate_engine_fiction(payload)


def _validate_engine_fiction(payload) -> dict:
    """Validate an engine fiction response against the FR-011 contract:
    a JSON object ``{"paragraphs": [...], "call_id": "..."}`` with at
    least one non-empty prose paragraph. Anything else is an
    unrecoverable schema error (never coerced, never invented)."""
    if not isinstance(payload, dict):
        raise AdapterSchemaError(
            "engine fiction response must be a JSON object")
    paragraphs = payload.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs:
        raise AdapterSchemaError(
            "engine fiction response must carry a non-empty "
            "'paragraphs' list")
    cleaned = [str(p).strip() for p in paragraphs if str(p).strip()]
    if not cleaned:
        raise AdapterSchemaError(
            "engine fiction response paragraphs are all empty")
    call_id = payload.get("call_id")
    if not isinstance(call_id, str) or not call_id.strip():
        raise AdapterSchemaError(
            "engine fiction response must carry a 'call_id' string")
    return {
        "paragraphs": cleaned,
        "call_id": call_id.strip(),
        "source": "engine",
    }


def generate_fiction(case_id: str, metadata: dict,
                     settings: Optional[Settings] = None,
                     strict: bool = False) -> dict:
    """Return a narrative seed via the FR-011 adapter contract
    ``{"paragraphs": [...], "call_id": "..."}``.

    Mock mode returns deterministic fixtures (distinct from the fallback
    text, so AC-007 can tell them apart). Live mode submits the
    single-model text-generation call to the Sneferu engine
    (``/sdk/uapvf/fiction``); a failed call engages the FR-011 retry-once
    ladder in the pipeline, and only a fully failed ladder reaches the
    deterministic fallback formatter. Live mode never returns local
    template prose as if it were a model output.
    """
    settings = settings or get_settings()
    # Same fail-closed guard as run_lineages: the removed ``simulation``
    # runtime must raise a named error before any spend path.
    _fail_closed_runtime_guard(settings)
    if settings.mock_mode:
        call_id = str(uuid.uuid4())
        fail = os.environ.get("UAPV_MOCK_FICTION_FAIL") == "1"
        invalid_first = os.environ.get("UAPV_MOCK_FICTION_INVALID") == "1"
        unfixable = os.environ.get("UAPV_MOCK_FICTION_UNFIXABLE") == "1"
        if unfixable:
            log_mock_call(settings, "fiction", case_id, call_id,
                          {"attempt": "invalid", "strict": strict})
            raise AdapterSchemaError("mock: unfixable fiction payload")
        if fail or (invalid_first and not strict):
            log_mock_call(settings, "fiction", case_id, call_id,
                          {"attempt": "invalid", "strict": strict})
            raise AdapterSchemaError("mock: invalid fiction payload")
        paragraphs = _fallback_paragraphs(metadata)
        # Substantive generated narrative (distinct from any fallback text).
        location = metadata.get("location_text") or "the recorded location"
        observed = metadata.get("observed_at") or "the recorded capture time"
        shape = metadata.get("shape") or "a formation of lights"
        paragraphs = [
            (
                f"Over {location} at {observed}, {shape} moved in a long, "
                "patient arc that the watchers later swore had been drawn by "
                "a hand thinking in centuries rather than seconds."
            ),
            (
                "Nobody spoke until the last glow folded itself behind the "
                "ridge; then the youngest of them laughed, not from humor "
                "but from the sheer weight of having seen something no "
                "checklist on Earth had been written to hold."
            ),
            (
                "In the weeks that followed, the story they told each other "
                "kept changing shape the way true stories do — growing "
                "quieter, stranger, and harder to put down, until the night "
                "itself seemed to keep a copy."
            ),
        ]
        log_mock_call(settings, "fiction", case_id, call_id,
                      {"attempt": "ok", "strict": strict,
                       "paragraphs": len(paragraphs)})
        return {"paragraphs": paragraphs, "call_id": call_id}
    return _engine_fiction(case_id, metadata, settings)


def fallback_fiction(metadata: dict) -> List[str]:
    """Deterministic minimal seed from case metadata (FR-011 fallback)."""
    settings = get_settings()
    if settings.mock_mode and os.environ.get("UAPV_MOCK_FICTION_UNFIXABLE") == "1":
        # Mock hook: even the deterministic fallback is unusable, exercising
        # the cleared-after-validation-failure path (FR-012).
        return ["short"]
    return _fallback_paragraphs(metadata)


def _validate_judgment(payload, stage: str) -> dict:
    """Validate one production ``/sdk/judge`` response.

    Xenoscience is outside the forensic pipeline, but its multi-model label
    still has to be honest: a mock, degraded, unlabelled, or single-lineage
    response must never be wrapped as a production multi-model result.
    """
    if not isinstance(payload, dict):
        raise AdapterSchemaError("judge response must be an object")
    if payload.get("is_mock") is not False:
        raise AdapterSchemaError("judge response is mock or unlabelled")
    if payload.get("degraded") is not False:
        raise AdapterSchemaError("judge response is degraded or unlabelled")
    if not isinstance(payload.get("agreed"), bool):
        raise AdapterSchemaError("judge response must carry boolean agreed")

    lineage_ids = payload.get("lineages_judged")
    if not isinstance(lineage_ids, list):
        raise AdapterSchemaError(
            "judge response must name at least two distinct lineages")
    distinct = []
    for lineage_id in lineage_ids:
        if isinstance(lineage_id, str) and lineage_id.strip() \
                and lineage_id.strip() not in distinct:
            distinct.append(lineage_id.strip())
    if len(distinct) < 2:
        raise AdapterSchemaError(
            "judge response must name at least two distinct lineages")

    traces = payload.get("per_lineage")
    if not isinstance(traces, dict) or any(
            lineage_id not in traces or not isinstance(traces[lineage_id], dict)
            for lineage_id in distinct):
        raise AdapterSchemaError(
            "judge response must carry per-lineage traces")

    claim_id = payload.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id.endswith(stage):
        raise AdapterSchemaError("judge response claim_id does not match stage")
    return {**payload, "lineages_judged": distinct}


def run_xenoscience(case_id: str, verdict: str, metadata: dict,
                    lineage_outputs: list, rigor_result=None,
                    settings: Optional[Settings] = None) -> dict:
    """Run seven bounded speculative claims through the public judge API.

    Each stage is judged independently with majority agreement required.
    The returned structure is suitable for ``build_multi_model_xenoscience``
    and preserves disagreements instead of smoothing them away.
    """
    from uapvf.xenoscience import STAGES, build_xenoscience

    settings = settings or get_settings()
    _fail_closed_runtime_guard(settings)
    draft = build_xenoscience(
        case_id, verdict, metadata, lineage_outputs, rigor_result)
    draft_by_stage = {
        item["stage"]: item["output"] for item in draft["stages"]
    }

    import httpx

    judgments = []
    for stage in STAGES:
        body = {
            "claim": json.dumps(
                draft_by_stage[stage], sort_keys=True, ensure_ascii=False),
            "claim_id": f"{case_id}:{stage}",
            # JudgeRequest.context is a string on the live engine; an object
            # here is refused with 422 before any judge runs.
            "context": json.dumps({
                "case_id": case_id,
                "verdict": verdict,
                "stage": stage,
                "evidence_eligible": False,
            }, sort_keys=True, ensure_ascii=False),
            "mock": False,
            "require_agreement": "majority",
        }
        try:
            response = httpx.post(
                settings.SNEFERU_SDK_URL.rstrip("/") + "/sdk/judge",
                json=body,
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise AdapterTimeout(
                f"xenoscience judgment timed out for {stage}") from exc
        except httpx.HTTPError as exc:
            raise AdapterTimeout(
                f"xenoscience judgment failed for {stage}: "
                f"{type(exc).__name__}") from exc
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterSchemaError(
                f"judge response for {stage} was unreadable: "
                f"{type(exc).__name__}") from exc
        judgments.append(_validate_judgment(payload, stage))

    lineage_ids = []
    for judgment in judgments:
        for lineage_id in judgment["lineages_judged"]:
            if lineage_id not in lineage_ids:
                lineage_ids.append(lineage_id)
    model_runs = [
        {
            "model_id": lineage_id,
            "run_id": lineage_id,
            "provider": "sneferu-judge",
        }
        for lineage_id in lineage_ids
    ]
    conflicts = [
        {
            "stage": stage,
            "verdict": judgment.get("verdict"),
            "rationale": judgment.get("rationale"),
            "lineages_judged": judgment["lineages_judged"],
            "per_lineage": judgment["per_lineage"],
        }
        for stage, judgment in zip(STAGES, judgments)
        if not judgment["agreed"]
    ]
    call_ids = [
        judgment["call_id"] for judgment in judgments
        if isinstance(judgment.get("call_id"), str)
        and judgment["call_id"].strip()
    ]
    return {
        "engine_run_id": None,
        "engine_call_ids": call_ids,
        "model_runs": model_runs,
        "stages": [
            {
                "stage": stage,
                "output": draft_by_stage[stage],
                "contributing_model_ids": judgment["lineages_judged"],
            }
            for stage, judgment in zip(STAGES, judgments)
        ],
        "consistency_judgment": {
            "judge_model_id": "sneferu-majority",
            "score": sum(judgment["agreed"] for judgment in judgments)
            / len(judgments),
            "conflicts": conflicts,
            "adjudications": judgments,
        },
    }


def sdk_reachable(settings: Settings) -> bool:
    """Verify that the configured service implements the judge contract.

    ``/sdk/judge`` is stateless and explicitly supports a no-dispatch mock
    probe. This is more portable than assuming the host exposes a particular
    health-route name, and the probe output can never enter a case record.
    """
    if settings.mock_mode:
        return True
    try:
        import httpx

        resp = httpx.post(
            settings.SNEFERU_SDK_URL.rstrip("/") + "/sdk/judge",
            json={
                "claim": "UAP Verdict Foundry readiness contract probe",
                "claim_id": "uapvf-readiness-probe",
                "mock": True,
            },
            timeout=5,
        )
        if resp.status_code >= 400:
            return False
        payload = resp.json()
        return (
            isinstance(payload, dict)
            and payload.get("is_mock") is True
            and isinstance(payload.get("agreed"), bool)
        )
    except Exception:
        return False
