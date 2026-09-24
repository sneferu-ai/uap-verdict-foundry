"""Taxonomy-driven battery categories: drones, birds, insects, meteors,
balloons (FR-006 plugin interface, spec §3.5).

NOT WIRED IN THE MVP DEFAULT BATTERY. The four-category FR-006 default
(aircraft, satellites, lens_artifacts, astronomical) deliberately
excludes these categories (spec §1 resolution 13), and the §1 mapping
marks drone/bird/weather_phenomenon as UNTESTED: a lineage naming one
forces the uncovered-mundane amendment to ``insufficient_data``
REGARDLESS of battery results — a single-lineage taxonomy positive here
can never defeat that amendment (verdict._base_verdict checks the
amendment before any positive). These adapters remain only as §5 plugin
examples for phase-2 category additions.

These categories have no independent external data source in the MVP; they
consume the lineage classification record directly, and they are
deliberately conservative:

  * a lineage that made NO taxonomy decision (classification null /
    ``unknown`` with no declared coverage) is NOT a negative — absence of a
    decision is ``insufficient`` ("no taxonomy-capable lineages ...");
  * a NEGATIVE requires enough *comparable* lineages — lineages that either
    classified something or explicitly declare ``taxonomy_coverage`` for
    this category in their provenance;
  * a POSITIVE requires an explicit classification inside the configured
    category set.

``SensorArtifactAdapter`` reuses the same logic for the sensor-artifact
classification category (OBL-13/40 parity).

Mock hooks (FR-018, mock mode only): ``UAPV_MOCK_DRONE=1`` forces a
pre-registered drone positive fixture for categories configured on
``[drone]``; ``UAPV_MOCK_BALLOON=1`` does the same for ``[balloon]``.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

from uapvf.adapters import BatteryCategoryAdapter, canonical_json, content_hash
from uapvf.config import utcnow_iso

_UNDECIDED = {None, "", "unknown", "unidentifiable"}


def _available_lineages(lineage_outputs: Optional[List[dict]]) -> List[dict]:
    """Lineages that actually ran (status ok or legacy no-status)."""
    out = []
    for entry in lineage_outputs or []:
        status = entry.get("status")
        if status in (None, "ok"):
            out.append(entry)
    return out


def _covers(entry: dict, classifications: List[str]) -> bool:
    """True when the lineage examined this category: it classified inside
    the set, it made ANY valid closed-set taxonomy determination (which
    covers every category — a classified object is by definition not the
    others), or it explicitly declares ``taxonomy_coverage``."""
    from uapvf.lineages.taxonomy import is_valid_taxonomy

    cls = entry.get("classification")
    if cls in classifications:
        return True
    if is_valid_taxonomy(cls):
        return True
    coverage = (entry.get("provenance") or {}).get("taxonomy_coverage") or []
    return any(c in classifications for c in coverage)


class TaxonomyAdapter(BatteryCategoryAdapter):
    """Closed-set taxonomy consumption for one mundane category family."""

    #: env var -> classification fixture (mock mode only)
    MOCK_HOOKS = {
        "drone": "UAPV_MOCK_DRONE",
        "balloon": "UAPV_MOCK_BALLOON",
    }

    def run(self, media_path, metadata, lineage_outputs, config) -> dict:
        now = utcnow_iso()
        classifications = [str(c) for c in config.get("classifications") or []]
        min_comparable = int(config.get("min_comparable") or 2)
        label = ", ".join(classifications) or "unspecified"

        def stamp(reason=None):
            return {
                "source_id": "taxonomy_analysis",
                "query_params": canonical_json(
                    {"classifications": classifications,
                     "min_comparable": min_comparable}),
                "utc_timestamp": now,
                "content_hash": content_hash(
                    canonical_json(lineage_outputs or [])),
                "source_version": "taxonomy_v1",
                "reason": reason,
            }

        hook = next((self.MOCK_HOOKS[c] for c in classifications
                     if c in self.MOCK_HOOKS), None)
        if hook and os.environ.get(hook) == "1":
            target = next(c for c in classifications if c in self.MOCK_HOOKS)
            return {
                "result": "positive",
                "evidence_citation": (
                    f"mock {target} fixture: one lineage classified the "
                    f"object as {target} (fixture MOCK-{target.upper()}-01)"),
                "source_stamp": stamp(),
            }

        available = _available_lineages(lineage_outputs)
        if not available:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": stamp(
                    "no taxonomy-capable lineages available for "
                    f"{label}"),
            }

        matched = [e for e in available
                   if e.get("classification") in classifications]
        if matched:
            ids = sorted(str(e.get("lineage_id")) for e in matched)
            return {
                "result": "positive",
                "evidence_citation": (
                    f"{len(matched)} of {len(available)} available lineages "
                    f"classified the object in {{{label}}} "
                    f"(lineages: {', '.join(ids)})"),
                "source_stamp": stamp(),
            }

        comparable = [e for e in available if _covers(e, classifications)]
        if len(comparable) < min_comparable:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": stamp(
                    f"insufficient taxonomy-capable lineages for {label}: "
                    f"{len(comparable)} comparable of {len(available)} "
                    f"available (need {min_comparable})"),
            }
        return {
            "result": "negative",
            "evidence_citation": (
                f"{len(comparable)} taxonomy-capable lineages covered "
                f"{{{label}}}; none classified the object in that set"),
            "source_stamp": stamp(),
        }


class SensorArtifactAdapter(TaxonomyAdapter):
    """Sensor-artifact category: identical consumption contract, configured
    on [sensor_artifact] (kept as its own class so battery_config names a
    distinct adapter per category, per the plugin interface)."""

    MOCK_HOOKS = {"sensor_artifact": "UAPV_MOCK_SENSOR_ARTIFACT"}
