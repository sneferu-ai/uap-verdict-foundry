"""Empirical low-agreement validation between lineage pairs (spec §3.2,
DIS-3/OBL-62 terminology).

Cohen's kappa measures inter-rater *agreement*, not statistical
independence. The product's claim is therefore: "low observed inter-lineage
agreement validated by kappa" combined with the structural modality
separation argument (11 of 21 pairs share zero input modality; see
``registry.pairs_with_zero_shared_input``).

Protocol per spec:
  * 21 pairs over the 7 lineages;
  * items where either lineage abstains (None classification) are excluded;
  * minimum 100 comparable items per pair (l4/l6/l7-enriched eval subsets
    guarantee this for the operator set);
  * bootstrap 99.5% CI with 10000 resamples (seeded, deterministic);
  * a pair passes only when the bootstrap upper CI < 0.2;
  * the report carries per-pair abstention rate and case difficulty
    distribution (spec §3.2 pt 6/7, AC-2) via the optional
    ``item_difficulties`` input channel.

Pure stdlib implementation: kappa is a closed-form confusion-matrix
computation; the bootstrap uses a seeded ``random.Random`` so repeated runs
on the same eval set produce the identical report.

Call forms:
  * statistics core: ``validate_independence(classifications_by_item, ...)``
    over plain dicts — the pure, dependency-free core used by tests;
  * spec shape (§3.2): ``validate_independence(registry,
    independence_eval_set)`` where ``registry`` is the
    ``registry.load_registry()`` result and ``independence_eval_set`` is an
    ``eval_set.IndependenceEvalSet``. The thin adapter takes lineage ids
    from the registry and classifications + difficulty channel from the
    eval set, then annotates the report with registry availability and
    eval-set provenance (including the
    ``synthetic_ci_not_production_independence`` marker when the CI
    fallback set was used).
"""
from __future__ import annotations

import math
import random
from itertools import combinations
from typing import Dict, List, Optional, Sequence

DEFAULT_KAPPA_THRESHOLD = 0.2
DEFAULT_CI_LEVEL = 0.995
DEFAULT_BOOTSTRAP_SAMPLES = 10000
DEFAULT_MIN_COMPARABLE = 100
DEFAULT_SEED = 20260821


def cohens_kappa(labels_a: Sequence, labels_b: Sequence) -> float:
    """Cohen's kappa for two label sequences of equal length.

    Categories are taken from the union of observed labels. Returns 1.0 for
    perfect agreement; returns 0.0 when chance agreement is 1.0 but
    observed agreement is not (kappa undefined — treated as no agreement
    beyond chance, fail-closed for our purposes).
    """
    if len(labels_a) != len(labels_b):
        raise ValueError("label sequences must have equal length")
    n = len(labels_a)
    if n == 0:
        raise ValueError("cannot compute kappa over zero items")

    categories = sorted(set(labels_a) | set(labels_b), key=str)
    index = {c: i for i, c in enumerate(categories)}
    k = len(categories)
    matrix = [[0] * k for _ in range(k)]
    for a, b in zip(labels_a, labels_b):
        matrix[index[a]][index[b]] += 1

    po = sum(matrix[i][i] for i in range(k)) / n
    pe = 0.0
    for i in range(k):
        row = sum(matrix[i][j] for j in range(k)) / n
        col = sum(matrix[j][i] for j in range(k)) / n
        pe += row * col
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def bootstrap_kappa_ci(
    labels_a: Sequence,
    labels_b: Sequence,
    n_resamples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    ci_level: float = DEFAULT_CI_LEVEL,
    seed: int = DEFAULT_SEED,
) -> Dict[str, float]:
    """Seeded nonparametric bootstrap CI for Cohen's kappa.

    Returns ``{"kappa", "ci_lower", "ci_upper", "n"}``. Deterministic for a
    fixed seed and input.
    """
    n = len(labels_a)
    if n == 0 or n != len(labels_b):
        raise ValueError("bootstrap requires equal-length non-empty sequences")
    rng = random.Random(seed)
    a = list(labels_a)
    b = list(labels_b)
    kappas: List[float] = []
    for _ in range(int(n_resamples)):
        idx = [rng.randrange(n) for _ in range(n)]
        try:
            kappas.append(
                cohens_kappa([a[i] for i in idx], [b[i] for i in idx])
            )
        except ValueError:
            continue
    if not kappas:
        raise ValueError("bootstrap produced no valid resamples")
    kappas.sort()
    m = len(kappas)
    alpha = (1.0 - ci_level) / 2.0
    lo_idx = min(m - 1, max(0, int(math.floor(alpha * m))))
    hi_idx = min(m - 1, max(0, int(math.ceil((1.0 - alpha) * m)) - 1))
    return {
        "kappa": cohens_kappa(a, b),
        "ci_lower": kappas[lo_idx],
        "ci_upper": kappas[hi_idx],
        "n": float(n),
    }


