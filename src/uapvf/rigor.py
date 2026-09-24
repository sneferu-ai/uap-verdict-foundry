"""Rigor engine: classification-gated refutation-consistent concordance with
positive evidence overlap (spec §3.3, DIS-2/OBL-48/63/93/101/102).

Metric, per pair (i, j) of the 7 lineages:

  * either classification null          -> 0.0 (null = not comparable)
  * different classifications           -> 0.0
  * equal classifications, and:
      - no refutation-rule violation between i's specific claims and j's
        classification (and symmetrically),
      - no structural-consistency violation between their shared claims,
      - no within-lineage contradiction in either output (a refuted lineage
        scores 0.0 against everyone),
    then:
      - at least one shared evidence claim (positive overlap) -> 1.0
      - disjoint claim sets                                   -> 0.5

``agreement_fraction = sum(pair_agreement) / C(n, 2)`` (21 pairs for the 7
lineages). Per-lineage concordance ``c_i`` is the mean of i's pair scores;
``variance`` is the *population variance* of the ``c_i`` (OBL-102: "variance
below 15%" means stddev^2 < 0.15, not stddev < 0.15).

The six rigor conditions and which are required per runtime come from
``defaults/rigor_config.json``: live [1,2,3,4,5,6], mock -> blocked. Any
runtime the table does not name (including the removed ``simulation``
runtime) is blocked fail-closed with reason ``runtime_blocked``. In live
mode missing operator eval sets leave conditions 4/5 False and rigor
blocked — the product never claims certification it has not validated
(fail closed).

Condition 4/5 wiring: :func:`evaluate_operator_gates` derives the two
booleans from the stored certification artifacts via
:func:`validate_stored_independence_report` and
:func:`validate_stored_calibration`, which re-check the stored documents
against the ``rigor_config.json`` protocol parameters (resample count,
CI level, comparable minimums, operator eval-set provenance) rather than
trusting them — a smoke-run or synthetic-marker report can never satisfy
live rigor.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from uapvf.lineages.protocol import (
    STATUS_OK,
    LineageOutput,
)
from uapvf.lineages.registry import declared_lineage_ids

DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
DEFAULT_RIGOR_CONFIG_PATH = DEFAULTS_DIR / "rigor_config.json"

# Stored certification artifacts the live rigor gate re-checks (never
# trusted blindly — see validate_stored_independence_report /
# validate_stored_calibration). Paths fixed by spec §3.2 OBL-21.
INDEPENDENCE_REPORT_NAME = "lineage_independence_report.json"
CALIBRATION_DIR_NAME = "lineage_calibration"

# Spec §3.3 condition 4: kappa CI < 0.2 for ALL 21 pairs (C(7,2)).
EXPECTED_PAIR_COUNT = 21

RUNTIME_LIVE = "live"
RUNTIME_MOCK = "mock"

CONDITION_AGREEMENT = 1
CONDITION_PLURALITY = 2
CONDITION_ALL_COMPARABLE = 3
CONDITION_INDEPENDENCE = 4
CONDITION_CALIBRATION = 5
CONDITION_REFUTATION = 6

ASTRONOMICAL_QUALIFICATION_NOTE = (
    "Lineages identified astronomical characteristics; astronomical battery "
    "adapter checked for known celestial objects and found no match in the "
    "viewing direction."
)


class RigorConfigError(ValueError):
    """Rigor configuration or refutation rules are missing/malformed."""


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_rigor_config(path: Optional[Path] = None) -> dict:
    """Load ``rigor_config.json`` (bundled default unless *path* given)."""
    cfg_path = Path(path) if path else DEFAULT_RIGOR_CONFIG_PATH
    try:
        with open(cfg_path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except FileNotFoundError as exc:
        raise RigorConfigError(f"rigor config not found: {cfg_path}") from exc
    except json.JSONDecodeError as exc:
        raise RigorConfigError(f"rigor config is not valid JSON: {exc}") from exc
    for key in (
        "agreement_fraction_threshold",
        "variance_threshold",
        "min_plurality_count",
        "rigor_conditions",
    ):
        if key not in config:
            raise RigorConfigError(f"rigor config missing key: {key}")
    return config


def _resolve_defaults_path(candidate: str) -> Path:
    """Resolve a config-referenced path: absolute/relative-to-cwd first,
    then the bundled ``defaults/`` directory (matched by basename)."""
    p = Path(candidate)
    if p.is_absolute() and p.exists():
        return p
    if p.exists():
        return p
    bundled = DEFAULTS_DIR / p.name
    if bundled.exists():
        return bundled
    raise RigorConfigError(f"cannot resolve rules path: {candidate}")


def load_refutation_rules(
    *, path: Optional[Path] = None, config: Optional[dict] = None
) -> dict:
    """Load ``refutation_rules.json``. Explicit *path* wins; otherwise the
    ``refutation_rules_path`` entry of *config* (or the bundled default)."""
    if path is not None:
        rules_path = Path(path)
    else:
        config = config or load_rigor_config()
        candidate = config.get("refutation_rules_path")
        if candidate:
            rules_path = _resolve_defaults_path(candidate)
        else:
            rules_path = DEFAULTS_DIR / "refutation_rules.json"
    try:
        with open(rules_path, "r", encoding="utf-8") as fh:
            rules = json.load(fh)
    except FileNotFoundError as exc:
        raise RigorConfigError(f"refutation rules not found: {rules_path}") from exc
    except json.JSONDecodeError as exc:
        raise RigorConfigError(f"refutation rules not valid JSON: {exc}") from exc
    for key in (
        "claim_classification_inconsistencies",
        "structural_inconsistencies",
        "specific_claim_contradictions",
        "defense_resolutions",
    ):
        if key not in rules:
            raise RigorConfigError(f"refutation rules missing key: {key}")
    return rules


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def population_variance(values: Sequence[float]) -> float:
    """Population variance (stddev^2) — the spec's variance statistic
    (OBL-102). Empty input -> 0.0."""
    values = list(values)
    if not values:
        return 0.0
    return statistics.pvariance(values)


def passes_agreement_thresholds(
    agreement_fraction: float, variance: float, config: Optional[dict] = None
) -> bool:
    """Condition-1 thresholds with exact boundary semantics:
    ``agreement_fraction >= 0.5`` (0.5 passes) and ``variance < 0.15``
    (0.15 blocks)."""
    config = config or load_rigor_config()
    return (
        agreement_fraction >= float(config["agreement_fraction_threshold"])
        and variance < float(config["variance_threshold"])
    )


def _lineage_prefix(lineage_id: str) -> str:
    """'l5-trajectory' -> 'l5' (rule files key lineages by prefix)."""
    return lineage_id.split("-", 1)[0]


def _inconsistency_table(rules: dict) -> Dict[str, Set[str]]:
    return {
        entry["claim"]: set(entry.get("inconsistent_with") or [])
        for entry in rules["claim_classification_inconsistencies"]
    }


# ---------------------------------------------------------------------------
# Refutation
# ---------------------------------------------------------------------------

# Challenge types. Only ``cross_classification_inconsistency`` is defensible;
# the other three always refute (spec §3.3: no defense for self-inconsistency,
# within-lineage contradiction, or structural contradiction).
CHALLENGE_CROSS = "cross_classification_inconsistency"
CHALLENGE_SELF = "self_classification_inconsistency"
CHALLENGE_WITHIN = "within_lineage_contradiction"
CHALLENGE_STRUCTURAL = "structural_inconsistency"


@dataclass
class RefutationChallenge:
    challenged_lineage: str
    challenging_lineage: str
    claim: str
    inconsistent_with: Optional[str]
    challenge_type: str
    resolved: bool = False
    defense_claim: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "challenged_lineage": self.challenged_lineage,
            "challenging_lineage": self.challenging_lineage,
            "claim": self.claim,
            "inconsistent_with": self.inconsistent_with,
            "challenge_type": self.challenge_type,
            "resolved": self.resolved,
            "defense_claim": self.defense_claim,
            "reason": self.reason,
        }


@dataclass
class RefutationReport:
    challenges: List[RefutationChallenge] = field(default_factory=list)
    refuted: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.refuted

    def to_dict(self) -> dict:
        return {
            "challenges": [c.to_dict() for c in self.challenges],
            "refuted": list(self.refuted),
            "passed": self.passed,
        }


def _structural_pairs(rules: dict) -> List[Tuple[str, str]]:
    pairs = []
    for entry in rules["structural_inconsistencies"]:
        pairs.append((entry["claim_a"], entry["claim_b"]))
        pairs.append((entry["claim_b"], entry["claim_a"]))
    return pairs


def _have_structural_conflict(shared_a: Set[str], shared_b: Set[str],
                              rules: dict) -> bool:
    for a, b in _structural_pairs(rules):
        if a in shared_a and b in shared_b:
            return True
    return False


def run_refutation(
    outputs: Sequence[LineageOutput], rules: Optional[dict] = None
) -> RefutationReport:
    """Deterministic structured cross-examination (spec §3.3 steps 1-4).

    1. Within-lineage contradictions (shared structural pairs + lineage
       specific-claim contradictions) — never defensible.
    2. Cross-lineage challenges: a lineage's specific claim inconsistent
       with a *different* classification asserted by another lineage —
       defensible via ``defense_claims`` + ``defense_resolutions``.
    3. Same-classification self-inconsistency: a specific claim
       inconsistent with the lineage's OWN classification — never
       defensible (OBL-20).
    4. Structural contradictions between two outputs' shared claims —
       challenges both, never defensible.
    """
    rules = rules or load_refutation_rules()
    inconsistent_with = _inconsistency_table(rules)
    structural_pairs = _structural_pairs(rules)
    specific_contradictions = rules["specific_claim_contradictions"]
    defense_resolutions = rules["defense_resolutions"]

    ordered = sorted(outputs, key=lambda o: o.lineage_id)
    by_id = {o.lineage_id: o for o in ordered}
    challenges: List[RefutationChallenge] = []
    seen: Set[tuple] = set()

    def add(challenge: RefutationChallenge) -> None:
        key = (
            challenge.challenge_type,
            challenge.challenged_lineage,
            challenge.challenging_lineage,
            challenge.claim,
            challenge.inconsistent_with,
        )
        if key in seen:
            return
        seen.add(key)
        challenges.append(challenge)

    # Step 1: within-lineage contradictions.
    for out in ordered:
        shared = set(out.evidence_claims.shared)
        specific = set(out.evidence_claims.specific)
        for a, b in structural_pairs:
            if a in shared and b in shared:
                add(
                    RefutationChallenge(
                        challenged_lineage=out.lineage_id,
                        challenging_lineage=out.lineage_id,
                        claim=f"{a}+{b}",
                        inconsistent_with=None,
                        challenge_type=CHALLENGE_WITHIN,
                        reason=(
                            "within-lineage structural contradiction: "
                            f"{a} vs {b}"
                        ),
                    )
                )
        for rule in specific_contradictions:
            if rule["lineage"] != _lineage_prefix(out.lineage_id):
                continue
            a, b = rule["claim_a"], rule["claim_b"]
            if a in specific and b in specific:
                add(
                    RefutationChallenge(
                        challenged_lineage=out.lineage_id,
                        challenging_lineage=out.lineage_id,
                        claim=f"{a}+{b}",
                        inconsistent_with=None,
                        challenge_type=CHALLENGE_WITHIN,
                        reason=rule.get("reason", "specific claim contradiction"),
                    )
                )

    # Steps 2-4: pairwise.
    for out_i, out_j in combinations(ordered, 2):
        cls_i, cls_j = out_i.classification, out_j.classification
        shared_i = set(out_i.evidence_claims.shared)
        shared_j = set(out_j.evidence_claims.shared)
        specific_i = set(out_i.evidence_claims.specific)
        specific_j = set(out_j.evidence_claims.specific)

        if cls_i is not None and cls_j is not None and cls_i != cls_j:
            # Step 2: cross-lineage challenges, both directions.
            for claim in sorted(specific_i - specific_j):
                if cls_j in inconsistent_with.get(claim, set()):
                    add(
                        RefutationChallenge(
                            challenged_lineage=out_i.lineage_id,
                            challenging_lineage=out_j.lineage_id,
                            claim=claim,
                            inconsistent_with=cls_j,
                            challenge_type=CHALLENGE_CROSS,
                            reason=(
                                f"claim {claim} inconsistent with "
                                f"challenger classification {cls_j}"
                            ),
                        )
                    )
            for claim in sorted(specific_j - specific_i):
                if cls_i in inconsistent_with.get(claim, set()):
                    add(
                        RefutationChallenge(
                            challenged_lineage=out_j.lineage_id,
                            challenging_lineage=out_i.lineage_id,
                            claim=claim,
                            inconsistent_with=cls_i,
                            challenge_type=CHALLENGE_CROSS,
                            reason=(
                                f"claim {claim} inconsistent with "
                                f"challenger classification {cls_i}"
                            ),
                        )
                    )

        if cls_i is not None and cls_i == cls_j:
            # Step 3: self-classification inconsistency (OBL-20). No defense.
            for claim in sorted(specific_i):
                if cls_i in inconsistent_with.get(claim, set()):
                    add(
                        RefutationChallenge(
                            challenged_lineage=out_i.lineage_id,
                            challenging_lineage=out_j.lineage_id,
                            claim=claim,
                            inconsistent_with=cls_i,
                            challenge_type=CHALLENGE_SELF,
                            reason=(
                                f"claim {claim} inconsistent with own "
                                f"classification {cls_i}"
                            ),
                        )
                    )
            for claim in sorted(specific_j):
                if cls_j in inconsistent_with.get(claim, set()):
                    add(
                        RefutationChallenge(
                            challenged_lineage=out_j.lineage_id,
                            challenging_lineage=out_i.lineage_id,
                            claim=claim,
                            inconsistent_with=cls_j,
                            challenge_type=CHALLENGE_SELF,
                            reason=(
                                f"claim {claim} inconsistent with own "
                                f"classification {cls_j}"
                            ),
                        )
                    )
            # Step 4: structural consistency between the pair's shared claims.
            for a, b in structural_pairs:
                if a in shared_i and b in shared_j:
                    for target, partner in (
                        (out_i.lineage_id, out_j.lineage_id),
                        (out_j.lineage_id, out_i.lineage_id),
                    ):
                        add(
                            RefutationChallenge(
                                challenged_lineage=target,
                                challenging_lineage=partner,
                                claim=f"{a}+{b}",
                                inconsistent_with=None,
                                challenge_type=CHALLENGE_STRUCTURAL,
                                reason=(
                                    f"structural contradiction {a} vs {b} "
                                    f"across pair"
                                ),
                            )
                        )

    # Step 5 (defense resolution): only cross challenges are defensible, and
    # only with a defense claim the challenged lineage actually emitted.
    for challenge in challenges:
        if challenge.challenge_type != CHALLENGE_CROSS:
            continue
        challenged = by_id.get(challenge.challenged_lineage)
        challenger = by_id.get(challenge.challenging_lineage)
        if challenged is None or challenger is None:
            continue
        emitted = set(challenged.evidence_claims.all_claims())
        for defense in sorted(set(challenged.defense_claims)):
            if defense not in emitted:
                continue  # not actually emitted -> cannot defend
            for resolution in defense_resolutions:
                if (
                    resolution["challenged_claim"] == challenge.claim
                    and resolution["defense_claim"] == defense
                    and challenged.classification
                    in resolution["defense_supports_challenged_classification"]
                    and challenger.classification
                    in resolution[
                        "defense_undermines_challenger_classification"
                    ]
                ):
                    challenge.resolved = True
                    challenge.defense_claim = defense
                    challenge.reason += f"; resolved by defense {defense}"
                    break
            if challenge.resolved:
                break

    refuted = sorted(
        {c.challenged_lineage for c in challenges if not c.resolved}
    )
    # Deterministic ordering of the challenge list.
    challenges.sort(
        key=lambda c: (
            c.challenge_type,
            c.challenged_lineage,
            c.challenging_lineage,
            c.claim,
        )
    )
    return RefutationReport(challenges=challenges, refuted=refuted)


# ---------------------------------------------------------------------------
# Concordance
# ---------------------------------------------------------------------------


def _pairwise_scores(
    outputs: Sequence[LineageOutput],
    refutation: RefutationReport,
    rules: dict,
) -> Tuple[Dict[str, float], List[LineageOutput]]:
    """Score every pair per the §3.3 metric. Returns (pair_scores keyed
    'id_a:id_b', outputs sorted by lineage_id)."""
    inconsistent_with = _inconsistency_table(rules)
    refuted = set(refutation.refuted)
    ordered = sorted(outputs, key=lambda o: o.lineage_id)
    scores: Dict[str, float] = {}
    for out_i, out_j in combinations(ordered, 2):
        key = f"{out_i.lineage_id}:{out_j.lineage_id}"
        scores[key] = _pair_agreement(out_i, out_j, refuted, inconsistent_with, rules)
    return scores, ordered


def _pair_agreement(
    out_i: LineageOutput,
    out_j: LineageOutput,
    refuted: Set[str],
    inconsistent_with: Dict[str, Set[str]],
    rules: dict,
) -> float:
    if out_i.lineage_id in refuted or out_j.lineage_id in refuted:
        return 0.0
    cls_i, cls_j = out_i.classification, out_j.classification
    if cls_i is None or cls_j is None:
        return 0.0  # null = not comparable, does not contribute
    if cls_i != cls_j:
        return 0.0
    # Same classification: refutation-consistency gates.
    for claim in out_i.evidence_claims.specific:
        if cls_j in inconsistent_with.get(claim, set()):
            return 0.0
    for claim in out_j.evidence_claims.specific:
        if cls_i in inconsistent_with.get(claim, set()):
            return 0.0
    shared_i = set(out_i.evidence_claims.shared)
    shared_j = set(out_j.evidence_claims.shared)
    if _have_structural_conflict(shared_i, shared_j, rules):
        return 0.0
    # Positive evidence-claim overlap (DIS-2/OBL-63): full agreement requires
    # at least one common evidence claim; disjoint claim sets -> partial 0.5.
    claims_i = set(out_i.evidence_claims.all_claims())
    claims_j = set(out_j.evidence_claims.all_claims())
    if claims_i & claims_j:
        return 1.0
    return 0.5


@dataclass
class RigorResult:
    agreement_fraction: float
    variance: float
    plurality: Optional[str]
    plurality_count: int
    pair_agreements: Dict[str, float]
    per_lineage_concordance: Dict[str, float]
    mean_pair_confidence: Optional[float]
    mean_pair_confidence_note: Optional[str]
    conditions: Dict[int, bool]
    required_conditions: object  # list[int] or "blocked"
    runtime: str
    passed: bool
    reason: Optional[str]
    refutation: RefutationReport
    independence_validated: bool
    calibration_valid: bool
    pair_count: int

    def to_dict(self) -> dict:
        return {
            "agreement_fraction": self.agreement_fraction,
            "variance": self.variance,
            "plurality": self.plurality,
            "plurality_count": self.plurality_count,
            "pair_agreements": dict(self.pair_agreements),
            "per_lineage_concordance": dict(self.per_lineage_concordance),
            "mean_pair_confidence": self.mean_pair_confidence,
            "mean_pair_confidence_note": self.mean_pair_confidence_note,
            "conditions": {str(k): v for k, v in self.conditions.items()},
            "required_conditions": self.required_conditions,
            "runtime": self.runtime,
            "passed": self.passed,
            "reason": self.reason,
            "refutation": self.refutation.to_dict(),
            "independence_validated": self.independence_validated,
            "calibration_valid": self.calibration_valid,
            "pair_count": self.pair_count,
        }


def compute_concordance(
    outputs: Sequence[LineageOutput],
    *,
    rules: Optional[dict] = None,
    config: Optional[dict] = None,
    runtime: str = RUNTIME_LIVE,
    independence_validated: bool = False,
    calibration_valid: bool = False,
    expected_lineage_count: int = 7,
    refutation: Optional[RefutationReport] = None,
) -> RigorResult:
    """Score lineage outputs with the classification-gated
    refutation-consistent concordance metric and evaluate the six rigor
    conditions (spec §3.3).

    ``independence_validated`` / ``calibration_validated`` come from the
    operator-gated ``lineages validate`` / ``lineages calibrate`` runs; in
    live mode their absence blocks rigor (fail closed).
    """
    config = config or load_rigor_config()
    rules = rules or load_refutation_rules(config=config)
    refutation = refutation or run_refutation(outputs, rules=rules)

    pair_scores, ordered = _pairwise_scores(outputs, refutation, rules)
    n = len(ordered)
    pair_count = n * (n - 1) // 2
    agreement_fraction = (
        sum(pair_scores.values()) / pair_count if pair_count else 0.0
    )

    per_lineage: Dict[str, float] = {}
    for out in ordered:
        partner_scores = [
            score
            for key, score in pair_scores.items()
            if out.lineage_id in key.split(":")
        ]
        per_lineage[out.lineage_id] = (
            sum(partner_scores) / len(partner_scores) if partner_scores else 0.0
        )
    variance = population_variance(list(per_lineage.values()))

    counts = Counter(
        o.classification for o in ordered if o.classification is not None
    )
    if counts:
        # Deterministic tie-break: highest count, then alphabetical.
        plurality = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        plurality_count = counts[plurality]
    else:
        plurality = None
        plurality_count = 0

    agreeing = [
        (key, score) for key, score in pair_scores.items() if score > 0.0
    ]
    if agreeing:
        confidences = []
        for key, _score in agreeing:
            id_a, id_b = key.split(":")
            conf_a = next(o.confidence for o in ordered if o.lineage_id == id_a)
            conf_b = next(o.confidence for o in ordered if o.lineage_id == id_b)
            confidences.append(min(conf_a, conf_b))
        mean_pair_confidence: Optional[float] = sum(confidences) / len(confidences)
        confidence_note: Optional[str] = None
    else:
        mean_pair_confidence = None
        confidence_note = "no_agreeing_pairs"

    conditions = {
        CONDITION_AGREEMENT: passes_agreement_thresholds(
            agreement_fraction, variance, config
        ),
        CONDITION_PLURALITY: plurality_count
        >= int(config["min_plurality_count"]),
        CONDITION_ALL_COMPARABLE: bool(
            all(o.status == STATUS_OK for o in ordered)
            and n == expected_lineage_count
            and not refutation.refuted  # refuted -> non_comparable (§3.2)
        ),
        CONDITION_INDEPENDENCE: bool(independence_validated),
        CONDITION_CALIBRATION: bool(calibration_valid),
        CONDITION_REFUTATION: refutation.passed,
    }

    runtime_conditions = config["rigor_conditions"].get(runtime)
    if runtime_conditions == "blocked" or runtime_conditions is None:
        required: object = "blocked"
        passed = False
        reason = "runtime_blocked"
    else:
        required = list(runtime_conditions)
        failing = [c for c in required if not conditions[int(c)]]
        passed = not failing
        reason = (
            None
            if passed
            else "conditions_failed:" + ",".join(str(c) for c in failing)
        )

    return RigorResult(
        agreement_fraction=agreement_fraction,
        variance=variance,
        plurality=plurality,
        plurality_count=plurality_count,
        pair_agreements=pair_scores,
        per_lineage_concordance=per_lineage,
        mean_pair_confidence=mean_pair_confidence,
        mean_pair_confidence_note=confidence_note,
        conditions=conditions,
        required_conditions=required,
        runtime=runtime,
        passed=passed,
        reason=reason,
        refutation=refutation,
        independence_validated=bool(independence_validated),
        calibration_valid=bool(calibration_valid),
        pair_count=pair_count,
    )


# ---------------------------------------------------------------------------
# Conflict adjudication trace
# ---------------------------------------------------------------------------


@dataclass
class AdjudicationTrace:
    consensus: Optional[str]
    consensus_count: int
    entries: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "consensus": self.consensus,
            "consensus_count": self.consensus_count,
            "entries": list(self.entries),
        }


def adjudicate_conflicts(
    outputs: Sequence[LineageOutput],
    consensus: Optional[str],
    refutation: Optional[RefutationReport] = None,
    rules: Optional[dict] = None,
) -> AdjudicationTrace:
    """Document every lineage that does not join the plurality consensus:
    its classification, its pair scores, the challenges it is involved in,
    and its disposition (excluded by refutation vs retained dissenting vs
    abstained). Used by the disagreement-trace reporting (AC-16 failure-mode
    proof ``test_lineage_disagreement_trace``)."""
    rules = rules or load_refutation_rules()
    refutation = refutation or run_refutation(outputs, rules=rules)
    pair_scores, ordered = _pairwise_scores(outputs, refutation, rules)

    counts = Counter(
        o.classification for o in ordered if o.classification is not None
    )
    consensus_count = counts.get(consensus, 0) if consensus else 0

    entries: List[dict] = []
    for out in ordered:
        if out.classification == consensus:
            continue
        if out.lineage_id in refutation.refuted:
            disposition = "excluded_refuted"
        elif out.status != STATUS_OK:
            disposition = "abstained"
        else:
            disposition = "retained_dissenting"
        entry_challenges = [
            c.to_dict()
            for c in refutation.challenges
            if c.challenged_lineage == out.lineage_id
            or c.challenging_lineage == out.lineage_id
        ]
        entries.append(
            {
                "lineage_id": out.lineage_id,
                "status": out.status,
                "classification": out.classification,
                "pair_agreements": {
                    key: score
                    for key, score in pair_scores.items()
                    if out.lineage_id in key.split(":")
                },
                "challenges": entry_challenges,
                "disposition": disposition,
            }
        )
    return AdjudicationTrace(
        consensus=consensus, consensus_count=consensus_count, entries=entries
    )


# ---------------------------------------------------------------------------
# Final rigor finalization (cross-modal check)
# ---------------------------------------------------------------------------


@dataclass
class FinalRigorResult:
    passed: bool
    reason: Optional[str]
    cross_modal_conflict: bool
    battery_verdict: str
    lineage_plurality: Optional[str]
    battery_category: Optional[str]
    note: Optional[str]
    stage: str = "final"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "cross_modal_conflict": self.cross_modal_conflict,
            "battery_verdict": self.battery_verdict,
            "lineage_plurality": self.lineage_plurality,
            "battery_category": self.battery_category,
            "note": self.note,
            "stage": self.stage,
        }


def finalize_rigor(
    preliminary: RigorResult,
    battery_verdict: str,
    lineage_plurality: Optional[str],
    battery_category: Optional[str] = None,
) -> FinalRigorResult:
    """Cross-modal finalization (spec §3.3/§3.4/§3.5).

    A conflict exists when the battery identified a specific mundane object
    whose category differs from the lineage plurality classification (e.g.
    lineages say ``drone``, battery matched an ``aircraft``) — the final
    rigor is blocked. A battery verdict of ``no_mundane_match`` is not a
    conflict by itself. The astronomical qualification is honoured: when
    lineages classify ``astronomical`` and the battery returns
    ``no_mundane_match_astronomical_checked`` (checked the viewing
    direction, found no known celestial object), the result carries the
    qualification note without blocking.
    """
    cross_modal_conflict = (
        battery_category is not None
        and lineage_plurality is not None
        and battery_category != lineage_plurality
    )
    note: Optional[str] = None
    if (
        lineage_plurality == "astronomical"
        and battery_verdict == "no_mundane_match_astronomical_checked"
    ):
        note = ASTRONOMICAL_QUALIFICATION_NOTE

    if cross_modal_conflict:
        passed = False
        reason = (
            f"cross_modal_conflict: battery identified {battery_category!r} "
            f"but lineage plurality is {lineage_plurality!r}"
        )
    else:
        passed = preliminary.passed
        reason = preliminary.reason

    return FinalRigorResult(
        passed=passed,
        reason=reason,
        cross_modal_conflict=cross_modal_conflict,
        battery_verdict=battery_verdict,
        lineage_plurality=lineage_plurality,
        battery_category=battery_category,
        note=note,
    )


# ---------------------------------------------------------------------------
# Operator certification gates (conditions 4/5 wiring)
# ---------------------------------------------------------------------------
#
# Live rigor conditions 4 (independence_validated) and 5 (calibration_valid)
# are not booleans an operator toggles: they are derived, fail-closed, from
# the stored certification artifacts `uapvf lineages validate` /
# `uapvf lineages calibrate` produce (spec §3.2 OBL-21). The validators
# below re-check the stored documents against rigor_config.json protocol
# parameters rather than trusting them: a smoke-run report (sub-protocol
# resample count, wrong CI level, lowered comparable minimum) or a report
# produced over the marked synthetic CI fallback can never satisfy live
# rigor (spec §3.2 pt 8: conditions 4/5 become False, never a lesser
# "operator_gated" state).


def _require_config_keys(config: dict, keys: Sequence[str]) -> None:
    for key in keys:
        if key not in config:
            raise RigorConfigError(f"rigor config missing key: {key}")


def _sha256_file(path: Path) -> Optional[str]:
    """Return the SHA-256 of *path*, or ``None`` when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _sandbox_recorder_provenance_valid(value) -> bool:
    """Production certification must come from the blinded OS-sandbox run."""
    return (
        isinstance(value, dict)
        and value.get("recorder") == "uapvf_seven_lineage_sandbox"
        and value.get("labels_blinded_during_inference") is True
        and isinstance(value.get("collection_manifest_sha256"), str)
        and len(value["collection_manifest_sha256"]) == 64
    )


