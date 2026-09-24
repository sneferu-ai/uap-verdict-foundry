"""One-shot hostile-media intake worker; launched only through the OS sandbox."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def run(media_path: str, case_dir: str, kind: str, quality_config: dict) -> dict:
    from uapvf.intake import (
        IMAGE_MAX_MP,
        VIDEO_MAX_DURATION_S,
        VIDEO_MIN_FRAMES,
        IntakeError,
        normalize_image,
        normalize_video,
    )
    from uapvf.media_check import _check_image_direct, probe_video
    from uapvf.quality_gate import run_quality_gate

    source = Path(media_path)
    case_root = Path(case_dir)
    target = case_root / "media" / ("working.mp4" if kind == "video" else "working.jpg")
    try:
        if kind == "video":
            probe = probe_video(str(source))
            if not probe.get("ok"):
                raise IntakeError(422, probe.get("error") or "undecodable media")
            if float(probe.get("duration_s") or 0) > VIDEO_MAX_DURATION_S:
                raise IntakeError(
                    413,
                    f"video exceeds {int(VIDEO_MAX_DURATION_S)} second duration cap",
                )
            if int(probe.get("frame_count") or 0) < VIDEO_MIN_FRAMES:
                raise IntakeError(422, "video has fewer than 10 frames")
            if int(probe.get("width") or 0) * int(probe.get("height") or 0) > IMAGE_MAX_MP:
                raise IntakeError(413, "video exceeds 20 megapixel frame cap")
            normalize_video(source, target)
        else:
            probe = _check_image_direct(str(source))
            if not probe.get("ok"):
                raise IntakeError(422, probe.get("error") or "undecodable media")
            if int(probe.get("width") or 0) * int(probe.get("height") or 0) > IMAGE_MAX_MP:
                raise IntakeError(413, "image exceeds 20 megapixel cap")
            normalize_image(source, target)
        quality = run_quality_gate(source, kind, quality_config)
        return {
            "ok": True,
            "probe": probe,
            "quality": quality,
            "normalized_path": str(target),
        }
    except IntakeError as exc:
        target.unlink(missing_ok=True)
        return {"ok": False, "status_code": exc.status_code,
                "error": exc.message}
    except Exception as exc:
        target.unlink(missing_ok=True)
        return {"ok": False, "status_code": 422,
                "error": (f"media processing failed: {type(exc).__name__}: "
                          f"{str(exc)[:240]}")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--media", required=True)
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--kind", choices=("image", "video"), required=True)
    parser.add_argument("--quality-config", required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(Path(args.quality_config).read_text(encoding="utf-8"))
    except Exception:
        config = {}
    payload = run(args.media, args.case_dir, args.kind, config)
    json.dump(payload, sys.stdout, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
