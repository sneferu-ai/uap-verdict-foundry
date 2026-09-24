"""Eval-set management for lineage certification (spec §3.2: calibration
eval set + independence eval set, pts 1/7/8, AC-2, OBL-21).

The two eval sets are separate by design (spec §3.2 "Separate calibration
(>=100 items...) and independence (>=300 items) eval sets"):

* **Calibration** — ``<var_dir>/operator/eval_set/manifest.json``: at
  least 100 items with ground-truth labels, at least 60 of them in the
  l6-covered categories (aircraft, bird, insect — the ~3 of 10 taxonomy
  categories ImageNet-1k maps to). Consumed by ``calibration.py`` /
  ``uapvf lineages calibrate``.
* **Independence** — ``<var_dir>/operator/eval_set_independence/
  manifest.json``: at least 300 recorded items, consumed by
  ``independence.py`` / ``uapvf lineages validate``.

The operator's independence eval set is a recorded, blinded collection of
at least 300 items at ``<var_dir>/operator/eval_set_independence/
manifest.json`` (path unified at runtime per spec §2).  Each item records
the classification every declared lineage produced for it (``null`` = the
lineage abstained) plus the item's subset and, for general items, its
difficulty label:

    {
      "eval_set": "independence",
      "items": [
        {
          "item_id": "general-0001",
          "subset": "general",
          "difficulty": "hard",
          "classifications": {
            "l1-photometric": "aircraft", ..., "l7-audio": null
          }
        }
      ]
    }

Composition is validated fail-closed (spec §3.2 pt 1/7): at least 100
general, 100 l6-enriched, 50 l4-enriched, and 50 l7-enriched items; at
least 30% of general items must be hard/ambiguous.  Classifications must
be keyed by exactly the seven declared lineage ids with taxonomy-valid
values or ``null`` — an abstention is recorded data, never a missing key.

When the operator set is absent, :func:`load_independence_eval_set` falls
back to a deterministic 20-item synthetic set marked
``synthetic_ci_not_production_independence`` (spec §3.2 pt 8).  That set
only exercises the validation code path: with fewer than 100 comparable
items per pair every pair fails closed, so ``uapvf lineages validate``
exits 1 and the product never claims independence without operator data.

The calibration loader (:func:`load_calibration_eval_set`) follows the
same fail-closed pattern: when the operator calibration set is absent it
falls back to a deterministic 20-item synthetic set marked
``synthetic_ci_not_production_calibration``.  With fewer than the 80
comparable items calibration requires, every lineage fails with
``insufficient_comparable_for_calibration``, so ``uapvf lineages
calibrate`` exits 1 and live rigor condition 5 can never be satisfied by
synthetic data.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from uapvf.lineages.registry import declared_lineage_ids
from uapvf.lineages.taxonomy import TAXONOMY, is_valid_taxonomy

SYNTHETIC_MARKER = "synthetic_ci_not_production_independence"

SUBSET_GENERAL = "general"
SUBSET_L6 = "l6_enriched"
SUBSET_L4 = "l4_enriched"
SUBSET_L7 = "l7_enriched"
ALLOWED_SUBSETS = frozenset({SUBSET_GENERAL, SUBSET_L6, SUBSET_L4, SUBSET_L7})

# Spec §3.2 pt 1: >=300 items; 100 general + 100 l6-enriched + 50
# l4-enriched + 50 l7-enriched (enforced as minimums — larger supersets
# are permitted and only strengthen the comparable-item guarantees).
MIN_TOTAL_ITEMS = 300
MIN_SUBSET_COUNTS = {
    SUBSET_GENERAL: 100,
    SUBSET_L6: 100,
    SUBSET_L4: 50,
    SUBSET_L7: 50,
}

# Spec §3.2 pt 7: >=30% of general items hard/ambiguous, computed with
# exact integer arithmetic (hard*10 >= 3*general_total).
HARD_AMBIGUOUS_NUMERATOR = 3
HARD_AMBIGUOUS_DENOMINATOR = 10
HARD_AMBIGUOUS_LABELS = frozenset({"hard", "ambiguous"})
ALLOWED_GENERAL_DIFFICULTIES = frozenset({"hard", "ambiguous", "easy"})

# Spec §3.2 pt 8: synthetic CI fallback size.
SYNTHETIC_ITEM_COUNT = 20

MANIFEST_NAME = "manifest.json"


class EvalSetError(ValueError):
    """The eval set is missing or fails composition validation."""


@dataclass
class IndependenceEvalSet:
    """Recorded independence eval items, ready for
    ``independence.validate_independence(registry, eval_set)``.

    ``classifications_by_item[i]`` maps lineage_id -> classification
    (``None`` = abstention).  ``item_difficulties[i]`` is the difficulty
    channel consumed by the independence report: the difficulty label for
    general items (``None`` = unlabeled), the subset tag for enriched
    items — so both the >=30%-hard general composition and the
    enriched-subset sizes are auditable from the stored report.
    """

    classifications_by_item: List[Dict[str, Optional[str]]]
    item_difficulties: List[Optional[str]]
    item_ids: List[str]
    subset_counts: Dict[str, int]
    source: str  # "operator" | "synthetic"
    marker: Optional[str] = None  # SYNTHETIC_MARKER on CI fallback sets
    path: Optional[Path] = None  # manifest location (operator sets only)
    validation_set_hash: Optional[str] = None  # SHA-256 of exact manifest bytes
    provenance: Optional[Dict] = None  # recorder provenance, when supplied

    @property
    def total_items(self) -> int:
        return len(self.classifications_by_item)


def independence_eval_dir(var_dir: Union[str, Path]) -> Path:
    """Unified runtime path of the operator independence eval set."""
    return Path(var_dir) / "operator" / "eval_set_independence"


def load_independence_eval_set(
    var_dir: Union[str, Path],
    *,
    fallback_to_synthetic: bool = True,
) -> IndependenceEvalSet:
    """Load the operator independence eval set from ``var_dir``.

    Falls back to the marked synthetic CI set (spec §3.2 pt 8) when the
    operator manifest is absent, unless ``fallback_to_synthetic`` is False
    (live-mode callers that must fail closed on missing operator data).

    Raises:
        EvalSetError: if the manifest exists but fails validation, or is
            absent and the synthetic fallback is disabled.
    """
    manifest_path = independence_eval_dir(var_dir) / MANIFEST_NAME
    if manifest_path.exists():
        return _load_operator_manifest(manifest_path)
    if fallback_to_synthetic:
        return synthetic_independence_eval_set()
    raise EvalSetError(
        f"independence eval set not found: expected {manifest_path} "
        "(spec §3.2 pt 1); provide the operator set or accept the marked "
        "synthetic CI fallback"
    )


def _load_operator_manifest(manifest_path: Path) -> IndependenceEvalSet:
    try:
        raw_bytes = manifest_path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalSetError(
            f"cannot read independence eval set manifest "
            f"{manifest_path}: {exc}"
        )
    if not isinstance(payload, dict):
        raise EvalSetError("manifest root must be a JSON object")
    kind = payload.get("eval_set", "independence")
    if kind != "independence":
        raise EvalSetError(
            f'manifest "eval_set" must be "independence", got {kind!r}'
        )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise EvalSetError('manifest must contain a non-empty "items" list')

    declared = declared_lineage_ids()
    declared_set = frozenset(declared)
    classifications_by_item: List[Dict[str, Optional[str]]] = []
    item_difficulties: List[Optional[str]] = []
    item_ids: List[str] = []
    subset_counts = {subset: 0 for subset in ALLOWED_SUBSETS}
    general_hard_ambiguous = 0
    seen_ids: set = set()

    for pos, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise EvalSetError(f"item #{pos}: must be a JSON object")
        item_id = raw.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise EvalSetError(
                f"item #{pos}: missing non-empty string item_id"
            )
        if item_id in seen_ids:
            raise EvalSetError(f"item #{pos}: duplicate item_id {item_id!r}")
        seen_ids.add(item_id)

        subset = raw.get("subset")
        if subset not in ALLOWED_SUBSETS:
            raise EvalSetError(
                f"item {item_id!r}: subset must be one of "
                f"{sorted(ALLOWED_SUBSETS)}, got {subset!r}"
            )

        classifications = raw.get("classifications")
        if not isinstance(classifications, dict):
            raise EvalSetError(
                f"item {item_id!r}: classifications must be an object"
            )
        keys = set(classifications)
        if keys != declared_set:
            detail = []
            missing = sorted(declared_set - keys)
            unknown = sorted(keys - declared_set)
            if missing:
                detail.append(f"missing {missing}")
            if unknown:
                detail.append(f"unknown {unknown}")
            raise EvalSetError(
                f"item {item_id!r}: classifications keys must be exactly "
                f"the declared lineage ids ({'; '.join(detail)})"
            )
        record: Dict[str, Optional[str]] = {}
        for lineage_id in declared:
            value = classifications[lineage_id]
            if value is not None and not is_valid_taxonomy(value):
                raise EvalSetError(
                    f"item {item_id!r}: {lineage_id} classification "
                    f"{value!r} is not in the taxonomy (record abstention "
                    "as null)"
                )
            record[lineage_id] = value

        difficulty = raw.get("difficulty")
        if subset == SUBSET_GENERAL:
            if (
                difficulty is not None
                and difficulty not in ALLOWED_GENERAL_DIFFICULTIES
            ):
                raise EvalSetError(
                    f"item {item_id!r}: general difficulty must be one of "
                    f"{sorted(ALLOWED_GENERAL_DIFFICULTIES)} or null, got "
                    f"{difficulty!r}"
                )
            if difficulty in HARD_AMBIGUOUS_LABELS:
                general_hard_ambiguous += 1
            item_difficulties.append(difficulty)
        else:
            # Enriched items carry their subset tag on the difficulty
            # channel so enriched-subset sizes are auditable in the report.
            item_difficulties.append(subset)

        subset_counts[subset] += 1
        classifications_by_item.append(record)
        item_ids.append(item_id)

    total = len(item_ids)
    if total < MIN_TOTAL_ITEMS:
        raise EvalSetError(
            f"independence eval set has {total} items; spec §3.2 pt 1 "
            f"requires >= {MIN_TOTAL_ITEMS}"
        )
    for subset in sorted(MIN_SUBSET_COUNTS):
        minimum = MIN_SUBSET_COUNTS[subset]
        if subset_counts[subset] < minimum:
            raise EvalSetError(
                f"subset {subset!r} has {subset_counts[subset]} items; "
                f"spec §3.2 pt 1 requires >= {minimum}"
            )
    general_total = subset_counts[SUBSET_GENERAL]
    if (
        general_hard_ambiguous * HARD_AMBIGUOUS_DENOMINATOR
        < HARD_AMBIGUOUS_NUMERATOR * general_total
    ):
        raise EvalSetError(
            f"only {general_hard_ambiguous}/{general_total} general items "
            "are hard/ambiguous; spec §3.2 pt 7 requires >= 30%"
        )

    return IndependenceEvalSet(
        classifications_by_item=classifications_by_item,
        item_difficulties=item_difficulties,
        item_ids=item_ids,
        subset_counts=subset_counts,
        source="operator",
        marker=None,
        path=manifest_path,
        validation_set_hash=hashlib.sha256(raw_bytes).hexdigest(),
        provenance=(
            dict(payload["provenance"])
            if isinstance(payload.get("provenance"), dict)
            else None
        ),
    )


def synthetic_independence_eval_set(
    n_items: int = SYNTHETIC_ITEM_COUNT,
) -> IndependenceEvalSet:
    """Deterministic CI fallback (spec §3.2 pt 8), marked
    ``synthetic_ci_not_production_independence``.

    Exercises the validation code path only: it has fewer than 100 items,
    so every pair fails the comparable-minimum requirement and no
    independence claim can ever be produced from it (fail closed).
    Classifications are mutually uncorrelated across lineages (distinct
    multipliers mod 7 over real taxonomy values), 30% of items are hard.
    """
    ids = declared_lineage_ids()
    categories = sorted(TAXONOMY)[: len(ids)]
    classifications_by_item: List[Dict[str, Optional[str]]] = []
    item_difficulties: List[Optional[str]] = []
    item_ids: List[str] = []
    for i in range(int(n_items)):
        classifications_by_item.append(
            {
                lineage_id: categories[(i * (k + 1) + k) % len(categories)]
                for k, lineage_id in enumerate(ids)
            }
        )
        item_difficulties.append("hard" if (i % 10) < 3 else "easy")
        item_ids.append(f"synthetic-{i:04d}")
    subset_counts = {subset: 0 for subset in ALLOWED_SUBSETS}
    subset_counts[SUBSET_GENERAL] = int(n_items)
    return IndependenceEvalSet(
        classifications_by_item=classifications_by_item,
        item_difficulties=item_difficulties,
        item_ids=item_ids,
        subset_counts=subset_counts,
        source="synthetic",
        marker=SYNTHETIC_MARKER,
        path=None,
        validation_set_hash=None,
        provenance=None,
    )

# ---------------------------------------------------------------------------
# Calibration eval set (spec §3.2: separate from the independence set)
# ---------------------------------------------------------------------------

SYNTHETIC_CALIBRATION_MARKER = "synthetic_ci_not_production_calibration"

CAL_SUBSET_GENERAL = "general"
CAL_SUBSET_L6 = "l6_enriched"
ALLOWED_CALIBRATION_SUBSETS = frozenset({CAL_SUBSET_GENERAL, CAL_SUBSET_L6})

# Spec §3.2 l6 ImageNet-1k coverage: aircraft (airplane classes), bird
# (bird classes), insect (insect classes). The remaining 7 categories
# cause l6 abstention, so the l6-enriched portion of the calibration set
# concentrates on exactly these categories.
L6_COVERED_CATEGORIES = frozenset({"aircraft", "bird", "insect"})

# Spec §3.2: calibration eval set >=100 items, >=60 in l6-covered
# categories (enforced as minimums — larger supersets are permitted).
MIN_CALIBRATION_ITEMS = 100
MIN_L6_COVERED_ITEMS = 60

# Same size philosophy as the independence CI fallback: enough to exercise
# the code path, too small (20 < 80 comparable) to ever certify.
SYNTHETIC_CALIBRATION_ITEM_COUNT = 20


@dataclass
class CalibrationEvalSet:
    """Recorded calibration eval items with ground-truth labels.

    ``labels[i]`` is the ground-truth taxonomy category of item *i*;
    ``classifications_by_item[i]`` maps lineage_id -> recorded
    classification (``None`` = abstention, excluded from accuracy);
    ``confidences_by_item[i]`` maps lineage_id -> raw confidence of the
    recorded classification (``None`` where the lineage abstained). The
    raw confidence channel is what the Platt/isotonic fits in
    ``calibration.py`` consume.
    """

    labels: List[str]
    classifications_by_item: List[Dict[str, Optional[str]]]
    confidences_by_item: List[Dict[str, Optional[float]]]
    item_ids: List[str]
    subset_counts: Dict[str, int]
    l6_covered_count: int
    source: str  # "operator" | "synthetic"
    marker: Optional[str] = None  # SYNTHETIC_CALIBRATION_MARKER on CI sets
    path: Optional[Path] = None  # manifest location (operator sets only)
    validation_set_hash: Optional[str] = None  # SHA-256 of the manifest
    provenance: Optional[Dict] = None  # recorder provenance, when supplied

    @property
    def total_items(self) -> int:
        return len(self.labels)


def calibration_eval_dir(var_dir: Union[str, Path]) -> Path:
    """Unified runtime path of the operator calibration eval set (spec §2:
    ``tests/fixtures/eval_set/`` is operator-provided; runtime path is
    ``var/operator/eval_set/``, mirroring the independence unification)."""
    return Path(var_dir) / "operator" / "eval_set"


def load_calibration_eval_set(
    var_dir: Union[str, Path],
    *,
    fallback_to_synthetic: bool = True,
) -> CalibrationEvalSet:
    """Load the operator calibration eval set from ``var_dir``.

    Falls back to the marked synthetic CI set when the operator manifest
    is absent, unless ``fallback_to_synthetic`` is False (live-mode
    callers that must fail closed on missing operator data).

    Raises:
        EvalSetError: if the manifest exists but fails validation, or is
            absent and the synthetic fallback is disabled.
    """
    manifest_path = calibration_eval_dir(var_dir) / MANIFEST_NAME
    if manifest_path.exists():
        return _load_calibration_manifest(manifest_path)
    if fallback_to_synthetic:
        return synthetic_calibration_eval_set()
    raise EvalSetError(
        f"calibration eval set not found: expected {manifest_path} "
        "(spec §3.2); provide the operator set or accept the marked "
        "synthetic CI fallback"
    )


def _load_calibration_manifest(manifest_path: Path) -> CalibrationEvalSet:
    try:
        raw_bytes = manifest_path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalSetError(
            f"cannot read calibration eval set manifest "
            f"{manifest_path}: {exc}"
        )
    if not isinstance(payload, dict):
        raise EvalSetError("manifest root must be a JSON object")
    kind = payload.get("eval_set")
    if kind != "calibration":
        raise EvalSetError(
            f'manifest "eval_set" must be "calibration", got {kind!r}'
        )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise EvalSetError('manifest must contain a non-empty "items" list')

    declared = declared_lineage_ids()
    declared_set = frozenset(declared)
    labels: List[str] = []
    classifications_by_item: List[Dict[str, Optional[str]]] = []
    confidences_by_item: List[Dict[str, Optional[float]]] = []
    item_ids: List[str] = []
    subset_counts = {subset: 0 for subset in ALLOWED_CALIBRATION_SUBSETS}
    l6_covered_count = 0
    seen_ids: set = set()

    for pos, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise EvalSetError(f"item #{pos}: must be a JSON object")
        item_id = raw.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise EvalSetError(
                f"item #{pos}: missing non-empty string item_id"
            )
        if item_id in seen_ids:
            raise EvalSetError(f"item #{pos}: duplicate item_id {item_id!r}")
        seen_ids.add(item_id)

        subset = raw.get("subset")
        if subset not in ALLOWED_CALIBRATION_SUBSETS:
            raise EvalSetError(
                f"item {item_id!r}: subset must be one of "
                f"{sorted(ALLOWED_CALIBRATION_SUBSETS)}, got {subset!r}"
            )

        label = raw.get("label")
        if not is_valid_taxonomy(label):
            raise EvalSetError(
                f"item {item_id!r}: label must be a taxonomy category "
                f"(ground truth is required for calibration), got {label!r}"
            )
        if subset == CAL_SUBSET_L6 and label not in L6_COVERED_CATEGORIES:
            raise EvalSetError(
                f"item {item_id!r}: l6_enriched items must be labeled with "
                f"an l6-covered category {sorted(L6_COVERED_CATEGORIES)}, "
                f"got {label!r}"
            )

        classifications = raw.get("classifications")
        if not isinstance(classifications, dict):
            raise EvalSetError(
                f"item {item_id!r}: classifications must be an object"
            )
        keys = set(classifications)
        if keys != declared_set:
            detail = []
            missing = sorted(declared_set - keys)
            unknown = sorted(keys - declared_set)
            if missing:
                detail.append(f"missing {missing}")
            if unknown:
                detail.append(f"unknown {unknown}")
            raise EvalSetError(
                f"item {item_id!r}: classifications keys must be exactly "
                f"the declared lineage ids ({'; '.join(detail)})"
            )
        confidences = raw.get("confidences")
        if not isinstance(confidences, dict):
            raise EvalSetError(
                f"item {item_id!r}: confidences must be an object (raw "
                "confidence per recorded classification; the Platt/isotonic "
                "fits consume this channel)"
            )
        if set(confidences) - declared_set:
            raise EvalSetError(
                f"item {item_id!r}: unknown confidence keys "
                f"{sorted(set(confidences) - declared_set)}"
            )

        cls_record: Dict[str, Optional[str]] = {}
        conf_record: Dict[str, Optional[float]] = {}
        for lineage_id in declared:
            value = classifications[lineage_id]
            if value is not None and not is_valid_taxonomy(value):
                raise EvalSetError(
                    f"item {item_id!r}: {lineage_id} classification "
                    f"{value!r} is not in the taxonomy (record abstention "
                    "as null)"
                )
            conf = confidences.get(lineage_id)
            if value is None:
                if conf is not None:
                    raise EvalSetError(
                        f"item {item_id!r}: {lineage_id} abstained "
                        "(classification null) but carries a confidence; "
                        "confidences are only recorded for classifications"
                    )
                conf_record[lineage_id] = None
            else:
                if not isinstance(conf, (int, float)) or isinstance(
                    conf, bool
                ):
                    raise EvalSetError(
                        f"item {item_id!r}: {lineage_id} classification "
                        f"{value!r} requires a numeric confidence, got "
                        f"{conf!r}"
                    )
                if not 0.0 <= float(conf) <= 1.0:
                    raise EvalSetError(
                        f"item {item_id!r}: {lineage_id} confidence "
                        f"{conf!r} outside [0, 1]"
                    )
                conf_record[lineage_id] = float(conf)
            cls_record[lineage_id] = value

        subset_counts[subset] += 1
        if label in L6_COVERED_CATEGORIES:
            l6_covered_count += 1
        labels.append(label)
        classifications_by_item.append(cls_record)
        confidences_by_item.append(conf_record)
        item_ids.append(item_id)

    total = len(item_ids)
    if total < MIN_CALIBRATION_ITEMS:
        raise EvalSetError(
            f"calibration eval set has {total} items; spec §3.2 "
            f"requires >= {MIN_CALIBRATION_ITEMS}"
        )
    if l6_covered_count < MIN_L6_COVERED_ITEMS:
        raise EvalSetError(
            f"calibration eval set has {l6_covered_count} items in the "
            f"l6-covered categories {sorted(L6_COVERED_CATEGORIES)}; "
            f"spec §3.2 requires >= {MIN_L6_COVERED_ITEMS}"
        )

    return CalibrationEvalSet(
        labels=labels,
        classifications_by_item=classifications_by_item,
        confidences_by_item=confidences_by_item,
        item_ids=item_ids,
        subset_counts=subset_counts,
        l6_covered_count=l6_covered_count,
        source="operator",
        marker=None,
        path=manifest_path,
        validation_set_hash=hashlib.sha256(raw_bytes).hexdigest(),
        provenance=(
            dict(payload["provenance"])
            if isinstance(payload.get("provenance"), dict)
            else None
        ),
    )


def synthetic_calibration_eval_set(
    n_items: int = SYNTHETIC_CALIBRATION_ITEM_COUNT,
) -> CalibrationEvalSet:
    """Deterministic CI fallback, marked
    ``synthetic_ci_not_production_calibration``.

    Mirrors the independence CI fallback philosophy: it exercises the
    calibration code path but can never certify — 20 items means no
    lineage can reach the 80-comparable minimum, so every lineage fails
    closed with ``insufficient_comparable_for_calibration``. Labels cycle
    the taxonomy; the l6-enriched tail cycles the l6-covered categories;
    recorded classifications are mutually uncorrelated across lineages
    (distinct multipliers mod 10) with deterministic raw confidences.
    """
    ids = declared_lineage_ids()
    categories = sorted(TAXONOMY)
    covered = sorted(L6_COVERED_CATEGORIES)
    n_items = int(n_items)
    n_l6 = min(6, n_items)
    labels: List[str] = []
    classifications_by_item: List[Dict[str, Optional[str]]] = []
    confidences_by_item: List[Dict[str, Optional[float]]] = []
    item_ids: List[str] = []
    for i in range(n_items):
        if i >= n_items - n_l6:
            label = covered[i % len(covered)]
            subset = CAL_SUBSET_L6
        else:
            label = categories[i % len(categories)]
            subset = CAL_SUBSET_GENERAL
        labels.append(label)
        classifications_by_item.append(
            {
                lineage_id: categories[(i * (k + 1) + k) % len(categories)]
                for k, lineage_id in enumerate(ids)
            }
        )
        confidences_by_item.append(
            {
                lineage_id: round(0.3 + 0.03 * ((i + k) % 20), 4)
                for k, lineage_id in enumerate(ids)
            }
        )
        item_ids.append(f"synthetic-cal-{i:04d}")
    subset_counts = {subset: 0 for subset in ALLOWED_CALIBRATION_SUBSETS}
    subset_counts[CAL_SUBSET_GENERAL] = n_items - n_l6
    subset_counts[CAL_SUBSET_L6] = n_l6
    payload = {
        "eval_set": "calibration",
        "items": [
            {
                "item_id": item_ids[i],
                "label": labels[i],
                "classifications": classifications_by_item[i],
                "confidences": confidences_by_item[i],
            }
            for i in range(n_items)
        ],
    }
    canonical = json.dumps(payload, sort_keys=True).encode("utf-8")
    return CalibrationEvalSet(
        labels=labels,
        classifications_by_item=classifications_by_item,
        confidences_by_item=confidences_by_item,
        item_ids=item_ids,
        subset_counts=subset_counts,
        l6_covered_count=sum(
            1 for label in labels if label in L6_COVERED_CATEGORIES
        ),
        source="synthetic",
        marker=SYNTHETIC_CALIBRATION_MARKER,
        path=None,
        validation_set_hash=hashlib.sha256(canonical).hexdigest(),
        provenance=None,
    )
