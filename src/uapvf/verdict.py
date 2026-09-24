"""Verdict synthesis (FR-007) and uncertainty calibration (FR-008).

Verdict rule (no exceptions):
  any positive                                 -> mundane_identified
  no positive, >=1 insufficient                -> insufficient_data
  all negative AND no uncovered classification -> no_mundane_match

Amendment: if any lineage classification names a mundane category the
battery configuration does not test (drone/bird/weather_phenomenon per the
§1 mapping), the verdict is insufficient_data with reason
uncovered_mundane_classification regardless of battery results. No verdict
asserts extraterrestrial origin; reasoning is templated, never
model-generated.

Rigor + coverage consumption (spec §2/§3.4/§3.5): ``synthesize_verdict``
also accepts the §3.3 rigor result and the coverage summary. For a
``no_mundane_match`` verdict the result carries explicit qualifications
instead of an unqualified exclusion claim:

* astronomical (OBL-10): lineages classified ``astronomical`` and the
  astronomical battery adapter checked the viewing direction and found no
  known celestial object -> ``astronomical_classification_present: true``
  plus the OBL-10 note;
* coverage (§3.5): weather/balloon geographic coverage sparse or
  unavailable -> caution note for those categories;
* rigor: a supplied rigor result that did not pass marks the
  exclusion conclusion provisional (fail-closed rigor stays visible to
  buyers rather than being silently dropped — this includes the mock
  runtime's outright ``runtime_blocked``).
"""
from __future__ import annotations

import json
from typing import List, Optional

from uapvf.config import get_settings
from uapvf.rigor import ASTRONOMICAL_QUALIFICATION_NOTE

# §1 lineage classification -> battery category mapping
CLASSIFICATION_TO_BATTERY = {
    "aircraft": "aircraft",
    "satellite": "satellites",
    # §1 mapping: lens_artifact AND sensor_artifact both map to the single
    # FR-006 `lens_artifacts` category (artifact group
    # {lens_artifact, sensor_artifact}).
    "lens_artifact": "lens_artifacts",
    "sensor_artifact": "lens_artifacts",
    "astronomical": "astronomical",
    "astronomical_object": "astronomical",
}
# §1 mapping: drone, bird, weather_phenomenon are UNTESTED mundane
# categories — the MVP four-category battery (FR-006) deliberately
# excludes them (spec §1 resolution 13), so naming one triggers the
# uncovered-mundane amendment regardless of any plugin-added battery
# category.
UNTESTED_MUNDANE = {"drone", "bird", "weather_phenomenon"}
# unknown / unidentifiable -> no mapping, no effect
NO_EFFECT = {"unknown", "unidentifiable"}

# New-taxonomy (spec §3.2) and legacy spelling of the astronomical
# classification (OBL-10 qualification).
ASTRONOMICAL_CLASSIFICATIONS = {"astronomical", "astronomical_object"}

RIGOR_PROVISIONAL_NOTE = (
    "Lineage concordance did not satisfy the required rigor conditions "
    "({reason}); the mundane-exclusion conclusion is provisional."
)
COVERAGE_SPARSE_NOTE = (
    "Weather/balloon coverage is sparse for this capture location; "
    "results should be interpreted with caution regarding these "
    "categories."
)

LOW_AGREEMENT_THRESHOLD = 0.6
ZERO_OR_ONE_LINEAGE_PENALTY = 0.15

VERDICT_NO_MUNDANE = "no_mundane_match"
VERDICT_MUNDANE = "mundane_identified"
VERDICT_INSUFFICIENT = "insufficient_data"


def synthesize_verdict(battery_results: List[dict],
                       lineage_outputs: List[dict],
                       rigor_result: Optional[dict] = None,
                       coverage: Optional[dict] = None) -> dict:
    """Apply the verdict rule + amendment, then consume the §3.3 rigor
    result and coverage summary (spec §2/§3.4/§3.5). Returns
    {verdict, verdict_text, reason, astronomical_classification_present,
    qualifications, rigor_passed, rigor}.

    ``rigor_result`` is the persisted ``RigorResult.to_dict()`` from the
    pipeline's concordance call site (None on pre-contract lineage paths);
    ``coverage`` maps battery category -> geographic_coverage value."""
    verdict = _base_verdict(battery_results, lineage_outputs)
    return _qualify_verdict(verdict, battery_results, lineage_outputs,
                            rigor_result, coverage)


