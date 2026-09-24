"""l3-geometric: edge/boundary + symmetry geometry lineage (spec §3.2).

Deterministic gradient and mirror-correlation measurements on a
downsampled grayscale frame. No learned weights. Emits claims only from
measured evidence; abstains otherwise. Zero shared implementation code
with other lineages.
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

LINEAGE_ID = "l3-geometric"

_ANALYSIS_SIZE = 256
_SHARP_EDGE_FRACTION = 0.04   # fraction of strong-gradient pixels => sharp
_SYMMETRY_CORRELATION = 0.75  # mirror correlation => symmetric form
_POINT_FRACTION = 0.005
_EXTENDED_FRACTION = 0.02


class Lineage:
    lineage_id = LINEAGE_ID
    frames_required = LINEAGE_FRAME_COUNTS[LINEAGE_ID]

    def run(self, case_id: str, media_path: str, case_dir: str) -> LineageOutput:
        try:
            stats = _geometry_stats(case_id, media_path, case_dir)
        except Exception as exc:
            return make_non_comparable(
                LINEAGE_ID, f"geometry_unreadable:{type(exc).__name__}")
        if stats is None:
            return make_non_comparable(LINEAGE_ID)

        supported_shared = []
        if stats["point"]:
            supported_shared.append("point_source")
        elif stats["extended"]:
            supported_shared.append("extended_source")
        if stats["sharp"]:
            supported_shared.append("sharp_boundary")
        else:
            supported_shared.append("diffuse_boundary")
        if stats["symmetric"]:
            supported_shared.append("symmetric_form")
        else:
            supported_shared.append("asymmetric_form")
        if stats["motion_possible"] and stats["displacement"] > 4.0:
            supported_shared.append("motion_detected")
        if stats["periodic"]:
            supported_shared.append("periodic_behavior")

        supported_specific = []
        if stats["contour_closed"]:
            supported_specific.append("geometric_contour_matched")
        if stats["range_consistent"]:
            supported_specific.append("perspective_consistent_with_range")
        if stats["symmetric"]:
            supported_specific.append("shape_symmetry_detected")

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
            confidence=round(
                min(0.9, 0.4 + 0.1 * stats["mirror_correlation"]), 4),
            defense_claims=[],
            rationale=(
                f"strong-edge fraction {stats['edge_fraction']:.3f}; "
                f"mirror correlation {stats['mirror_correlation']:.3f}; "
                f"bright-region fraction {stats['region_fraction']:.4f}"
            ),
            provenance={
                "analysis_size": _ANALYSIS_SIZE,
                "edge_fraction": round(stats["edge_fraction"], 6),
                "mirror_correlation": round(stats["mirror_correlation"], 6),
                "region_fraction": round(stats["region_fraction"], 8),
                "thresholds": {
                    "sharp_edge_fraction": _SHARP_EDGE_FRACTION,
                    "symmetry_correlation": _SYMMETRY_CORRELATION,
                },
            },
            frame_indices_used=list(stats.get("frame_indices") or [0]),
        )


def _frame_paths(case_id: str, media_path: str, case_dir: str, count: int):
    """First frame (always) plus a later frame for video motion checks."""
    from pathlib import Path

    path = Path(media_path)
    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return [str(path)]
    first = Path(case_dir) / ".l3_frame0.png"
    later = Path(case_dir) / ".l3_frameN.png"
    paths = []
    try:
        import subprocess

        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(path),
             "-frames:v", "1", "-f", "image2", str(first)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=60,
        )
        if proc.returncode == 0 and first.is_file():
            paths.append(str(first))
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-sseof", "-0.5", "-i", str(path),
             "-frames:v", "1", "-f", "image2", str(later)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=60,
        )
        if proc.returncode == 0 and later.is_file():
            paths.append(str(later))
    except Exception:
        pass
    return paths


def _to_gray(frame_path: str):
    from PIL import Image

    with Image.open(frame_path) as im:
        return im.convert("L").resize((_ANALYSIS_SIZE, _ANALYSIS_SIZE))


def _geometry_stats(case_id: str, media_path: str, case_dir: str):
    paths = _frame_paths(case_id, media_path, case_dir, 2)
    if not paths:
        return None
    gray = _to_gray(paths[0])
    px = list(gray.getdata())
    n = len(px)
    mean = sum(px) / float(n)
    var = sum((p - mean) ** 2 for p in px) / float(n)
    std = var ** 0.5
    if std <= 1e-6:
        return None
    w = _ANALYSIS_SIZE

    # Strong-edge fraction via 4-neighbour gradient magnitude.
    strong = 0
    for y in range(1, w - 1):
        base = y * w
        for x in range(1, w - 1):
            i = base + x
            gx = abs(px[i + 1] - px[i - 1])
            gy = abs(px[i + w] - px[i - w])
            if (gx + gy) > 2.0 * std:
                strong += 1
    edge_fraction = strong / float((w - 2) * (w - 2))

    # Mirror correlation (left half vs flipped right half).
    num = 0.0
    den_a = 0.0
    den_b = 0.0
    half = w // 2
    for y in range(w):
        for x in range(half):
            a = px[y * w + x] - mean
            b = px[y * w + (w - 1 - x)] - mean
            num += a * b
            den_a += a * a
            den_b += b * b
    mirror_correlation = (
        num / ((den_a ** 0.5) * (den_b ** 0.5)) if den_a > 0 and den_b > 0
        else 0.0)

    threshold = mean + 2.0 * std
    bright = sum(1 for p in px if p > threshold)
    region_fraction = bright / float(n)

    # Bright-region centroid extent: bounding-box fill as a contour closure
    # proxy, and size-vs-frame as a range-consistency proxy.
    xs = [i % w for i, p in enumerate(px) if p > threshold]
    ys = [i // w for i, p in enumerate(px) if p > threshold]
    contour_closed = False
    range_consistent = False
    if xs and ys:
        bw = max(xs) - min(xs) + 1
        bh = max(ys) - min(ys) + 1
        fill = bright / float(bw * bh)
        contour_closed = fill > 0.25
        size_frac = (bw * bh) / float(n)
        range_consistent = 1e-5 < size_frac < 0.5

    # Motion between first and a later frame (video only).
    displacement = 0.0
    motion_possible = len(paths) > 1
    if motion_possible:
        gray2 = _to_gray(paths[1])
        px2 = list(gray2.getdata())

        def centroid(pixels):
            idxs = [i for i, p in enumerate(pixels) if p > threshold]
            if not idxs:
                return None
            cx = sum(i % w for i in idxs) / len(idxs)
            cy = sum(i // w for i in idxs) / len(idxs)
            return cx, cy

        c0, c1 = centroid(px), centroid(px2)
        if c0 is not None and c1 is not None:
            displacement = ((c0[0] - c1[0]) ** 2 + (c0[1] - c1[1]) ** 2) ** 0.5

    return {
        "point": region_fraction <= _POINT_FRACTION,
        "extended": region_fraction >= _EXTENDED_FRACTION,
        "sharp": edge_fraction >= _SHARP_EDGE_FRACTION,
        "symmetric": mirror_correlation >= _SYMMETRY_CORRELATION,
        "motion_possible": motion_possible,
        "displacement": displacement,
        "periodic": False,  # periodicity is l5/l7's measurement, not l3's
        "contour_closed": contour_closed,
        "range_consistent": range_consistent,
        "edge_fraction": edge_fraction,
        "mirror_correlation": mirror_correlation,
        "region_fraction": region_fraction,
        "frame_indices": [0] if len(paths) == 1 else [0, 1],
    }
