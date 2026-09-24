"""Confidence calibration with abstention-aware accuracy (spec §3.2).

Separate from independence validation: calibration measures each lineage's
classification accuracy against ground truth on the *calibration* eval set
(``eval_set.load_calibration_eval_set``, >=100 items with >=60 in the
l6-covered categories) and fits a confidence transform whose parameters
the CLI stores at ``var/lineage_calibration/{lineage_id}.json``.

Rules (spec §3.2 "Calibration"):

* Accuracy is computed only over comparable items — items where the
  lineage returned a classification (``status: ok``; an abstention is
  recorded as a ``null`` classification and excluded from accuracy).
* Minimum 80 comparable items. Fewer -> the lineage fails with
  ``insufficient_comparable_for_calibration`` (fail closed).
* ``calibrated: true`` requires accuracy >= 0.60 over comparable items
  AND >= 80 comparable items (thresholds live in
  ``defaults/rigor_config.json``: ``calibration_accuracy_threshold``,
  ``calibration_min_comparable``).
* Fit method per lineage: Platt scaling (temperature) for l6; isotonic
  regression (pool adjacent violators) for l1-l5 and l7.

Pure stdlib: the Platt temperature is a deterministic golden-section
minimisation of the negative log-likelihood; the isotonic fit is the
classic PAV step function with linear interpolation between block
midpoints at apply time. Both are seeded-free closed-form computations —
repeated runs on the same eval set produce byte-identical parameters.

The per-lineage result serialises to the stored parameter file and
carries everything the live rigor gate (``rigor.validate_stored_calibration``)
re-checks rather than trusts: the ``calibrated`` flag, accuracy,
comparable count, and the eval-set provenance block (source, the
``synthetic_ci_not_production_calibration`` marker when the CI fallback
set was used, manifest path, item count, validation-set hash).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from uapvf.rigor import load_rigor_config

# Spec §3.2 thresholds (mirrors defaults/rigor_config.json).
DEFAULT_ACCURACY_THRESHOLD = 0.6
DEFAULT_MIN_COMPARABLE = 80

# Spec §3.2: Platt scaling (temperature) for l6; isotonic regression for
# l1-l5, l7. Rule files and the registry key lineages by prefix.
METHOD_PLATT = "platt_temperature"
METHOD_ISOTONIC = "isotonic"
PLATT_LINEAGE_PREFIXES = frozenset({"l6"})

# Failure reason codes (spec-literal where the spec names one).
REASON_INSUFFICIENT_COMPARABLE = "insufficient_comparable_for_calibration"
REASON_ACCURACY_BELOW_THRESHOLD = "accuracy_below_threshold"

# Numerical guards for logit/sigmoid.
_EPS = 1e-9
_PLATT_T_LO = 0.01
_PLATT_T_HI = 20.0
_PLATT_ITERATIONS = 200


def _lineage_prefix(lineage_id: str) -> str:
    """'l6-resnet50' -> 'l6'."""
    return lineage_id.split("-", 1)[0]


def calibration_method(lineage_id: str) -> str:
    """Fit method mandated per lineage by spec §3.2."""
    if _lineage_prefix(lineage_id) in PLATT_LINEAGE_PREFIXES:
        return METHOD_PLATT
    return METHOD_ISOTONIC


# ---------------------------------------------------------------------------
# Fit primitives (pure stdlib, deterministic)
# ---------------------------------------------------------------------------


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clamp_probability(p: float) -> float:
    return min(1.0 - _EPS, max(_EPS, float(p)))


def platt_temperature_fit(
    confidences: Sequence[float],
    correct: Sequence[int],
    *,
    t_lo: float = _PLATT_T_LO,
    t_hi: float = _PLATT_T_HI,
    iterations: int = _PLATT_ITERATIONS,
) -> float:
    """Fit the Platt-scaling temperature T > 0.

    Calibrated probability ``sigmoid(logit(p) / T)``; T minimises the mean
    negative log-likelihood over (confidence, correctness) pairs via a
    deterministic golden-section search on ``[t_lo, t_hi]``.

    Raises:
        ValueError: on empty or length-mismatched input.
    """
    if len(confidences) != len(correct):
        raise ValueError("confidences and correct must have equal length")
    if not confidences:
        raise ValueError("cannot fit a temperature over zero items")
    logits = [math.log(_clamp_probability(p) / (1.0 - _clamp_probability(p)))
              for p in confidences]
    targets = [float(c) for c in correct]

    def nll(t: float) -> float:
        total = 0.0
        for z, y in zip(logits, targets):
            p = _sigmoid(z / t)
            p = _clamp_probability(p)
            total += -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
        return total / len(logits)

    gr = (math.sqrt(5.0) - 1.0) / 2.0
    lo, hi = float(t_lo), float(t_hi)
    c = hi - gr * (hi - lo)
    d = lo + gr * (hi - lo)
    fc, fd = nll(c), nll(d)
    for _ in range(int(iterations)):
        if fc < fd:
            hi = d
            d = c
            fd = fc
            c = hi - gr * (hi - lo)
            fc = nll(c)
        else:
            lo = c
            c = d
            fc = fd
            d = lo + gr * (hi - lo)
            fd = nll(d)
    return round((lo + hi) / 2.0, 9)


def isotonic_fit(
    confidences: Sequence[float],
    correct: Sequence[int],
) -> List[List[float]]:
    """Pool-adjacent-violators isotonic regression (l1-l5, l7).

    Fits the non-decreasing step function minimising squared error over
    (confidence, correctness) pairs. Returns interpolation knots
    ``[[x, y], ...]`` sorted by x, where x is each PAV block's midpoint
    and y its mean target — the form :func:`apply_calibration` consumes.

    Raises:
        ValueError: on empty or length-mismatched input.
    """
    if len(confidences) != len(correct):
        raise ValueError("confidences and correct must have equal length")
    if not confidences:
        raise ValueError("cannot fit isotonic regression over zero items")
    order = sorted(
        range(len(confidences)),
        key=lambda i: (float(confidences[i]), i),
    )
    # Each block: [sum_y, count, x_min, x_max]; merge while the means are
    # not non-decreasing (the "pool adjacent violators" step).
    blocks: List[List[float]] = []
    for i in order:
        x = float(confidences[i])
        blocks.append([float(correct[i]), 1.0, x, x])
        while len(blocks) > 1:
            s_prev, c_prev, _, _ = blocks[-2]
            s_last, c_last, _, _ = blocks[-1]
            if s_prev / c_prev <= s_last / c_last:
                break
            s2, c2, _, xmax = blocks.pop()
            s1, c1, xmin, _ = blocks.pop()
            blocks.append([s1 + s2, c1 + c2, xmin, xmax])
    knots: List[List[float]] = []
    for s, c, xmin, xmax in blocks:
        knots.append([round((xmin + xmax) / 2.0, 12), round(s / c, 12)])
    return knots


def apply_calibration(confidence: float, parameters: Dict[str, Any]) -> float:
    """Apply fitted parameters to a raw confidence.

    ``{"temperature": T}`` -> Platt ``sigmoid(logit(p) / T)``.
    ``{"isotonic": [[x, y], ...]}`` -> clamped linear interpolation
    between block midpoints (constant outside the outermost knots). Both
    are monotone non-decreasing and map into [0, 1].

    Raises:
        ValueError: on an unknown or empty parameter shape.
    """
    p = _clamp_probability(confidence)
    if "temperature" in parameters:
        t = float(parameters["temperature"])
        if t <= 0:
            raise ValueError(f"temperature must be positive, got {t}")
        z = math.log(p / (1.0 - p))
        return _sigmoid(z / t)
    if "isotonic" in parameters:
        knots = parameters["isotonic"]
        if not knots:
            raise ValueError("isotonic parameters carry no knots")
        xs = [float(k[0]) for k in knots]
        ys = [float(k[1]) for k in knots]
        raw = float(confidence)
        if raw <= xs[0]:
            return min(1.0, max(0.0, ys[0]))
        if raw >= xs[-1]:
            return min(1.0, max(0.0, ys[-1]))
        for j in range(len(xs) - 1):
            if xs[j] <= raw <= xs[j + 1]:
                if xs[j + 1] == xs[j]:
                    return min(1.0, max(0.0, ys[j]))
                frac = (raw - xs[j]) / (xs[j + 1] - xs[j])
                y = ys[j] + frac * (ys[j + 1] - ys[j])
                return min(1.0, max(0.0, y))
        return min(1.0, max(0.0, ys[-1]))
    raise ValueError(
        "unknown calibration parameters: expected 'temperature' or "
        f"'isotonic', got {sorted(parameters)}"
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def macro_precision_recall(
    labels: Sequence[str], predictions: Sequence[str]
) -> Tuple[float, float]:
    """Macro-averaged per-category precision/recall over scored items.

    Categories are the union of ground-truth and predicted values; a
    category with no predictions gets precision 0.0 (standard macro
    behaviour). Deterministic: categories iterate in sorted order.
    """
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must have equal length")
    if not labels:
        return 0.0, 0.0
    pairs = list(zip(labels, predictions))
    categories = sorted(set(labels) | set(predictions))
    precisions: List[float] = []
    recalls: List[float] = []
    for cat in categories:
        tp = sum(1 for l, p in pairs if l == cat and p == cat)
        fp = sum(1 for l, p in pairs if l != cat and p == cat)
        fn = sum(1 for l, p in pairs if l == cat and p != cat)
        precisions.append(tp / (tp + fp) if (tp + fp) else 0.0)
        recalls.append(tp / (tp + fn) if (tp + fn) else 0.0)
    return sum(precisions) / len(precisions), sum(recalls) / len(recalls)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class CalibrationResult:
    """One lineage's calibration outcome; serialises to the stored
    parameter file ``var/lineage_calibration/{lineage_id}.json``."""

    lineage_id: str
    calibrated: bool
    reason: Optional[str]
    method: str
    comparable_count: int
    total_items: int
    abstention_rate: float
    accuracy: Optional[float]  # None when nothing was comparable
    precision: Optional[float]
    recall: Optional[float]
    parameters: Dict[str, Any]
    eval_set: Dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "lineage_id": self.lineage_id,
            "calibrated": self.calibrated,
            "reason": self.reason,
            "method": self.method,
            "comparable_count": self.comparable_count,
            "total_items": self.total_items,
            "abstention_rate": self.abstention_rate,
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "parameters": self.parameters,
            "eval_set": dict(self.eval_set),
        }

    def calibration_record(self) -> Dict[str, Any]:
        """The ``LineageOutput.calibration`` block shape (spec §3.2)."""
        return {
            "calibrated": self.calibrated,
            "source": (
                self.eval_set.get("marker")
                or ("operator_eval_set"
                    if self.eval_set.get("source") == "operator"
                    else self.eval_set.get("source"))
            ),
            "validation_set_hash": self.eval_set.get("validation_set_hash"),
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "abstention_rate": self.abstention_rate,
            "comparable_count": self.comparable_count,
        }


def _eval_set_provenance(eval_set) -> Dict[str, Any]:
    path = getattr(eval_set, "path", None)
    return {
        "source": str(getattr(eval_set, "source", "unknown")),
        "marker": getattr(eval_set, "marker", None),
        "path": str(path) if path is not None else None,
        "items": len(getattr(eval_set, "labels", []) or []),
        "subset_counts": dict(getattr(eval_set, "subset_counts", {}) or {}),
        "l6_covered_count": int(getattr(eval_set, "l6_covered_count", 0)),
        "validation_set_hash": getattr(eval_set, "validation_set_hash", None),
        "provenance": getattr(eval_set, "provenance", None),
    }


def calibrate_lineage(
    lineage_id: str,
    eval_set,
    *,
    accuracy_threshold: float = DEFAULT_ACCURACY_THRESHOLD,
    min_comparable: int = DEFAULT_MIN_COMPARABLE,
) -> CalibrationResult:
    """Calibrate one lineage over a ``CalibrationEvalSet``.

    Accuracy is computed only over comparable items (recorded
    classification non-null); abstentions are excluded from accuracy and
    reported as ``abstention_rate``. The confidence transform is fitted
    only when the comparable minimum is met; a lineage below it fails
    with ``insufficient_comparable_for_calibration`` and no parameters.
    """
    labels = list(eval_set.labels)
    total = len(labels)
    preds: List[str] = []
    truths: List[str] = []
    scores: List[float] = []
    for i in range(total):
        pred = eval_set.classifications_by_item[i].get(lineage_id)
        if pred is None:
            continue  # abstention -> excluded from accuracy
        preds.append(pred)
        truths.append(labels[i])
        conf = eval_set.confidences_by_item[i].get(lineage_id)
        scores.append(float(conf) if conf is not None else 0.5)

    comparable = len(preds)
    abstained = total - comparable
    abstention_rate = abstained / total if total else 0.0
    provenance = _eval_set_provenance(eval_set)
    method = calibration_method(lineage_id)

    if comparable < min_comparable:
        return CalibrationResult(
            lineage_id=lineage_id,
            calibrated=False,
            reason=REASON_INSUFFICIENT_COMPARABLE,
            method=method,
            comparable_count=comparable,
            total_items=total,
            abstention_rate=abstention_rate,
            accuracy=None,
            precision=None,
            recall=None,
            parameters={},
            eval_set=provenance,
        )

    correct = [int(p == t) for p, t in zip(preds, truths)]
    accuracy = sum(correct) / comparable
    precision, recall = macro_precision_recall(truths, preds)
    if method == METHOD_PLATT:
        parameters: Dict[str, Any] = {
            "temperature": platt_temperature_fit(scores, correct)
        }
    else:
        parameters = {"isotonic": isotonic_fit(scores, correct)}

    if accuracy >= accuracy_threshold:
        calibrated = True
        reason: Optional[str] = None
    else:
        calibrated = False
        reason = REASON_ACCURACY_BELOW_THRESHOLD

    return CalibrationResult(
        lineage_id=lineage_id,
        calibrated=calibrated,
        reason=reason,
        method=method,
        comparable_count=comparable,
        total_items=total,
        abstention_rate=abstention_rate,
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        parameters=parameters,
        eval_set=provenance,
    )


def _registry_lineage_ids(registry) -> List[str]:
    entries = list(registry)
    if not entries:
        raise ValueError("registry is empty: no lineages to calibrate")
    ids: List[str] = []
    for entry in entries:
        lineage_id = getattr(entry, "lineage_id", None)
        if not lineage_id:
            raise ValueError(
                "registry entries must expose a lineage_id "
                "(pass the load_registry() result)"
            )
        ids.append(lineage_id)
    return ids


def calibrate_lineages(
    registry,
    eval_set,
    *,
    config: Optional[dict] = None,
) -> Dict[str, CalibrationResult]:
    """Calibrate every lineage in the registry over the eval set.

    Thresholds come from ``rigor_config.json`` (single source of truth:
    ``calibration_accuracy_threshold`` / ``calibration_min_comparable``)
    unless an explicit *config* is supplied. The registry is used for its
    declared lineage ids in declaration order; the recorded eval-set data
    drives the statistics (an unavailable implementation is annotated by
    the CLI summary, never silently skipped — same contract as
    ``independence.validate_independence``).

    Raises:
        ValueError: on an empty or malformed registry.
    """
    config = config or load_rigor_config()
    accuracy_threshold = float(
        config.get("calibration_accuracy_threshold", DEFAULT_ACCURACY_THRESHOLD)
    )
    min_comparable = int(
        config.get("calibration_min_comparable", DEFAULT_MIN_COMPARABLE)
    )
    results: Dict[str, CalibrationResult] = {}
    for lineage_id in _registry_lineage_ids(registry):
        results[lineage_id] = calibrate_lineage(
            lineage_id,
            eval_set,
            accuracy_threshold=accuracy_threshold,
            min_comparable=min_comparable,
        )
    return results
