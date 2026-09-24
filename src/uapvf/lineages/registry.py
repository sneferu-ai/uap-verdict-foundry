"""Lineage registry: the seven declared lineages across four modality
classes (spec §3.2 table) plus lazy loading of their implementations.

``LINEAGE_DECLARATIONS`` is normative: ids, modality classes, sampling
budgets, and the structural separation basis used by the independence
argument. 11 of the 21 lineage pairs share zero input modality (every pair
touching l4-metadata or l7-audio); the remaining 10 pairs share frame input
and rely on empirical low-agreement validation (Cohen's kappa) — see
``independence.py``.

Implementations live in ``l1_photometric`` … ``l7_audio`` modules exposing
a ``Lineage`` class. ``load_registry()`` imports what exists and marks the
rest unavailable — a missing implementation is never silently substituted
(fail closed: unavailable lineages block live rigor).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, List, Optional

# Modality classes (spec §3.2: vision luminance/frequency/shape, non-vision
# metadata + audio, physics, ML vision).
MODALITY_VISION = "vision"
MODALITY_NON_VISION = "non_vision"
MODALITY_PHYSICS = "physics"
MODALITY_ML_VISION = "ml_vision"

# Input substrate per lineage, for the structural separation argument.
INPUT_FRAMES = "frames"
INPUT_METADATA = "metadata"
INPUT_AUDIO = "audio"

LINEAGE_DECLARATIONS = [
    {
        "lineage_id": "l1-photometric",
        "module": "uapvf.lineages.l1_photometric",
        "modality": MODALITY_VISION,
        "modality_detail": "luminance",
        "architecture": "Hand-crafted luminance profile",
        "training_corpus": "none",
        "input_modality": INPUT_FRAMES,
        "frames": 10,
        "independence_basis": "Different feature space; low kappa confirmed",
    },
    {
        "lineage_id": "l2-spectral",
        "module": "uapvf.lineages.l2_spectral",
        "modality": MODALITY_VISION,
        "modality_detail": "frequency",
        "architecture": "Hand-crafted FFT analysis",
        "training_corpus": "none",
        "input_modality": INPUT_FRAMES,
        "frames": 10,
        "independence_basis": "Different feature space; low kappa confirmed",
    },
    {
        "lineage_id": "l3-geometric",
        "module": "uapvf.lineages.l3_geometric",
        "modality": MODALITY_VISION,
        "modality_detail": "shape",
        "architecture": "Hand-crafted geometric/perspective",
        "training_corpus": "none",
        "input_modality": INPUT_FRAMES,
        "frames": 10,
        "independence_basis": "Different feature space; low kappa confirmed",
    },
    {
        "lineage_id": "l4-metadata",
        "module": "uapvf.lineages.l4_metadata",
        "modality": MODALITY_NON_VISION,
        "modality_detail": "metadata",
        "architecture": "EXIF, file structure, timestamps",
        "training_corpus": "none",
        "input_modality": INPUT_METADATA,
        "frames": 0,
        "independence_basis": "Zero pixel overlap; different input modality",
    },
    {
        "lineage_id": "l5-trajectory",
        "module": "uapvf.lineages.l5_trajectory",
        "modality": MODALITY_PHYSICS,
        "modality_detail": "motion dynamics",
        "architecture": "Motion dynamics with physical constraints",
        "training_corpus": "none",
        "input_modality": INPUT_FRAMES,
        "frames": 60,
        "independence_basis": "Different paradigm; requires video",
    },
    {
        "lineage_id": "l6-resnet50",
        "module": "uapvf.lineages.l6_resnet50",
        "modality": MODALITY_ML_VISION,
        "modality_detail": "cnn",
        "architecture": "ResNet-50 ONNX (ImageNet-1k)",
        "training_corpus": "ImageNet-1k (public)",
        "input_modality": INPUT_FRAMES,
        "frames": 5,
        "independence_basis": "Different architecture and corpus",
    },
    {
        "lineage_id": "l7-audio",
        "module": "uapvf.lineages.l7_audio",
        "modality": MODALITY_NON_VISION,
        "modality_detail": "audio",
        "architecture": "Audio spectrogram analysis",
        "training_corpus": "none",
        "input_modality": INPUT_AUDIO,
        "frames": 0,
        "independence_basis": "Zero visual overlap; different input modality",
    },
]

EXPECTED_LINEAGE_COUNT = 7

# Modules all lineage implementations may share (spec §3.2 static-analysis
# no-shared-module rule, OBL-44). Anything else imported by two or more
# lineage modules is a violation.
ALLOWED_SHARED_MODULES = frozenset(
    {
        "uapvf.lineages.protocol",
        "uapvf.lineages.subprocess_worker",
        "uapvf.lineages.taxonomy",
        "uapvf.lineages.evidence_vocabulary",
        "uapvf.lineages.frame_sampling",
    }
)


@dataclass
class RegistryEntry:
    """One declared lineage and (if importable) its runtime instance."""

    declaration: dict
    runtime: Any = None
    available: bool = False
    unavailable_reason: Optional[str] = None

    @property
    def lineage_id(self) -> str:
        return self.declaration["lineage_id"]


def declared_lineage_ids() -> List[str]:
    return [d["lineage_id"] for d in LINEAGE_DECLARATIONS]


def pairs_with_zero_shared_input() -> List[tuple]:
    """The 11 of 21 pairs that share no input modality (structural
    independence basis; spec §3.2 DIS-3 terminology)."""
    by_input = {d["lineage_id"]: d["input_modality"] for d in LINEAGE_DECLARATIONS}
    ids = declared_lineage_ids()
    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if by_input[a] != by_input[b]:
                pairs.append((a, b))
    return pairs


def load_registry(strict: bool = False) -> List[RegistryEntry]:
    """Load all 7 lineage implementations.

    Missing modules yield ``RegistryEntry(available=False)`` so callers can
    report honest unavailability; with ``strict=True`` any missing
    implementation raises ``LookupError`` (used by live-mode gating, which
    must fail closed rather than run with fewer than 7 lineages).
    """
    entries: List[RegistryEntry] = []
    missing: List[str] = []
    for decl in LINEAGE_DECLARATIONS:
        entry = RegistryEntry(declaration=dict(decl))
        try:
            module = importlib.import_module(decl["module"])
            factory = getattr(module, "Lineage", None)
            if factory is None:
                raise AttributeError(
                    f"module {decl['module']} exposes no 'Lineage' class"
                )
            entry.runtime = factory()
            entry.available = True
        except Exception as exc:  # ImportError, AttributeError, ctor errors
            entry.available = False
            entry.unavailable_reason = f"{type(exc).__name__}: {exc}"
            missing.append(decl["lineage_id"])
        entries.append(entry)
    if strict and missing:
        raise LookupError(
            "lineage implementations unavailable (fail closed): "
            + ", ".join(missing)
        )
    return entries
