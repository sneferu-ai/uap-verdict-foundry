"""Normative evidence vocabulary loader + deterministic claim selection
(spec §3.2: 14 shared + 20 specific claims, allowed emitters, 3+2 claim
emission contract, deterministic lowest-number selection rule).

The vocabulary is the single machine-readable authority on which claims a
lineage may emit. Selection is deterministic: given the same evidence
support set, the same claims are always chosen (lowest claim number first).
No synthetic or placeholder claims are ever produced: when the contract
cannot be satisfied the caller abstains with
``insufficient_evidence_for_claim_emission``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from uapvf.lineages.protocol import (
    REQUIRED_SHARED_CLAIMS,
    REQUIRED_SPECIFIC_CLAIMS,
    LineageOutput,
)

DEFAULT_VOCABULARY_PATH = (
    Path(__file__).resolve().parent.parent / "defaults" / "evidence_vocabulary.json"
)


class VocabularyError(ValueError):
    """The evidence vocabulary file is missing, malformed, or violates the
    14 shared + 20 specific structure."""


def load_vocabulary(path: Optional[Path] = None) -> dict:
    """Load and structurally validate the evidence vocabulary.

    Raises VocabularyError unless the file contains exactly 14 shared and
    20 specific claims with unique claim ids and non-empty emitter lists.
    """
    vocab_path = Path(path) if path else DEFAULT_VOCABULARY_PATH
    try:
        with open(vocab_path, "r", encoding="utf-8") as fh:
            vocab = json.load(fh)
    except FileNotFoundError as exc:
        raise VocabularyError(f"evidence vocabulary not found: {vocab_path}") from exc
    except json.JSONDecodeError as exc:
        raise VocabularyError(f"evidence vocabulary is not valid JSON: {exc}") from exc

    shared = vocab.get("shared_claims") or []
    specific = vocab.get("specific_claims") or []
    if len(shared) != 14:
        raise VocabularyError(f"expected 14 shared claims, found {len(shared)}")
    if len(specific) != 20:
        raise VocabularyError(f"expected 20 specific claims, found {len(specific)}")

    seen: Set[str] = set()
    for claim in list(shared) + list(specific):
        cid = claim.get("claim_id")
        if not cid or not isinstance(cid, str):
            raise VocabularyError(f"claim without claim_id: {claim!r}")
        if cid in seen:
            raise VocabularyError(f"duplicate claim_id: {cid}")
        seen.add(cid)
    for claim in shared:
        emitters = claim.get("allowed_emitters") or []
        if not emitters:
            raise VocabularyError(
                f"shared claim {claim['claim_id']} has no allowed emitters"
            )
    for claim in specific:
        if not claim.get("lineage"):
            raise VocabularyError(
                f"specific claim {claim['claim_id']} has no owning lineage"
            )
    vocab["_shared_by_id"] = {c["claim_id"]: c for c in shared}
    vocab["_specific_by_id"] = {c["claim_id"]: c for c in specific}
    return vocab


def shared_claim_emitters(vocab: dict) -> Dict[str, List[str]]:
    """claim_id -> sorted list of lineage ids allowed to emit it."""
    return {
        c["claim_id"]: sorted(c.get("allowed_emitters") or [])
        for c in vocab.get("shared_claims") or []
    }


def specific_claim_lineages(vocab: dict) -> Dict[str, str]:
    """claim_id -> owning lineage id."""
    return {
        c["claim_id"]: c["lineage"] for c in vocab.get("specific_claims") or []
    }


def available_shared_claims(vocab: dict, lineage_id: str) -> List[str]:
    """Shared claims *lineage_id* is an allowed emitter for, in normative
    number order (spec: selection picks lowest claim ID numbers)."""
    ordered = sorted(
        vocab.get("shared_claims") or [], key=lambda c: int(c.get("number", 0))
    )
    return [
        c["claim_id"]
        for c in ordered
        if lineage_id in (c.get("allowed_emitters") or [])
    ]


def available_specific_claims(vocab: dict, lineage_id: str) -> List[str]:
    """Specific claims owned by *lineage_id*, in normative number order."""
    ordered = sorted(
        vocab.get("specific_claims") or [], key=lambda c: int(c.get("number", 0))
    )
    return [c["claim_id"] for c in ordered if c.get("lineage") == lineage_id]


def select_claims(
    vocab: dict,
    lineage_id: str,
    supported_shared: Iterable[str],
    supported_specific: Iterable[str],
) -> Optional[Tuple[List[str], List[str]]]:
    """Deterministic claim selection (spec §3.2).

    From the intersection of the lineage's available pool and the
    evidence-supported set, pick the REQUIRED_SHARED_CLAIMS shared and
    REQUIRED_SPECIFIC_CLAIMS specific claims with the lowest claim numbers.
    Returns ``(shared, specific)`` or ``None`` when the contract cannot be
    satisfied — the caller must then abstain entirely
    (``insufficient_evidence_for_claim_emission``); fabricated claims are
    never emitted.
    """
    supported_shared_set = set(supported_shared)
    supported_specific_set = set(supported_specific)
    shared_candidates = [
        c
        for c in available_shared_claims(vocab, lineage_id)
        if c in supported_shared_set
    ]
    specific_candidates = [
        c
        for c in available_specific_claims(vocab, lineage_id)
        if c in supported_specific_set
    ]
    if (
        len(shared_candidates) < REQUIRED_SHARED_CLAIMS
        or len(specific_candidates) < REQUIRED_SPECIFIC_CLAIMS
    ):
        return None
    return (
        shared_candidates[:REQUIRED_SHARED_CLAIMS],
        specific_candidates[:REQUIRED_SPECIFIC_CLAIMS],
    )


def validate_claim_contract(output: LineageOutput, vocab: dict) -> List[str]:
    """Check the claim emission contract for one output (spec §3.2
    ``test_claim_emission_contract`` + ``test_defense_claims_subset``).

    Returns a list of violation strings (empty = valid):
      * status ``ok`` requires exactly 3 shared + 2 specific claims;
      * status ``non_comparable``/``unavailable`` must carry none;
      * every claim must be in the lineage's available pool;
      * ``defense_claims`` must be a subset of the emitted claims.
    """
    errors: List[str] = []
    shared = list(output.evidence_claims.shared)
    specific = list(output.evidence_claims.specific)

    if output.status == "ok":
        if len(shared) != REQUIRED_SHARED_CLAIMS:
            errors.append(
                f"{output.lineage_id}: expected {REQUIRED_SHARED_CLAIMS} shared "
                f"claims, found {len(shared)}"
            )
        if len(specific) != REQUIRED_SPECIFIC_CLAIMS:
            errors.append(
                f"{output.lineage_id}: expected {REQUIRED_SPECIFIC_CLAIMS} "
                f"specific claims, found {len(specific)}"
            )
    elif shared or specific:
        errors.append(
            f"{output.lineage_id}: status {output.status} must not carry "
            f"evidence claims"
        )

    allowed_shared = set(available_shared_claims(vocab, output.lineage_id))
    allowed_specific = set(available_specific_claims(vocab, output.lineage_id))
    for claim in shared:
        if claim not in allowed_shared:
            errors.append(
                f"{output.lineage_id}: shared claim {claim!r} is not in its "
                f"allowed emitter pool"
            )
    for claim in specific:
        if claim not in allowed_specific:
            errors.append(
                f"{output.lineage_id}: specific claim {claim!r} is not owned "
                f"by this lineage"
            )

    emitted = set(shared) | set(specific)
    for claim in output.defense_claims:
        if claim not in emitted:
            errors.append(
                f"{output.lineage_id}: defense claim {claim!r} is not a "
                f"subset of its emitted evidence claims"
            )
    return errors
