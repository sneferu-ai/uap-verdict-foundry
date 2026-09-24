"""Lens/sensor artifact battery category driven by lineage outputs (§5).

The artifact group is {lens_artifact, sensor_artifact}.
artifact_group_fraction = count(artifact_detected=true AND classification in
artifact group) / total lineage count.

- lineage_outputs None/empty -> insufficient, "no vision lineages available",
  source_id "lineage_unavailable".
- fewer than min_lineages -> insufficient, "insufficient lineages for
  artifact assessment".
- >= min_lineages, >= 2 in group, fraction >= threshold -> positive.
- >= min_lineages, none with artifact_detected in group -> negative.
- otherwise (0 < fraction < threshold) -> insufficient, "lineage
  disagreement below threshold".
"""
from __future__ import annotations

from uapvf.adapters import BatteryCategoryAdapter, canonical_json, content_hash
from uapvf.config import utcnow_iso

ARTIFACT_GROUP = {"lens_artifact", "sensor_artifact"}


class LineageAdapter(BatteryCategoryAdapter):
    name = "lens_artifacts"

    def run(self, media_path, metadata: dict, lineage_outputs, config: dict) -> dict:
        min_lineages = int(self.params.get("min_lineages", 2))
        threshold = float(self.params.get("artifact_group_fraction_threshold", 0.7))
        now = utcnow_iso()
        outputs = lineage_outputs or []

        def stamp(reason=None, payload=None):
            return {
                "source_id": "lineage_analysis" if outputs else "lineage_unavailable",
                "query_params": "{}",
                "utc_timestamp": now,
                "content_hash": content_hash(payload if payload is not None else outputs),
                "source_version": "lineage_v1",
                **({"reason": reason} if reason else {}),
            }

        if not outputs:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": stamp("no vision lineages available", []),
            }
        comparable = [
            output for output in outputs
            if output.get("status", "ok") == "ok"
        ]
        total = len(comparable)
        if total < min_lineages:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": stamp(
                    f"insufficient comparable lineages for artifact assessment; "
                    f"{total} available"
                ),
            }
        in_group = [
            o
            for o in comparable
            if o.get("artifact_detected")
            and (o.get("classification") in ARTIFACT_GROUP)
        ]
        fraction = len(in_group) / total
        if len(in_group) >= 2 and fraction >= threshold:
            return {
                "result": "positive",
                "evidence_citation": (
                    f"{len(in_group)} of {total} vision lineages detected an "
                    "optical/sensor artifact signature (fraction "
                    f"{fraction:.2f} >= {threshold:.2f})"
                ),
                "source_stamp": stamp(),
            }
        if not in_group:
            return {
                "result": "negative",
                "evidence_citation": (
                    f"{total} vision lineages examined the media; none "
                    "detected a lens or sensor artifact signature"
                ),
                "source_stamp": stamp(),
            }
        return {
            "result": "insufficient",
            "evidence_citation": None,
            "source_stamp": stamp("lineage disagreement below threshold"),
        }