def _looks_like_eval_set(obj) -> bool:
    """Duck-type check for ``eval_set.IndependenceEvalSet`` (avoids a hard
    import so the statistics core stays dependency-free)."""
    return (
        obj is not None
        and hasattr(obj, "classifications_by_item")
        and hasattr(obj, "item_difficulties")
    )


def _registry_lineage_ids(registry) -> List[str]:
    entries = list(registry)
    if not entries:
        raise ValueError("registry is empty: no lineage pairs to validate")
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


def validate_independence(
    registry_or_classifications,
    independence_eval_set=None,
    *,
    lineage_ids: Optional[List[str]] = None,
    item_difficulties: Optional[Sequence[Optional[str]]] = None,
    min_comparable: int = DEFAULT_MIN_COMPARABLE,
    kappa_threshold: float = DEFAULT_KAPPA_THRESHOLD,
    ci_level: float = DEFAULT_CI_LEVEL,
    n_resamples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Run the pairwise low-agreement validation.

    Two call forms (spec §3.2):

    1. **Spec shape** — ``validate_independence(registry,
       independence_eval_set)``: ``registry`` is the
       ``registry.load_registry()`` result (lineage ids are taken from it
       in declaration order), ``independence_eval_set`` is an
       ``eval_set.IndependenceEvalSet`` supplying the recorded
       classifications and the difficulty channel. The returned report
       additionally carries a ``registry`` block (ids + unavailable
       implementations) and an ``eval_set`` provenance block (source, the
       ``synthetic_ci_not_production_independence`` marker when the CI
       fallback set was used, path, item count, subset counts). Passing
       ``lineage_ids``/``item_difficulties`` alongside the eval set is
       rejected: in the spec shape those channels come from the registry
       and the eval set.

    2. **Statistics core** — ``validate_independence(
       classifications_by_item, lineage_ids=None, *,
       item_difficulties=None, ...)``: ``classifications_by_item`` is one
       dict per eval item mapping lineage_id -> classification, where
       ``None`` means the lineage abstained (status ``non_comparable``) on
       that item and the item is excluded for that pair.

    ``item_difficulties`` (core form): optional parallel list with one
    difficulty label per eval item (e.g. ``"hard"``, ``"ambiguous"``,
    ``"easy"``, or subset tags such as ``"l6_enriched"``); ``None`` entries are counted as
    ``"unlabeled"``. This is the difficulty channel required by spec §3.2
    pt 6 (case difficulty distribution in the report) and AC-2 (the
    l4/l6/l7-enriched / >=30%-hard composition must be reportable by the
    eval-set driver). When omitted, the difficulty distribution fields are
    ``None``. Length must equal ``len(classifications_by_item)``.

    Returns an IndependenceReport dict:
      {
        "pairs": [{"a", "b", "comparable", "total_items", "excluded",
                   "abstention_rate", "difficulty_distribution",
                   "kappa", "ci_lower", "ci_upper", "passed", "reason"}],
        "pair_count": int, "all_passed": bool, "failed_pairs": [...],
        "total_items": int, "difficulty_distribution": {label: count} | None,
        "min_comparable": int,
        "kappa_threshold": float, "ci_level": float, "n_resamples": int,
      }

    Per-pair ``abstention_rate`` = excluded / total_items, where an item is
    excluded for the pair when either lineage abstained (AC-2: per-pair
    abstention rates reported). Per-pair ``difficulty_distribution`` counts
    difficulty labels over that pair's comparable items only; the
    report-level ``difficulty_distribution`` counts over all eval items so
    the >=30% hard/ambiguous general-item composition (§3.2 pt 7) is
    auditable.

    A pair passes only when it has >= min_comparable comparable items AND
    its bootstrap upper CI < kappa_threshold. Insufficient comparable items
    is a failure (fail closed), not a skip.
    """
    if _looks_like_eval_set(independence_eval_set):
        # Spec shape: validate_independence(registry, independence_eval_set)
        if lineage_ids is not None or item_difficulties is not None:
            raise ValueError(
                "registry-form validate_independence(registry, eval_set) "
                "takes lineage ids and difficulty labels from the registry "
                "and the eval set; do not pass them separately"
            )
        eval_set = independence_eval_set
        ids = _registry_lineage_ids(registry_or_classifications)
        unavailable = [
            str(getattr(entry, "lineage_id"))
            for entry in registry_or_classifications
            if getattr(entry, "available", True) is False
        ]
        report = validate_independence(
            eval_set.classifications_by_item,
            lineage_ids=ids,
            item_difficulties=getattr(eval_set, "item_difficulties", None),
            min_comparable=min_comparable,
            kappa_threshold=kappa_threshold,
            ci_level=ci_level,
            n_resamples=n_resamples,
            seed=seed,
        )
        path = getattr(eval_set, "path", None)
        report["registry"] = {"lineage_ids": ids, "unavailable": unavailable}
        report["eval_set"] = {
            "source": str(getattr(eval_set, "source", "unknown")),
            "marker": getattr(eval_set, "marker", None),
            "path": str(path) if path is not None else None,
            "items": len(eval_set.classifications_by_item),
            "subset_counts": dict(getattr(eval_set, "subset_counts", {}) or {}),
            "validation_set_hash": getattr(
                eval_set, "validation_set_hash", None
            ),
            "provenance": getattr(eval_set, "provenance", None),
        }
        return report

    # Statistics core: first argument is the classification matrix; the
    # legacy second positional carries the lineage-id list.
    classifications_by_item = registry_or_classifications
    if lineage_ids is None and independence_eval_set is not None:
        lineage_ids = independence_eval_set

    total_items = len(classifications_by_item)
    if item_difficulties is not None:
        item_difficulties = list(item_difficulties)
        if len(item_difficulties) != total_items:
            raise ValueError(
                "item_difficulties must have one label per eval item: "
                f"{len(item_difficulties)} != {total_items}"
            )

    if lineage_ids is None:
        seen = set()
        for item in classifications_by_item:
            seen.update(item.keys())
        lineage_ids = sorted(seen)
    lineage_ids = list(lineage_ids)

    def difficulty_counts(labels: Sequence[Optional[str]]) -> Dict[str, int]:
        dist: Dict[str, int] = {}
        for label in labels:
            key = "unlabeled" if label is None else str(label)
            dist[key] = dist.get(key, 0) + 1
        return dist

    report_difficulty: Optional[Dict[str, int]] = (
        difficulty_counts(item_difficulties)
        if item_difficulties is not None
        else None
    )

    pair_reports: List[dict] = []
    failed: List[str] = []
    for a, b in combinations(lineage_ids, 2):
        labels_a: List[str] = []
        labels_b: List[str] = []
        comparable_difficulties: List[Optional[str]] = []
        excluded = 0
        for pos, item in enumerate(classifications_by_item):
            va = item.get(a)
            vb = item.get(b)
            if va is None or vb is None:
                excluded += 1  # either lineage abstained -> excluded
                continue
            labels_a.append(va)
            labels_b.append(vb)
            if item_difficulties is not None:
                comparable_difficulties.append(item_difficulties[pos])
        pair_id = f"{a}:{b}"
        comparable = len(labels_a)
        abstention_rate = excluded / total_items if total_items else 0.0
        pair_difficulty: Optional[Dict[str, int]] = (
            difficulty_counts(comparable_difficulties)
            if item_difficulties is not None
            else None
        )
        pair_report = {
            "a": a,
            "b": b,
            "comparable": comparable,
            "total_items": total_items,
            "excluded": excluded,
            "abstention_rate": abstention_rate,
            "difficulty_distribution": pair_difficulty,
        }
        if comparable < min_comparable:
            pair_report.update(
                {
                    "kappa": None,
                    "ci_lower": None,
                    "ci_upper": None,
                    "passed": False,
                    "reason": (
                        f"insufficient_comparable: {comparable} < "
                        f"{min_comparable}"
                    ),
                }
            )
            pair_reports.append(pair_report)
            failed.append(pair_id)
            continue
        stats = bootstrap_kappa_ci(
            labels_a,
            labels_b,
            n_resamples=n_resamples,
            ci_level=ci_level,
            seed=seed,
        )
        passed = stats["ci_upper"] < kappa_threshold
        pair_report.update(
            {
                "kappa": stats["kappa"],
                "ci_lower": stats["ci_lower"],
                "ci_upper": stats["ci_upper"],
                "passed": passed,
                "reason": (
                    None
                    if passed
                    else f"upper_ci_{stats['ci_upper']:.4f}_not_below_"
                    f"{kappa_threshold}"
                ),
            }
        )
        pair_reports.append(pair_report)
        if not passed:
            failed.append(pair_id)

    return {
        "pairs": pair_reports,
        "pair_count": len(pair_reports),
        "all_passed": not failed,
        "failed_pairs": failed,
        "total_items": total_items,
        "difficulty_distribution": report_difficulty,
        "min_comparable": min_comparable,
        "kappa_threshold": kappa_threshold,
        "ci_level": ci_level,
        "n_resamples": n_resamples,
    }