def validate_stored_independence_report(
    var_dir, config: Optional[dict] = None
) -> Tuple[bool, Optional[str]]:
    """Verify ``var/lineage_independence_report.json`` for live condition 4.

    Re-checks the stored report against ``rigor_config.json`` instead of
    trusting it: the report's protocol parameters must match the config
    (``n_resamples >= independence_bootstrap_samples``, ``ci_level ==
    independence_ci_level``, ``min_comparable >=
    independence_min_comparable_items``), the eval set must be the
    operator set (the ``synthetic_ci_not_production_independence`` marker
    is rejected), and every one of the 21 pairs must carry a bootstrap
    upper CI below ``independence_threshold`` with enough comparable
    items. Returns ``(valid, reason)``; ``reason`` is ``None`` iff valid.
    """
    config = config or load_rigor_config()
    _require_config_keys(
        config,
        (
            "independence_threshold",
            "independence_bootstrap_samples",
            "independence_ci_level",
            "independence_min_comparable_items",
        ),
    )
    path = Path(var_dir) / INDEPENDENCE_REPORT_NAME
    if not path.exists():
        return False, "independence_report_missing"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return False, "independence_report_invalid_json"
    if not isinstance(report, dict):
        return False, "independence_report_invalid_json"

    eval_set_info = report.get("eval_set") or {}
    if eval_set_info.get("marker") is not None:
        # Spec §3.2 pt 8: synthetic CI fallback is not production evidence.
        return False, "synthetic_eval_set_not_production"
    if eval_set_info.get("source") != "operator":
        return False, "eval_set_source_not_operator"
    # Protocol parameters: a sub-protocol certification run (e.g. a
    # 50-resample smoke run) must not satisfy live rigor.
    required_resamples = int(config["independence_bootstrap_samples"])
    required_ci_level = float(config["independence_ci_level"])
    required_min_comparable = int(config["independence_min_comparable_items"])
    threshold = float(config["independence_threshold"])

    n_resamples = report.get("n_resamples")
    if not isinstance(n_resamples, int) or n_resamples < required_resamples:
        return False, "protocol_mismatch:n_resamples"
    ci_level = report.get("ci_level")
    if not isinstance(ci_level, (int, float)) or abs(
        float(ci_level) - required_ci_level
    ) > 1e-9:
        return False, "protocol_mismatch:ci_level"
    min_comparable = report.get("min_comparable")
    if (
        not isinstance(min_comparable, int)
        or min_comparable < required_min_comparable
    ):
        return False, "protocol_mismatch:min_comparable"

    pairs = report.get("pairs")
    if report.get("pair_count") != EXPECTED_PAIR_COUNT or not isinstance(
        pairs, list
    ) or len(pairs) != EXPECTED_PAIR_COUNT:
        return False, "pair_count_mismatch"

    for pair in pairs:
        if not isinstance(pair, dict):
            return False, "pair_report_malformed"
        pair_id = f"{pair.get('a')}:{pair.get('b')}"
        comparable = pair.get("comparable")
        if (
            not isinstance(comparable, int)
            or comparable < required_min_comparable
        ):
            return False, f"pair_insufficient_comparable:{pair_id}"
        ci_upper = pair.get("ci_upper")
        if not isinstance(ci_upper, (int, float)) or ci_upper >= threshold:
            return False, f"pair_failed:{pair_id}"
        if pair.get("passed") is not True:
            return False, f"pair_failed:{pair_id}"
    if report.get("all_passed") is not True:
        return False, "report_not_all_passed"
    manifest_path = (
        Path(var_dir) / "operator" / "eval_set_independence" / "manifest.json"
    )
    manifest_hash = _sha256_file(manifest_path)
    if manifest_hash is None:
        return False, "independence_manifest_missing"
    if eval_set_info.get("validation_set_hash") != manifest_hash:
        return False, "independence_manifest_hash_mismatch"
    if not _sandbox_recorder_provenance_valid(eval_set_info.get("provenance")):
        return False, "independence_recorder_provenance_invalid"
    return True, None


