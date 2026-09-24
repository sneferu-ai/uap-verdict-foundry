"""l4-metadata: EXIF/container metadata consistency lineage (spec §3.2).

Reads ONLY metadata (no pixel analysis). The shared pool for l4 is exactly
three claims (anomalous_signature, metadata_anomaly, temporal_consistency),
so an ``ok`` output requires measured support for all three — a missing or
empty EXIF block therefore abstains rather than emitting claims.
"""
from __future__ import annotations

from uapvf.lineages.evidence_vocabulary import load_vocabulary, select_claims
from uapvf.lineages.frame_sampling import LINEAGE_FRAME_COUNTS
from uapvf.lineages.protocol import (
    EvidenceClaims,
    LineageOutput,
    STATUS_OK,
    make_non_comparable,
)

LINEAGE_ID = "l4-metadata"

_EDIT_SOFTWARE_MARKERS = (
    "photoshop", "gimp", "lightroom", "affinity", "snapseed", "canva",
)


class Lineage:
    lineage_id = LINEAGE_ID
    frames_required = LINEAGE_FRAME_COUNTS[LINEAGE_ID]

    def run(self, case_id: str, media_path: str, case_dir: str) -> LineageOutput:
        try:
            meta = _metadata_stats(media_path)
        except Exception as exc:
            return make_non_comparable(
                LINEAGE_ID, f"metadata_unreadable:{type(exc).__name__}")
        if meta is None or not meta["has_exif"]:
            # No metadata to analyse: no claim contract is satisfiable.
            return make_non_comparable(LINEAGE_ID)

        supported_shared = []
        if meta["temporal_consistent"]:
            supported_shared.append("temporal_consistency")
        if meta["anomaly"]:
            supported_shared.append("metadata_anomaly")
            supported_shared.append("anomalous_signature")

        supported_specific = []
        if meta["device_identified"]:
            supported_specific.append("exif_device_identified")
        if meta["temporal_consistent"]:
            supported_specific.append("timestamp_metadata_consistent")
        if meta["structure_anomaly"]:
            supported_specific.append("file_structure_anomaly")

        vocab = load_vocabulary()
        selected = select_claims(
            vocab, LINEAGE_ID, supported_shared, supported_specific)
        if selected is None:
            return make_non_comparable(LINEAGE_ID)
        shared, specific = selected
        return LineageOutput(
            lineage_id=LINEAGE_ID,
            status=STATUS_OK,
            classification=None,
            artifact_detected=False,
            evidence_claims=EvidenceClaims(shared=shared, specific=specific),
            confidence=round(min(0.9, 0.4 + 0.2 * len(meta["anomalies"])), 4),
            defense_claims=[],
            rationale=(
                f"EXIF present; device identified: "
                f"{meta['device_identified']}; anomalies: "
                f"{meta['anomalies'] or 'none detected'}"
            ),
            provenance={
                "exif_fields_present": meta["fields_present"],
                "anomalies": meta["anomalies"],
                "temporal_consistent": meta["temporal_consistent"],
                "software_marker_present": meta["software_marker"],
            },
            frame_indices_used=[],
        )


def _parse_exif_datetime(value: str):
    from datetime import datetime

    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _metadata_stats(media_path: str):
    from pathlib import Path

    from PIL import Image

    path = Path(media_path)
    if path.suffix.lower() in (".mp4", ".mov", ".webm"):
        # Video containers: no EXIF; container-level consistency checks
        # would require ffprobe metadata parsing, which l4 does not claim.
        return {"has_exif": False, "fields_present": [], "anomalies": [],
                "temporal_consistent": False, "device_identified": False,
                "structure_anomaly": False, "anomaly": False,
                "software_marker": False}
    with Image.open(str(path)) as im:
        try:
            exif = im.getexif()
        except Exception:
            exif = {}
        info = dict(im.info or {})
    fields_present = []
    anomalies = []
    make = model = software = None
    dt_original = None
    try:
        make = exif.get(0x010F)
        model = exif.get(0x0110)
        software = exif.get(0x0131)
        dt_raw = exif.get(0x9003) or exif.get(0x0132)
        if dt_raw:
            dt_original = _parse_exif_datetime(dt_raw)
            if dt_original is None:
                anomalies.append("unparseable_capture_timestamp")
    except Exception:
        anomalies.append("exif_read_error")
    for name, value in (("make", make), ("model", model),
                        ("software", software)):
        if value:
            fields_present.append(name)
    if dt_original is not None:
        fields_present.append("datetime_original")
    has_exif = bool(fields_present) or bool(exif)
    if not has_exif and not info:
        return {"has_exif": False, "fields_present": [], "anomalies": [],
                "temporal_consistent": False, "device_identified": False,
                "structure_anomaly": False, "anomaly": False,
                "software_marker": False}

    device_identified = bool(make or model)
    if make and not model:
        anomalies.append("make_without_model")
    if model and not make:
        anomalies.append("model_without_make")
    software_marker = bool(
        software and any(m in str(software).lower()
                         for m in _EDIT_SOFTWARE_MARKERS))
    if software_marker:
        anomalies.append("editing_software_marker")
    temporal_consistent = False
    if dt_original is not None:
        from datetime import datetime

        year = dt_original.year
        temporal_consistent = 1970 <= year <= datetime.now().year + 1
        if not temporal_consistent:
            anomalies.append("implausible_capture_year")
    structure_anomaly = bool(
        anomalies and "exif_read_error" in anomalies)
    return {
        "has_exif": True,
        "fields_present": fields_present,
        "anomalies": anomalies,
        "temporal_consistent": temporal_consistent,
        "device_identified": device_identified,
        "structure_anomaly": structure_anomaly,
        "anomaly": bool(anomalies),
        "software_marker": software_marker,
    }