def _base_verdict(battery_results: List[dict],
                  lineage_outputs: List[dict]) -> dict:
    """Apply the verdict rule + amendment. Returns
    {verdict, verdict_text, reason}."""
    if not battery_results:
        return {
            "verdict": VERDICT_INSUFFICIENT,
            "verdict_text": (
                "No mundane-explanation categories were configured or "
                "executed, so the capture cannot be evaluated."
            ),
            "reason": "empty_battery",
        }

    positives = [r for r in battery_results if r["result"] == "positive"]
    insufficient = [r for r in battery_results if r["result"] == "insufficient"]

    classifications = [
        (o.get("classification") or "").strip()
        for o in (lineage_outputs or [])
    ]
    battery_categories = {r.get("category") for r in battery_results}
    untested = sorted({
        c for c in classifications
        if c in UNTESTED_MUNDANE
        or (c in CLASSIFICATION_TO_BATTERY
            and CLASSIFICATION_TO_BATTERY[c] not in battery_categories)
    })
    if untested:
        text = (
            "Vision lineages classified this object as "
            f"[{', '.join(untested)}], which is not tested by the current "
            "battery configuration. Because the mundane-explanation space "
            "is not fully testable, the verdict is insufficient_data rather "
            "than no_mundane_match."
        )
        if positives:
            text += (
                " Battery categories returning positive before the "
                "amendment applied: "
                + "; ".join(
                    f"{p['category']}: "
                    f"{str(p.get('evidence_citation') or 'no citation').rstrip().rstrip('.')}"
                    for p in positives
                )
                + "."
            )
        return {
            "verdict": VERDICT_INSUFFICIENT,
            "verdict_text": text,
            "reason": "uncovered_mundane_classification",
        }

    if positives:
        listing = "; ".join(
            f"{p['category']}: "
            f"{str(p.get('evidence_citation') or 'no citation').rstrip().rstrip('.')}"
            for p in positives
        )
        return {
            "verdict": VERDICT_MUNDANE,
            "verdict_text": (
                "Mundane cause identified. Categories returning positive: "
                f"{listing}."
            ),
            "reason": None,
        }
    if insufficient:
        listing = "; ".join(
            f"{r['category']} ({(r.get('source_stamp') or {}).get('reason') or 'no reason recorded'})"
            for r in insufficient
        )
        return {
            "verdict": VERDICT_INSUFFICIENT,
            "verdict_text": (
                "The following categories could not be tested with the "
                f"supplied inputs: {listing}."
            ),
            "reason": None,
        }
    return {
        "verdict": VERDICT_NO_MUNDANE,
        "verdict_text": (
            "Every listed mundane explanation was tested against available "
            "data sources for the supplied media and capture time/place. No "
            "match was found within source coverage areas."
        ),
        "reason": None,
    }


# ---------------------------------------------------------------------------
# §3.4/§3.5 qualifications (no_mundane_match is never unqualified)
# ---------------------------------------------------------------------------

def _astronomical_checked_no_match(battery_results: List[dict],
                                   lineage_outputs: List[dict]) -> bool:
    """OBL-10: lineages classify astronomical AND the astronomical battery
    adapter ran and found no known celestial object. ``negative`` on the
    astronomical category is the current-battery form of the spec's
    ``no_mundane_match_astronomical_checked`` (the adapter checked the
    viewing direction and matched nothing)."""
    astro_lineage = any(
        isinstance(o, dict)
        and (o.get("classification") or "").strip() in ASTRONOMICAL_CLASSIFICATIONS
        for o in (lineage_outputs or [])
    )
    if not astro_lineage:
        return False
    astro = [
        r for r in (battery_results or []) if r.get("category") == "astronomical"
    ]
    return bool(astro) and all(r.get("result") == "negative" for r in astro)


def _coverage_sparse(coverage: Optional[dict]) -> bool:
    """§3.5: any weather/balloon category whose geographic coverage is
    sparse or unavailable qualifies the exclusion conclusion."""
    if not isinstance(coverage, dict) or not coverage:
        return False
    for cat in ("weather", "balloon", "balloons"):
        value = coverage.get(cat)
        if isinstance(value, dict):
            value = value.get("geographic_coverage")
        if value in ("sparse", "unavailable"):
            return True
    return False


def _qualify_verdict(verdict: dict, battery_results: List[dict],
                     lineage_outputs: List[dict],
                     rigor_result: Optional[dict],
                     coverage: Optional[dict]) -> dict:
    """Attach the §3.4/§3.5 qualification channels to the rule-produced
    verdict. Qualifications apply to ``no_mundane_match`` only: the
    mundane_identified and insufficient_data outcomes are driven by
    battery evidence (FR-007) and need no concordance caveat."""
    qualifications: List[str] = []
    astronomical = False
    if verdict["verdict"] == VERDICT_NO_MUNDANE:
        if _astronomical_checked_no_match(battery_results, lineage_outputs):
            astronomical = True
            qualifications.append("astronomical_classification_present")
            verdict["verdict_text"] += " " + ASTRONOMICAL_QUALIFICATION_NOTE
        if _coverage_sparse(coverage):
            qualifications.append("coverage_sparse")
            verdict["verdict_text"] += " " + COVERAGE_SPARSE_NOTE
        if isinstance(rigor_result, dict) and rigor_result.get("passed") is not True:
            qualifications.append("rigor_not_certified")
            verdict["verdict_text"] += " " + RIGOR_PROVISIONAL_NOTE.format(
                reason=rigor_result.get("reason") or "unknown"
            )
    verdict["astronomical_classification_present"] = astronomical
    verdict["qualifications"] = qualifications
    verdict["rigor_passed"] = (
        None if not isinstance(rigor_result, dict)
        else bool(rigor_result.get("passed"))
    )
    verdict["rigor"] = rigor_result if isinstance(rigor_result, dict) else None
    return verdict


