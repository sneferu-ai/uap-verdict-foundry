"""l7-audio: audio-spectrum lineage (spec §3.2, consumes no frames).

Extracts the mono audio track (ffmpeg) and measures band energies,
narrowband peaks, and rotor-band periodicity with numpy. Media without an
audio track carries no audio evidence — l7 abstains. Zero shared
implementation code with other lineages.
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

LINEAGE_ID = "l7-audio"

_SAMPLE_RATE = 16000
_PEAK_DB_FLOOR = 6.0        # narrowband peak above the broadband floor
_ROTOR_BAND_HZ = (5.0, 200.0)
_TILT_SLOPE = (-2.0, -0.3)  # 1/f-ish environmental spectral tilt range


class Lineage:
    lineage_id = LINEAGE_ID
    frames_required = LINEAGE_FRAME_COUNTS[LINEAGE_ID]

    def run(self, case_id: str, media_path: str, case_dir: str) -> LineageOutput:
        from pathlib import Path

        if Path(media_path).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            return make_non_comparable(
                LINEAGE_ID, "image_media_has_no_audio_track")
        try:
            stats = _audio_stats(case_id, media_path, case_dir)
        except Exception as exc:
            return make_non_comparable(
                LINEAGE_ID, f"audio_unreadable:{type(exc).__name__}")
        if stats is None:
            return make_non_comparable(LINEAGE_ID)

        supported_shared = []
        if stats["periodic"]:
            supported_shared.append("periodic_behavior")
            supported_shared.append("motion_detected")
        if stats["signature"]:
            supported_shared.append("frequency_signature")
        if stats["stable"]:
            supported_shared.append("temporal_consistency")
        if stats["doppler_like"]:
            supported_shared.append("velocity_profile")

        supported_specific = []
        if stats["signature"]:
            supported_specific.append("audio_spectral_signature_detected")
        if stats["rotor"]:
            supported_specific.append("audio_rotor_signature_detected")
        if stats["environmental"]:
            supported_specific.append("audio_environmental_match")

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
            confidence=round(min(0.9, 0.35 + 0.1 * stats["peak_db"] / 10.0), 4),
            defense_claims=[],
            rationale=(
                f"strongest narrowband peak {stats['peak_hz']:.1f} Hz at "
                f"+{stats['peak_db']:.1f} dB over the broadband floor; "
                f"rotor-band energy share {stats['rotor_share']:.3f}"
            ),
            provenance={
                "sample_rate": _SAMPLE_RATE,
                "peak_hz": round(stats["peak_hz"], 3),
                "peak_db_over_floor": round(stats["peak_db"], 3),
                "rotor_share": round(stats["rotor_share"], 6),
                "spectral_tilt": round(stats["tilt"], 4),
                "thresholds": {
                    "peak_db_floor": _PEAK_DB_FLOOR,
                    "rotor_band_hz": list(_ROTOR_BAND_HZ),
                },
            },
            frame_indices_used=[],
        )


def _extract_wav(case_id: str, media_path: str, case_dir: str):
    import subprocess
    from pathlib import Path

    out = Path(case_dir) / ".l7_audio.wav"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(media_path),
         "-vn", "-ac", "1", "-ar", str(_SAMPLE_RATE),
         "-f", "wav", str(out)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=120,
    )
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 1024:
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        return None
    return out


def _audio_stats(case_id: str, media_path: str, case_dir: str):
    import math

    import numpy as np

    wav_path = _extract_wav(case_id, media_path, case_dir)
    if wav_path is None:
        return None
    try:
        samples = _read_wav_mono(wav_path)
    finally:
        try:
            wav_path.unlink()
        except OSError:
            pass
    if samples is None or len(samples) < _SAMPLE_RATE // 2:
        return None
    if float(np.max(np.abs(samples))) <= 1e-6:
        return None  # silent track: no audio evidence

    def band_spectrum(block):
        window = np.hanning(len(block))
        fft = np.fft.rfft(block * window)
        power = (np.abs(fft) ** 2) + 1e-12
        freqs = np.fft.rfftfreq(len(block), d=1.0 / _SAMPLE_RATE)
        return freqs, power

    n = len(samples)
    half = n // 2
    freqs, power_a = band_spectrum(samples[:half])
    _, power_b = band_spectrum(samples[half:2 * half])
    power = (power_a + power_b) / 2.0
    db = 10.0 * np.log10(power)
    floor = float(np.median(db))
    peak_idx = int(np.argmax(db))
    peak_hz = float(freqs[peak_idx])
    peak_db = float(db[peak_idx] - floor)

    rotor_mask = (freqs >= _ROTOR_BAND_HZ[0]) & (freqs <= _ROTOR_BAND_HZ[1])
    rotor_share = float(power[rotor_mask].sum() / power.sum()) if power.sum() else 0.0
    rotor_peak = bool(rotor_mask.any() and peak_hz <= _ROTOR_BAND_HZ[1]
                      and peak_db >= _PEAK_DB_FLOOR)

    # Spectral tilt: log-log slope between 100 Hz and 4 kHz.
    m = (freqs > 100) & (freqs < 4000)
    tilt = 0.0
    if m.sum() > 8:
        lx = np.log(freqs[m])
        ly = np.log(power[m])
        tilt = float(np.polyfit(lx, ly, 1)[0])

    stable = bool(np.corrcoef(power_a, power_b)[0, 1] > 0.5)
    # Doppler-like shift: dominant frequency drift between the two halves.
    peak_a = float(freqs[int(np.argmax(power_a))])
    peak_b = float(freqs[int(np.argmax(power_b))])
    doppler_like = bool(abs(peak_a - peak_b) > max(2.0, 0.05 * peak_a))
    environmental = bool(_TILT_SLOPE[0] <= tilt <= _TILT_SLOPE[1])
    return {
        "signature": peak_db >= _PEAK_DB_FLOOR,
        "rotor": rotor_peak,
        "environmental": environmental,
        "periodic": peak_db >= _PEAK_DB_FLOOR,
        "stable": stable,
        "doppler_like": doppler_like,
        "peak_hz": peak_hz,
        "peak_db": max(0.0, peak_db),
        "rotor_share": rotor_share,
        "tilt": tilt,
    }


def _read_wav_mono(path):
    """Minimal PCM16 WAV reader (numpy) — avoids scipy dependencies."""
    import wave

    import numpy as np

    try:
        with wave.open(str(path), "rb") as fh:
            n_frames = fh.getnframes()
            if n_frames <= 0:
                return None
            raw = fh.readframes(n_frames)
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
        # Bounded analysis window: at most 20 s.
        cap = _SAMPLE_RATE * 20
        if len(data) > cap:
            data = data[:cap]
        return data
    except Exception:
        return None