def validate_stored_calibration(
    var_dir, config: Optional[dict] = None
) -> Tuple[bool, Optional[str]]:
    """Verify ``var/lineage_calibration/{lineage_id}.json`` for live
    condition 5 (all 7 lineages calibrated, accuracy >= 0.60, >= 80
    comparable — thresholds from ``rigor_config.json``).

    Every declared lineage must have a stored record that matches its
    filename, was produced over the operator eval set (the
    ``synthetic_ci_not_production_calibration`` marker is rejected), and
    carries ``calibrated: true`` with accuracy/comparable counts meeting
    the configured thresholds. Returns ``(valid, reason)``.
    """
    config = config or load_rigor_config()
    _require_config_keys(
        config, ("calibration_accuracy_threshold", "calibration_min_comparable")
    )
    accuracy_threshold = float(config["calibration_accuracy_threshold"])
    min_comparable = int(config["calibration_min_comparable"])
    cal_dir = Path(var_dir) / CALIBRATION_DIR_NAME
    eval_set_infos = []
    for lineage_id in declared_lineage_ids():
        path = cal_dir / f"{lineage_id}.json"
        if not path.exists():
            return False, f"calibration_missing:{lineage_id}"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return False, f"calibration_invalid_json:{lineage_id}"
        if not isinstance(record, dict):
            return False, f"calibration_invalid_json:{lineage_id}"
        if record.get("lineage_id") != lineage_id:
            return False, f"calibration_lineage_mismatch:{lineage_id}"
        eval_set_info = record.get("eval_set") or {}
        if eval_set_info.get("marker") is not None:
            return False, f"calibration_synthetic:{lineage_id}"
        if eval_set_info.get("source") != "operator":
            return False, f"calibration_source_not_operator:{lineage_id}"
        eval_set_infos.append((lineage_id, eval_set_info))
        if record.get("calibrated") is not True:
            return False, f"calibration_not_calibrated:{lineage_id}"
        accuracy = record.get("accuracy")
        if (
            not isinstance(accuracy, (int, float))
            or isinstance(accuracy, bool)
            or float(accuracy) < accuracy_threshold
        ):
            return False, f"calibration_accuracy_below_threshold:{lineage_id}"
        comparable = record.get("comparable_count")
        if (
            not isinstance(comparable, int)
            or isinstance(comparable, bool)
            or comparable < min_comparable
        ):
            return False, f"calibration_insufficient_comparable:{lineage_id}"
    manifest_path = Path(var_dir) / "operator" / "eval_set" / "manifest.json"
    manifest_hash = _sha256_file(manifest_path)
    if manifest_hash is None:
        return False, "calibration_manifest_missing"
    for lineage_id, eval_set_info in eval_set_infos:
        if eval_set_info.get("validation_set_hash") != manifest_hash:
            return False, f"calibration_manifest_hash_mismatch:{lineage_id}"
        if not _sandbox_recorder_provenance_valid(
            eval_set_info.get("provenance")
        ):
            return False, f"calibration_recorder_provenance_invalid:{lineage_id}"
    return True, None


