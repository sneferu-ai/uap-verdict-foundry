"""Intake quality gate with per-category sufficiency (FR-002).

quality_score is computed on the ORIGINAL uploaded file before
normalization. Files below the vision floor are NOT rejected: the case
proceeds with metadata-only categories running normally and vision-dependent
categories (lens artifacts) returning `insufficient`.

Thresholds live in var/quality_config.json (operator-tuned defaults, not
benchmark-derived; reversible by editing the file).

Units note (FR-002): the spec's video formula normalizes BYTES per second —
``(file_size_bytes / duration_seconds) / 5_000_000.0`` — while the spec's
own config schema names that reference value ``bitrate_reference_bps``. The
formula is the behavioral contract, so this implementation divides bytes/s
by the configured reference value, keeping the spec's key name unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from uapvf.config import Settings, get_settings
from uapvf.media_check import probe_video

DEFAULT_QUALITY_CONFIG = {
    "vision_quality_floor": 0.35,
    "calibration_source": "operator_tuned_v1",
    "calibration_date": "2026-01-15",
    "calibration_basis": (
        "operator judgment on 20 sample images/videos; threshold set to "
        "separate clearly usable media from heavily degraded media"
    ),
    "components": {
        "resolution_weight": 0.40,
        "compression_weight": 0.35,
        "exif_weight": 0.25,
        "frame_rate_weight": 0.30,
        "bitrate_weight": 0.30,
        "resolution_reference_pixels": 1920,
        "bitrate_reference_bps": 5000000,
        "frame_rate_reference_fps": 30,
    },
}


def load_quality_config(settings: Optional[Settings] = None) -> dict:
    settings = settings or get_settings()
    path = settings.var_dir / "quality_config.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        merged = json.loads(json.dumps(DEFAULT_QUALITY_CONFIG))
        merged.update({k: v for k, v in data.items() if k != "components"})
        merged["components"].update(data.get("components", {}))
        return merged
    except Exception:
        return json.loads(json.dumps(DEFAULT_QUALITY_CONFIG))


def score_image(
    media_path, quality_config: Optional[dict] = None
) -> dict:
    """FR-002 image formula. Returns {quality_score, components, has_exif,
    width, height}."""
    from PIL import Image

    cfg = (quality_config or DEFAULT_QUALITY_CONFIG)["components"]
    with Image.open(media_path) as im:
        width, height = im.size
        info = dict(im.info or {})
        try:
            has_exif = bool(im.getexif()) or "exif" in info
        except Exception:
            has_exif = False
    long_edge = max(width, height)
    resolution_score = min(1.0, long_edge / float(cfg["resolution_reference_pixels"]))
    if "quality" in info:
        try:
            compression_score = float(info["quality"]) / 100.0
        except Exception:
            compression_score = 0.5
    else:
        compression_score = 0.5
    exif_score = 1.0 if has_exif else 0.0
    score = (
        cfg["resolution_weight"] * resolution_score
        + cfg["compression_weight"] * compression_score
        + cfg["exif_weight"] * exif_score
    )
    return {
        "quality_score": round(min(1.0, max(0.0, score)), 6),
        "components": {
            "resolution_score": round(resolution_score, 6),
            "compression_score": round(compression_score, 6),
            "exif_score": round(exif_score, 6),
        },
        "has_exif": has_exif,
        "width": width,
        "height": height,
    }


def score_video(media_path, quality_config: Optional[dict] = None) -> dict:
    """FR-002 video formula, driven by ffprobe metrics."""
    cfg = (quality_config or DEFAULT_QUALITY_CONFIG)["components"]
    probe = probe_video(str(media_path))
    if not probe.get("ok"):
        return {
            "quality_score": 0.0,
            "components": {},
            "error": probe.get("error") or "video probe failed",
            "width": 0,
            "height": 0,
        }
    long_edge = max(probe["width"], probe["height"])
    resolution_score = min(1.0, long_edge / float(cfg["resolution_reference_pixels"]))
    duration = max(probe["duration_s"], 0.001)
    frame_rate_score = min(1.0, probe["fps"] / float(cfg["frame_rate_reference_fps"]))
    size_bytes = probe.get("size_bytes") or Path(media_path).stat().st_size
    # FR-002 formula: min(1.0, (file_size_bytes / duration_seconds) /
    # 5_000_000.0). Bytes per second, NOT bits per second — see the module
    # docstring units note about the spec's key name.
    bitrate_score = min(
        1.0, (size_bytes / duration) / float(cfg["bitrate_reference_bps"])
    )
    score = (
        cfg["resolution_weight"] * resolution_score
        + cfg["frame_rate_weight"] * frame_rate_score
        + cfg["bitrate_weight"] * bitrate_score
    )
    return {
        "quality_score": round(min(1.0, max(0.0, score)), 6),
        "components": {
            "resolution_score": round(resolution_score, 6),
            "frame_rate_score": round(frame_rate_score, 6),
            "bitrate_score": round(bitrate_score, 6),
        },
        "width": probe["width"],
        "height": probe["height"],
        "duration_s": probe["duration_s"],
        "fps": probe["fps"],
        "frame_count": probe["frame_count"],
    }


def run_quality_gate(
    media_path, kind: str, quality_config: Optional[dict] = None
) -> dict:
    """Score original media and apply the vision floor.

    Returns {quality_score, quality_gate_pass (bool), floor, components...}.
    quality_gate_pass False means vision analysis is skipped — NOT that the
    case was rejected (spec §1 quality_gate_pass semantics)."""
    cfg = quality_config or DEFAULT_QUALITY_CONFIG
    floor = float(cfg.get("vision_quality_floor", 0.35))
    if kind == "image":
        result = score_image(media_path, cfg)
    else:
        result = score_video(media_path, cfg)
    score = result.get("quality_score", 0.0)
    result["vision_quality_floor"] = floor
    result["quality_gate_pass"] = score >= floor
    # quality_score_normalized == quality_score (components already in [0,1])
    result["quality_score_normalized"] = score
    return result
