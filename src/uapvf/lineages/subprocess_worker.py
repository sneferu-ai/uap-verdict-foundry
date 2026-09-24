"""Sandboxed subprocess worker (FR-017, spec §3.2 IPC contract).

Executed as ``python -m uapvf.lineages.subprocess_worker`` inside the
case-scoped sandbox built by ``uapvf.media_sandbox``. Reads ONE JSON job
from stdin, does decoder-facing work (intake decode/normalize/quality or
lineage analysis), and prints ONE JSON result document as the last stdout
line. It never touches the database and never imports server code.

Jobs:
  {"job": "intake",   "original_path", "case_dir", "kind", "quality_config"}
  {"job": "lineages", "case_id", "media_path", "case_dir", "env_passthrough"}

Lineage outputs are validated through ``LineageOutput.from_dict`` semantics
by construction (implementations return real ``LineageOutput`` objects);
video lineage outputs get frame-timestamp provenance attached before they
leave the sandbox.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

# Intake caps live in intake.py; imported lazily inside job handlers so a
# lineages job never pays the intake import cost (and vice versa).


def _attach_frame_provenance(payload: dict, fps: float) -> dict:
    """Attach frame-timestamp provenance to a lineage output dict.

    ``frame_indices_used`` is copied into ``provenance`` together with the
    container-timestamp of each sampled frame (``index / fps``). A missing
    or zero fps records indices only, never invented timestamps."""
    payload = dict(payload)
    indices = [int(i) for i in (payload.get("frame_indices_used") or [])]
    provenance = dict(payload.get("provenance") or {})
    provenance["frame_indices_used"] = indices
    if fps and fps > 0:
        provenance["frame_timestamps_seconds"] = [
            round(i / float(fps), 6) for i in indices
        ]
        provenance["timestamp_basis"] = "frame_index/source_fps"
        provenance["container_fps"] = float(fps)
    else:
        provenance["frame_timestamps_seconds"] = []
        provenance["timestamp_basis"] = "unavailable"
    payload["provenance"] = provenance
    return payload


# ---------------------------------------------------------------------------
# Intake job
# ---------------------------------------------------------------------------

def _run_intake_job(job: dict) -> dict:
    from pathlib import Path

    from uapvf.media_check import probe_video
    from uapvf.quality_gate import run_quality_gate

    original = Path(job["original_path"])
    case_dir = Path(job["case_dir"])
    kind = job.get("kind") or "image"
    qcfg = job.get("quality_config") or None

    if not original.is_file():
        return {"ok": False, "status_code": 422,
                "error": "original media missing in sandbox",
                "probe": {}, "quality": {}}

    if kind == "video":
        from uapvf.intake import (VIDEO_MAX_DURATION_S, VIDEO_MIN_FRAMES,
                                  normalize_video)

        probe = probe_video(str(original))
        if not probe.get("ok"):
            return {"ok": False, "status_code": 422,
                    "error": probe.get("error") or "undecodable video",
                    "probe": {}, "quality": {}}
        if probe.get("duration_s", 0.0) > VIDEO_MAX_DURATION_S:
            return {"ok": False, "status_code": 413,
                    "error": (f"video duration {probe['duration_s']:.1f}s "
                              f"exceeds {VIDEO_MAX_DURATION_S:.0f}s cap"),
                    "probe": probe, "quality": {}}
        if probe.get("frame_count", 0) < VIDEO_MIN_FRAMES:
            return {"ok": False, "status_code": 422,
                    "error": (f"video has {probe.get('frame_count', 0)} "
                              f"frames; minimum is {VIDEO_MIN_FRAMES}"),
                    "probe": probe, "quality": {}}
        quality = run_quality_gate(str(original), "video", qcfg)
        working = case_dir / "media" / "working.mp4"
        try:
            normalize_video(original, working)
        except FileNotFoundError:
            return {"ok": False, "status_code": 503,
                    "error": "ffmpeg not available in sandbox",
                    "probe": probe, "quality": quality}
        except Exception as exc:
            return {"ok": False, "status_code": 422,
                    "error": f"video normalization failed: {exc}",
                    "probe": probe, "quality": quality}
        return {"ok": True, "probe": probe, "quality": quality,
                "normalized_path": "media/working.mp4"}

    # image
    from uapvf.intake import IMAGE_MAX_MP, normalize_image

    try:
        from PIL import Image

        with Image.open(str(original)) as im:
            im.verify()
        with Image.open(str(original)) as im:
            im.load()
            width, height = im.size
    except Exception as exc:
        return {"ok": False, "status_code": 422,
                "error": f"undecodable image: {type(exc).__name__}",
                "probe": {}, "quality": {}}
    if width * height > IMAGE_MAX_MP:
        return {"ok": False, "status_code": 413,
                "error": (f"image resolution {width}x{height} exceeds the "
                          "20 MP cap"),
                "probe": {"width": width, "height": height}, "quality": {}}
    quality = run_quality_gate(str(original), "image", qcfg)
    working = case_dir / "media" / "working.jpg"
    try:
        normalize_image(original, working)
    except Exception as exc:
        return {"ok": False, "status_code": 422,
                "error": f"image normalization failed: {type(exc).__name__}",
                "probe": {"width": width, "height": height},
                "quality": quality}
    return {"ok": True, "probe": {"width": width, "height": height},
            "quality": quality, "normalized_path": "media/working.jpg"}


# ---------------------------------------------------------------------------
# Lineages job
# ---------------------------------------------------------------------------

def _run_lineages_job(job: dict) -> dict:
    import os

    for key, value in (job.get("env_passthrough") or {}).items():
        if value is not None:
            os.environ[str(key)] = str(value)

    from uapvf.lineages.protocol import LineageOutput, STATUS_UNAVAILABLE
    from uapvf.lineages.registry import load_registry
    from uapvf.media_check import probe_video

    case_id = str(job["case_id"])
    media_path = str(job["media_path"])
    case_dir = str(job["case_dir"])

    try:
        registry = load_registry(strict=True)
    except LookupError as exc:
        return {"ok": False, "error": f"lineage_registry_unavailable: {exc}",
                "lineages": []}

    fps = 0.0
    if media_path.lower().endswith(".mp4"):
        probe = probe_video(media_path)
        if probe.get("ok"):
            fps = float(probe.get("fps") or 0.0)

    outputs = []
    for entry in registry:
        try:
            output = entry.instance.run(case_id, media_path, case_dir)
        except Exception as exc:
            output = LineageOutput(
                lineage_id=entry.lineage_id,
                status=STATUS_UNAVAILABLE,
                rationale=(f"lineage raised {type(exc).__name__}: {exc}"
                           )[:400],
            )
        data = output.to_dict()
        if data.get("frame_indices_used") and fps > 0:
            data = _attach_frame_provenance(data, fps)
        outputs.append(data)
    return {"ok": True, "lineages": outputs}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    try:
        raw = sys.stdin.read()
        job = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        print(json.dumps({"ok": False,
                          "error": f"unparseable job: {exc}"}))
        return 0
    try:
        if job.get("job") == "intake":
            result = _run_intake_job(job)
        elif job.get("job") == "lineages":
            result = _run_lineages_job(job)
        else:
            result = {"ok": False,
                      "error": f"unknown job {job.get('job')!r}"}
    except Exception as exc:
        result = {"ok": False,
                  "error": f"worker failure: {type(exc).__name__}: {exc}"}
    try:
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        print(json.dumps({"ok": False, "error": "worker result not serializable"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
