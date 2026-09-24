"""l1-photometric: luminance-ratio photometry lineage (spec §3.2).

Measures the brightest region of the scene against the scene floor on a
downsampled grayscale render. Deterministic thresholds; no learned weights.
Emits the 3 shared + 2 specific claim contract only when its measured
evidence supports it — otherwise abstains (``non_comparable``). Zero shared
implementation code with the other six lineages (independence argument).

Runs inside the sandboxed worker (FR-017); PIL is the only decoder.
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

LINEAGE_ID = "l1-photometric"

_ANALYSIS_SIZE = 256
# Deterministic measurement thresholds (operator-tuned constants).
_LUMINOUS_RATIO = 1.5          # peak / scene mean
_ANOMALOUS_RATIO = 3.0         # peak / scene mean for anomalous signature
_POINT_FRACTION = 0.005        # bright-region area fraction => point source
_EXTENDED_FRACTION = 0.02      # bright-region area fraction => extended


class Lineage:
    lineage_id = LINEAGE_ID
    frames_required = LINEAGE_FRAME_COUNTS[LINEAGE_ID]

    def run(self, case_id: str, media_path: str, case_dir: str) -> LineageOutput:
        try:
            stats = _photometry_stats(case_id, media_path, case_dir)
        except Exception as exc:
            return make_non_comparable(
                LINEAGE_ID, f"photometry_unreadable:{type(exc).__name__}")
        if stats is None:
            return make_non_comparable(LINEAGE_ID)

        ratio = stats["peak_mean_ratio"]
        frac = stats["bright_region_fraction"]
        supported_shared = []
        if frac <= _POINT_FRACTION:
            supported_shared.append("point_source")
        elif frac >= _EXTENDED_FRACTION:
            supported_shared.append("extended_source")
        if ratio >= _LUMINOUS_RATIO:
            supported_shared.append("luminous")
        if ratio >= _ANOMALOUS_RATIO:
            supported_shared.append("anomalous_signature")

        supported_specific = []
        if ratio >= 2.0:
            supported_specific.append("high_luminance_ratio")
        if stats["gradient_anomalous"]:
            supported_specific.append("luminance_gradient_anomalous")
        if stats["profile_known"]:
            supported_specific.append(
                "luminance_profile_consistent_with_known_object")

        vocab = load_vocabulary()
        selected = select_claims(
            vocab, LINEAGE_ID, supported_shared, supported_specific)
        if selected is None:
            return make_non_comparable(LINEAGE_ID)
        shared, specific = selected
        rationale = (
            f"peak/mean luminance ratio {ratio:.2f} over scene mean; bright "
            f"region covers {frac * 100.0:.2f}% of the analysis frame"
        )
        return LineageOutput(
            lineage_id=LINEAGE_ID,
            status=STATUS_OK,
            classification=None,
            artifact_detected=False,
            evidence_claims=EvidenceClaims(shared=shared, specific=specific),
            confidence=round(min(0.95, 0.4 + 0.1 * ratio), 4),
            defense_claims=[],
            rationale=rationale,
            provenance={
                "analysis_size": _ANALYSIS_SIZE,
                "peak_mean_ratio": round(ratio, 6),
                "bright_region_fraction": round(frac, 8),
                "thresholds": {
                    "luminous": _LUMINOUS_RATIO,
                    "anomalous": _ANOMALOUS_RATIO,
                    "point_fraction": _POINT_FRACTION,
                    "extended_fraction": _EXTENDED_FRACTION,
                },
            },
            frame_indices_used=list(stats.get("frame_indices") or []),
        )


def _first_frame_path(case_id: str, media_path: str, case_dir: str):
    """Return a readable image path for the media (extracts frame 0 for
    video inside the sandbox). None when extraction is impossible."""
    import os
    from pathlib import Path

    path = Path(media_path)
    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return str(path)
    out = Path(case_dir) / ".l1_frame0.png"
    try:
        import subprocess

        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(path),
             "-frames:v", "1", "-f", "image2", str(out)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=60,
        )
        if proc.returncode == 0 and out.is_file():
            return str(out)
    except Exception:
        pass
    # Extraction failed: remove any partial output and abstain upstream.
    if out.exists():
        try:
            out.unlink()
        except OSError:
            pass
    return None


def _photometry_stats(case_id: str, media_path: str, case_dir: str):
    """Deterministic luminance statistics on a downsampled grayscale frame."""
    from PIL import Image

    frame_path = _first_frame_path(case_id, media_path, case_dir)
    if frame_path is None:
        return None
    with Image.open(frame_path) as im:
        gray = im.convert("L").resize((_ANALYSIS_SIZE, _ANALYSIS_SIZE))
        pixels = list(gray.getdata())
    n = len(pixels)
    mean = sum(pixels) / float(n)
    if mean <= 1e-6:
        return None
    peak = float(max(pixels))
    var = sum((p - mean) ** 2 for p in pixels) / float(n)
    std = var ** 0.5
    threshold = mean + 2.0 * std
    bright = sum(1 for p in pixels if p > threshold)
    # Radial profile monotonicity around the brightest pixel: a physical
    # point emitter falls off monotonically; sensor speckle does not.
    width = _ANALYSIS_SIZE
    idx = pixels.index(int(peak))
    cx, cy = idx % width, idx // width
    ring = []
    for radius in (2, 4, 8, 16):
        vals = []
        for dx in range(-radius, radius + 1, max(1, radius)):
            for dy in range(-radius, radius + 1, max(1, radius)):
                x, y = cx + dx, cy + dy
                if 0 <= x < width and 0 <= y < width:
                    vals.append(pixels[y * width + x])
        ring.append(sum(vals) / len(vals) if vals else mean)
    profile_known = all(ring[i] >= ring[i + 1] for i in range(len(ring) - 1))
    return {
        "peak_mean_ratio": peak / mean,
        "bright_region_fraction": bright / float(n),
        "gradient_anomalous": (std / mean) > 0.85,
        "profile_known": profile_known,
        "frame_indices": [0],
    }
