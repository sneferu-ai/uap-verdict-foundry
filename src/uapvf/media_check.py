"""Media decodability check executed in a subprocess (FR-017).

Images are decoded by running `[sys.executable, "-m", "uapvf.media_check",
<media_path>]` so a hostile file is parsed outside the server process.
(``uapvf.pipeline`` is a single module per the §6 layout, hence the module
path differs from FR-017's literal example; see IMPLEMENTATION_NOTES.md.)
The production callers execute this module and ffprobe through
``media_sandbox``: a case-only filesystem view plus denied network access.
The subprocess boundary here is defense in depth, not the egress boundary.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Optional

FFPROBE_TIMEOUT_S = 30
IMAGE_CHECK_TIMEOUT_S = 30


def _ffprobe_argv(media_path: str) -> list:
    return [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]


def probe_video(media_path: str) -> dict:
    """Return {ok, width, height, duration_s, fps, frame_count, bitrate_bps,
    error}. Runs ffprobe in a subprocess with no stdin."""
    try:
        proc = subprocess.run(
            _ffprobe_argv(media_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=FFPROBE_TIMEOUT_S,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "ffprobe not on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ffprobe timeout"}
    if proc.returncode != 0:
        return {"ok": False, "error": "ffprobe failed (undecodable or corrupt video)"}
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return {"ok": False, "error": "ffprobe output unparseable"}
    vstream = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            vstream = s
            break
    fmt = data.get("format", {}) or {}
    if vstream is None:
        return {"ok": False, "error": "no video stream found"}
    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)

    def _rate(value: Optional[str]) -> float:
        if not value:
            return 0.0
        try:
            if "/" in value:
                num, den = value.split("/", 1)
                den = float(den)
                return float(num) / den if den else 0.0
            return float(value)
        except Exception:
            return 0.0

    fps = _rate(vstream.get("avg_frame_rate")) or _rate(vstream.get("r_frame_rate"))
    duration_s = 0.0
    try:
        duration_s = float(fmt.get("duration") or vstream.get("duration") or 0.0)
    except Exception:
        duration_s = 0.0
    frame_count = 0
    try:
        frame_count = int(vstream.get("nb_frames") or 0)
    except Exception:
        frame_count = 0
    if not frame_count and duration_s > 0 and fps > 0:
        frame_count = int(round(duration_s * fps))
    bitrate_bps = 0.0
    try:
        bitrate_bps = float(fmt.get("bit_rate") or 0.0)
    except Exception:
        bitrate_bps = 0.0
    size_bytes = 0
    try:
        size_bytes = int(fmt.get("size") or 0)
    except Exception:
        size_bytes = 0
    if not bitrate_bps and duration_s > 0 and size_bytes > 0:
        bitrate_bps = (size_bytes * 8.0) / duration_s
    return {
        "ok": width > 0 and height > 0,
        "width": width,
        "height": height,
        "duration_s": duration_s,
        "fps": fps,
        "frame_count": frame_count,
        "bitrate_bps": bitrate_bps,
        "size_bytes": size_bytes,
        "error": None if width > 0 and height > 0 else "missing video dimensions",
    }


def check_image_in_subprocess(media_path: str) -> dict:
    """Spawn `python -m uapvf.media_check <path>` and parse its JSON.

    The child interpreter is the same as the server process (`sys.executable`).
    In development/test environments the package is often run from `src/` via
    PYTHONPATH rather than installed, so we propagate the current ``sys.path``
    to the child so it can import ``uapvf`` regardless of how the parent was
    launched.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "uapvf.media_check", str(media_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=IMAGE_CHECK_TIMEOUT_S,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "image check timeout"}
    except FileNotFoundError:
        return {"ok": False, "error": "python interpreter not found"}
    try:
        payload = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except Exception:
        payload = {}
    payload["ok"] = bool(payload.get("ok")) and proc.returncode == 0
    if not payload.get("error") and proc.returncode != 0:
        payload["error"] = (
            proc.stderr.decode("utf-8", errors="replace")[:500]
            or "image decode failed"
        )
    return payload


def _check_image_direct(media_path: str) -> dict:
    """The in-child implementation used by `python -m uapvf.media_check`."""
    try:
        from PIL import Image

        with Image.open(media_path) as im:
            im.verify()
        with Image.open(media_path) as im:
            width, height = im.size
            has_exif = False
            try:
                has_exif = bool(im.getexif()) or "exif" in (im.info or {})
            except Exception:
                has_exif = False
            return {
                "ok": width > 0 and height > 0,
                "width": width,
                "height": height,
                "has_exif": has_exif,
                "format": (im.format or "").lower(),
                "error": None,
            }
    except Exception as exc:
        return {"ok": False, "error": f"image decode failed: {exc.__class__.__name__}"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(json.dumps({"ok": False, "error": "usage: media_check <path>"}))
        return 2
    result = _check_image_direct(argv[0])
    print(json.dumps(result))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
