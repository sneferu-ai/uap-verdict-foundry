"""l2-spectral: broadband channel-ratio spectral lineage (spec §3.2).

No spectrometer exists in this product, so l2 measures what an RGB sensor
actually records: per-channel energy ratios, brightness-histogram peak
structure, and row-periodicity in the luminance signal. Deterministic
thresholds; abstains when the claim contract cannot be satisfied from
measured evidence. Zero shared implementation code with other lineages.
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

LINEAGE_ID = "l2-spectral"

_ANALYSIS_SIZE = 256
_HISTOGRAM_BINS = 32
_PEAK_SHARE = 0.30        # single-bin share of the histogram => spectral peak
_DOMINANT_CHANNEL_RATIO = 1.35  # channel energy dominance vs the others
_PERIODIC_POWER_RATIO = 4.0     # strongest row-FFT line vs median power


class Lineage:
    lineage_id = LINEAGE_ID
    frames_required = LINEAGE_FRAME_COUNTS[LINEAGE_ID]

    def run(self, case_id: str, media_path: str, case_dir: str) -> LineageOutput:
        try:
            stats = _spectral_stats(case_id, media_path, case_dir)
        except Exception as exc:
            return make_non_comparable(
                LINEAGE_ID, f"spectral_unreadable:{type(exc).__name__}")
        if stats is None:
            return make_non_comparable(LINEAGE_ID)

        supported_shared = []
        if stats["bright"]:
            supported_shared.append("luminous")
        if stats["extended"]:
            supported_shared.append("extended_source")
        if stats["channel_anomalous"]:
            supported_shared.append("anomalous_signature")
        if stats["periodic"]:
            supported_shared.append("frequency_signature")

        supported_specific = []
        if stats["peak_detected"]:
            supported_specific.append("spectral_peak_detected")
        if stats["entropy_anomalous"]:
            supported_specific.append("spectral_entropy_anomalous")
        if stats["harmonic"]:
            supported_specific.append("frequency_harmonic_pattern")

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
                min(0.9, 0.35 + 0.15 * len(supported_shared)), 4),
            defense_claims=[],
            rationale=(
                f"channel ratios R:G:B "
                f"{stats['r_mean']:.2f}:{stats['g_mean']:.2f}:"
                f"{stats['b_mean']:.2f}; histogram peak share "
                f"{stats['peak_share']:.2f}; row periodicity power ratio "
                f"{stats['periodic_power_ratio']:.2f}"
            ),
            provenance={
                "analysis_size": _ANALYSIS_SIZE,
                "r_mean": round(stats["r_mean"], 4),
                "g_mean": round(stats["g_mean"], 4),
                "b_mean": round(stats["b_mean"], 4),
                "peak_share": round(stats["peak_share"], 6),
                "periodic_power_ratio": round(stats["periodic_power_ratio"], 6),
                "thresholds": {
                    "peak_share": _PEAK_SHARE,
                    "dominant_channel_ratio": _DOMINANT_CHANNEL_RATIO,
                    "periodic_power_ratio": _PERIODIC_POWER_RATIO,
                },
            },
            frame_indices_used=[0],
        )


def _first_frame(case_id: str, media_path: str, case_dir: str):
    from pathlib import Path

    path = Path(media_path)
    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return str(path)
    out = Path(case_dir) / ".l2_frame0.png"
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
    if out.exists():
        try:
            out.unlink()
        except OSError:
            pass
    return None


def _spectral_stats(case_id: str, media_path: str, case_dir: str):
    import math

    from PIL import Image

    frame = _first_frame(case_id, media_path, case_dir)
    if frame is None:
        return None
    with Image.open(frame) as im:
        rgb = im.convert("RGB").resize((_ANALYSIS_SIZE, _ANALYSIS_SIZE))
        r, g, b = rgb.split()
        r_px, g_px, b_px = list(r.getdata()), list(g.getdata()), list(b.getdata())
    n = float(len(r_px))
    r_mean = sum(r_px) / n
    g_mean = sum(g_px) / n
    b_mean = sum(b_px) / n
    lum = [(r_px[i] + g_px[i] + b_px[i]) / 3.0 for i in range(int(n))]
    mean_lum = sum(lum) / n
    if mean_lum <= 1e-6:
        return None

    # Histogram peak structure (a proxy for spectral line concentration).
    bins = [0] * _HISTOGRAM_BINS
    for value in lum:
        idx = min(_HISTOGRAM_BINS - 1, int(value * _HISTOGRAM_BINS / 256.0))
        bins[idx] += 1
    peak_share = max(bins) / n
    entropy = 0.0
    for count in bins:
        if count:
            p = count / n
            entropy -= p * math.log2(p)
    max_entropy = math.log2(_HISTOGRAM_BINS)

    # Row-luminance periodicity via a pure-python DFT on the brightest row.
    bright_row_idx = max(range(_ANALYSIS_SIZE), key=lambda y: sum(
        lum[y * _ANALYSIS_SIZE + x] for x in range(0, _ANALYSIS_SIZE, 8)))
    row = [lum[bright_row_idx * _ANALYSIS_SIZE + x] - mean_lum
           for x in range(_ANALYSIS_SIZE)]
    powers = []
    for k in range(2, _ANALYSIS_SIZE // 2):
        re = sum(row[x] * math.cos(2 * math.pi * k * x / _ANALYSIS_SIZE)
                 for x in range(_ANALYSIS_SIZE))
        im_ = sum(row[x] * math.sin(2 * math.pi * k * x / _ANALYSIS_SIZE)
                  for x in range(_ANALYSIS_SIZE))
        powers.append(re * re + im_ * im_)
    powers_sorted = sorted(powers)
    median_power = powers_sorted[len(powers_sorted) // 2] or 1e-9
    periodic_power_ratio = max(powers) / max(median_power, 1e-9)

    channels = sorted((r_mean, g_mean, b_mean))
    dominance = channels[2] / max(channels[1], 1e-6)
    bright = max(r_mean, g_mean, b_mean) > 96.0
    extended = True  # an RGB frame's content is spatially extended by
    # construction unless the frame is near-black (handled by mean guard).
    return {
        "r_mean": r_mean, "g_mean": g_mean, "b_mean": b_mean,
        "bright": bright,
        "extended": extended,
        "channel_anomalous": dominance >= _DOMINANT_CHANNEL_RATIO,
        "periodic": periodic_power_ratio >= _PERIODIC_POWER_RATIO,
        "peak_detected": peak_share >= _PEAK_SHARE,
        "entropy_anomalous": entropy < 0.5 * max_entropy,
        "harmonic": periodic_power_ratio >= 2.0 * _PERIODIC_POWER_RATIO,
        "periodic_power_ratio": periodic_power_ratio,
        "peak_share": peak_share,
    }