@dataclass
class OperatorGates:
    """Live-mode condition 4/5 inputs derived from stored certification
    artifacts — the fail-closed wiring for missing operator eval sets:
    absent or non-conforming artifacts leave both gates False, blocking
    live rigor (spec §3.2 pt 8 / §3.3 condition table)."""

    independence_validated: bool
    calibration_valid: bool
    independence_reason: Optional[str] = None
    calibration_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "independence_validated": self.independence_validated,
            "calibration_valid": self.calibration_valid,
            "independence_reason": self.independence_reason,
            "calibration_reason": self.calibration_reason,
        }


def evaluate_operator_gates(
    var_dir, config: Optional[dict] = None
) -> OperatorGates:
    """Derive the condition 4/5 booleans for ``compute_concordance`` from
    the artifacts stored under *var_dir*.

    Live-mode callers pass the two booleans into ``compute_concordance``;
    the mock runtime is blocked outright (and any runtime the shipped
    ``rigor_conditions`` table does not name is blocked fail-closed), so
    these gates matter exactly where the spec says they do.
    """
    config = config or load_rigor_config()
    ind_valid, ind_reason = validate_stored_independence_report(
        var_dir, config
    )
    cal_valid, cal_reason = validate_stored_calibration(var_dir, config)
    return OperatorGates(
        independence_validated=ind_valid,
        calibration_valid=cal_valid,
        independence_reason=ind_reason,
        calibration_reason=cal_reason,
    )
