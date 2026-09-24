"""Lineage protocol: output contract, status values, runtime interface
(spec §3.2 ``LineageOutput`` contract, subprocess IPC contract).

``LineageOutput`` is the single JSON-serializable contract every lineage
implementation must produce, whether it runs in-process (tests) or inside a
sandboxed subprocess worker (production, FR-017). ``from_dict`` validates
status and taxonomy at the boundary so malformed worker output is rejected,
never coerced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from uapvf.lineages.taxonomy import is_valid_taxonomy

# Status values (spec §3.2).
STATUS_OK = "ok"
STATUS_NON_COMPARABLE = "non_comparable"
STATUS_UNAVAILABLE = "unavailable"
VALID_STATUSES = frozenset({STATUS_OK, STATUS_NON_COMPARABLE, STATUS_UNAVAILABLE})

# Abstention reason when a lineage ran but could not emit the 3 shared +
# 2 specific claim contract from its available pool (spec §3.2: abstain
# entirely, never fabricate claims).
REASON_INSUFFICIENT_EVIDENCE = "insufficient_evidence_for_claim_emission"

REQUIRED_SHARED_CLAIMS = 3
REQUIRED_SPECIFIC_CLAIMS = 2


class ProtocolError(ValueError):
    """A lineage output violates the LineageOutput contract."""


@dataclass
class EvidenceClaims:
    """Emitted evidence claims: exactly 3 shared + 2 specific when status is
    ``ok`` (see evidence_vocabulary.validate_claim_contract)."""

    shared: List[str] = field(default_factory=list)
    specific: List[str] = field(default_factory=list)

    def all_claims(self) -> List[str]:
        return list(self.shared) + list(self.specific)

    def to_dict(self) -> Dict[str, List[str]]:
        return {"shared": list(self.shared), "specific": list(self.specific)}

    @classmethod
    def from_dict(cls, data: Any) -> "EvidenceClaims":
        data = data or {}
        shared = data.get("shared") or []
        specific = data.get("specific") or []
        if not isinstance(shared, list) or not isinstance(specific, list):
            raise ProtocolError("evidence_claims.shared/specific must be lists")
        for claim in list(shared) + list(specific):
            if not isinstance(claim, str):
                raise ProtocolError("evidence claims must be strings")
        return cls(shared=list(shared), specific=list(specific))


@dataclass
class LineageOutput:
    """One lineage's verdict on one case (spec §3.2 JSON contract).

    ``classification`` is None when the lineage ran successfully but asserts
    no taxonomy category (OBL-13: ``classification: null, status: ok``) or
    when it abstained (``status: non_comparable``). Null classification
    contributes pair_agreement 0.0 but the lineage still counts as
    comparable for the all-lineages-comparable rigor condition.
    """

    lineage_id: str
    status: str
    classification: Optional[str] = None
    artifact_detected: bool = False
    evidence_claims: EvidenceClaims = field(default_factory=EvidenceClaims)
    confidence: float = 0.0
    defense_claims: List[str] = field(default_factory=list)
    rationale: str = ""
    abstention_reason: Optional[str] = None
    calibration: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    frame_indices_used: List[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ProtocolError(
                f"invalid status {self.status!r}; must be one of "
                f"{sorted(VALID_STATUSES)}"
            )
        if self.classification is not None and not is_valid_taxonomy(
            self.classification
        ):
            raise ProtocolError(
                f"classification {self.classification!r} is not in the "
                f"10-category lineage taxonomy"
            )
        if not isinstance(self.lineage_id, str) or not self.lineage_id:
            raise ProtocolError("lineage_id must be a non-empty string")

    def to_dict(self) -> Dict[str, Any]:
        """Canonical JSON-ready form (subprocess IPC: worker prints this to
        stdout as a single JSON document)."""
        return {
            "lineage_id": self.lineage_id,
            "status": self.status,
            "classification": self.classification,
            "artifact_detected": bool(self.artifact_detected),
            "evidence_claims": self.evidence_claims.to_dict(),
            "confidence": float(self.confidence),
            "defense_claims": list(self.defense_claims),
            "rationale": self.rationale,
            "abstention_reason": self.abstention_reason,
            "calibration": dict(self.calibration),
            "provenance": dict(self.provenance),
            "frame_indices_used": list(self.frame_indices_used),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LineageOutput":
        """Parse and validate worker JSON. Raises ProtocolError on any
        contract violation (fail closed: bad output is never coerced)."""
        if not isinstance(data, dict):
            raise ProtocolError("lineage output must be a JSON object")
        try:
            return cls(
                lineage_id=data.get("lineage_id") or "",
                status=data.get("status") or "",
                classification=data.get("classification"),
                artifact_detected=bool(data.get("artifact_detected", False)),
                evidence_claims=EvidenceClaims.from_dict(
                    data.get("evidence_claims")
                ),
                confidence=float(data.get("confidence", 0.0) or 0.0),
                defense_claims=list(data.get("defense_claims") or []),
                rationale=str(data.get("rationale") or ""),
                abstention_reason=data.get("abstention_reason"),
                calibration=dict(data.get("calibration") or {}),
                provenance=dict(data.get("provenance") or {}),
                frame_indices_used=[
                    int(i) for i in (data.get("frame_indices_used") or [])
                ],
            )
        except (TypeError, ValueError) as exc:
            raise ProtocolError(f"malformed lineage output: {exc}") from exc


@runtime_checkable
class LineageRuntime(Protocol):
    """Interface every lineage implementation satisfies.

    Implementations run inside a per-case sandboxed subprocess worker
    (FR-017); ``run`` receives everything it needs through its arguments and
    the case directory, never through cross-case state.
    """

    lineage_id: str
    #: Number of uniformly sampled frames this lineage processes
    #: (0 for metadata-only / audio-only lineages; spec §3.2).
    frames_required: int

    def run(
        self,
        case_id: str,
        media_path: str,
        case_dir: str,
    ) -> LineageOutput:
        """Analyze one case. Must return a contract-valid LineageOutput or
        abstain with status ``non_comparable``; must never raise for
        low-quality input."""
        ...


def make_non_comparable(
    lineage_id: str, reason: str = REASON_INSUFFICIENT_EVIDENCE
) -> LineageOutput:
    """Convenience constructor for an abstention (spec §3.2 abstention
    triggers). Abstentions carry no classification and no claims."""
    return LineageOutput(
        lineage_id=lineage_id,
        status=STATUS_NON_COMPARABLE,
        classification=None,
        abstention_reason=reason,
    )