# ---------------------------------------------------------------------------
# FR-008 uncertainty
# ---------------------------------------------------------------------------

def load_live_calibration(settings=None) -> Optional[dict]:
    """Load var/benchmark/calibration.json only when it is a LIVE-mode run.
    Mock calibration is never used for live-case uncertainty (FR-014)."""
    settings = settings or get_settings()
    path = settings.var_dir / "benchmark" / "calibration.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if data.get("mode") != "live":
        return None
    return data


def _calibrated_probability(calibration: dict, raw_confidence: float) -> Optional[float]:
    bins = calibration.get("bins") or []
    if not bins:
        return None
    for b in bins:
        lo = float(b.get("lower_bound", 0.0))
        hi = float(b.get("upper_bound", 1.0))
        inclusive = bool(b.get("upper_inclusive", False))
        if raw_confidence >= lo and (raw_confidence < hi or (inclusive and raw_confidence <= hi)):
            return float(b.get("probability", 0.0))
    return None


def compute_uncertainty(
    verdict: str,
    battery_results: List[dict],
    agreement_fraction: float,
    lineage_count: int,
    quality_score_normalized: float,
    settings=None,
) -> dict:
    """FR-008 raw confidence + calibration mapping + low-confidence flag.

    low_confidence is set only when >=2 lineages disagree below the 0.6
    threshold: with 0 or 1 lineages there is no disagreement to flag (the
    0.15 availability penalty still applies; AC-032)."""
    settings = settings or get_settings()
    total = len(battery_results)
    negative_count = sum(1 for r in battery_results if r["result"] == "negative")
    insufficient_count = sum(
        1 for r in battery_results if r["result"] == "insufficient"
    )
    agreement = float(agreement_fraction or 0.0)
    quality = max(0.0, min(1.0, float(quality_score_normalized or 0.0)))

    if total == 0:
        raw = 0.0
    elif verdict == VERDICT_MUNDANE:
        positives = [r for r in battery_results if r["result"] == "positive"]
        positive_evidence_strength = (
            1.0
            if positives and all((p.get("evidence_citation") or "").strip() for p in positives)
            else 0.5
        )
        raw = (
            0.50 * positive_evidence_strength
            + 0.30 * agreement
            + 0.20 * quality
        )
    elif verdict == VERDICT_NO_MUNDANE:
        source_coverage_ratio = (total - insufficient_count) / total
        raw = (
            0.40 * (negative_count / total)
            + 0.25 * agreement
            + 0.20 * quality
            + 0.15 * source_coverage_ratio
        )
    else:  # insufficient_data
        sufficient_ratio = (total - insufficient_count) / total
        raw = (
            0.30 * (negative_count / total)
            + 0.20 * agreement
            + 0.20 * quality
            + 0.30 * sufficient_ratio
        )

    penalty = ZERO_OR_ONE_LINEAGE_PENALTY if lineage_count <= 1 else 0.0
    raw_confidence = max(0.0, min(1.0, raw - penalty))
    low_confidence = lineage_count >= 2 and agreement < LOW_AGREEMENT_THRESHOLD

    calibration = load_live_calibration(settings)
    if calibration is None:
        return {
            "raw_confidence": round(raw, 6),
            "penalty": penalty,
            "value": None,
            "calibrated": False,
            "calibration_source": None,
            "low_confidence": low_confidence,
            "uncertainty_reason": "no calibration run available",
        }
    probability = _calibrated_probability(calibration, raw_confidence)
    vsid = calibration.get("validation_set_id")
    if probability is None:
        return {
            "raw_confidence": round(raw, 6),
            "penalty": penalty,
            "value": None,
            "calibrated": False,
            "calibration_source": None,
            "low_confidence": low_confidence,
            "uncertainty_reason": "no calibration run available",
        }
    reason = f"calibrated from validation set {vsid}"
    if verdict == VERDICT_NO_MUNDANE:
        reason += (
            " — provisional — calibrated from synthetic and known-mundane "
            "cases; no ground truth exists for truly unexplained cases."
        )
    return {
        "raw_confidence": round(raw, 6),
        "penalty": penalty,
        "value": round(probability, 6),
        "calibrated": True,
        "calibration_source": {
            "validation_set_id": vsid,
            "validation_set_hash": calibration.get("validation_set_hash"),
        },
        "low_confidence": low_confidence,
        "uncertainty_reason": reason,
    }
