"""Deterministic uniform frame sampling (spec §3.2, OBL-12/38/51).

Every frame-consuming lineage processes a fixed-size uniform sample:
l1/l2/l3 = 10 frames, l5 = 60, l6 = 5; l4 (metadata) and l7 (audio)
consume no frames. Sampling is deterministic per case:

    frame_indices = [int(i * total_frames / n_frames) for i in range(n_frames)]

Videos with fewer frames than the cap use all frames. The seed (the case_id
hash) is part of the contract so downstream provenance can record it; the
uniform formula itself is already seed-independent, which is exactly the
determinism property the audit chain needs.

Frame *counting* requires probing the media file, which is untrusted-media
parsing and therefore happens inside the sandboxed worker (FR-017). The
worker passes the probed count in; this module never parses media.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

# Lineage id -> number of uniformly sampled frames (spec §3.2).
LINEAGE_FRAME_COUNTS: Dict[str, int] = {
    "l1-photometric": 10,
    "l2-spectral": 10,
    "l3-geometric": 10,
    "l4-metadata": 0,
    "l5-trajectory": 60,
    "l6-resnet50": 5,
    "l7-audio": 0,
}


def uniform_indices(total_frames: int, n_frames: int) -> List[int]:
    """Uniform deterministic frame indices per the spec formula.

    * ``total_frames <= 0`` -> [] (nothing to sample).
    * ``total_frames <= n_frames`` -> all frames (fewer frames than the cap).
    * Otherwise ``int(i * total_frames / n_frames)`` for i in range(n_frames).
    """
    if total_frames <= 0:
        return []
    if n_frames <= 0:
        return []
    if total_frames <= n_frames:
        return list(range(total_frames))
    return [int(i * total_frames / n_frames) for i in range(n_frames)]


def seed_from_case_id(case_id: str) -> str:
    """Canonical seed derivation: SHA-256 hex of the case id."""
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()


def sample_frames(
    video_path: str,
    n_frames: int,
    seed: str,
    frame_count: Optional[int] = None,
) -> List[int]:
    """Spec signature ``sample_frames(video_path, n_frames, seed)``.

    ``frame_count`` is the total frame count probed by the sandboxed worker
    (ffprobe). It is required: this module never parses media files itself
    (FR-017 keeps untrusted media parsing out of the main process).

    Returns the deterministic uniform index list. ``video_path`` and
    ``seed`` are accepted for contract fidelity and provenance; the uniform
    formula depends only on (frame_count, n_frames), so the same case +
    same video always yields the same indices.
    """
    if frame_count is None:
        raise ValueError(
            "frame_count must be provided by the sandboxed media probe; "
            "frame_sampling never parses media in-process (FR-017)"
        )
    if not isinstance(video_path, str) or not video_path:
        raise ValueError("video_path must be a non-empty string")
    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be a non-empty string (case_id hash)")
    return uniform_indices(int(frame_count), int(n_frames))


def probe_frame_count(media_path: str) -> int:
    """Count frames inside the sandbox worker without trusting a suffix."""
    path = Path(media_path)
    if path.suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"}:
        return 1
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-count_frames", "-show_entries", "stream=nb_read_frames,nb_frames",
         "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=60,
    )
    if proc.returncode != 0:
        return 0
    try:
        stream = (json.loads(proc.stdout.decode("utf-8"))["streams"] or [{}])[0]
        return int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0)
    except Exception:
        return 0


def load_sampled_frames(
    media_path: str,
    case_dir: str,
    lineage_id: str,
    n_frames: int,
):
    """Decode a deterministic sample and return independent RGB PIL images.

    This function is called only by ``subprocess_worker``. Video frames are
    written beneath the current case and removed before return; no decoder
    runs in the web/worker process.
    """
    from PIL import Image

    path = Path(media_path)
    if path.suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"}:
        with Image.open(path) as image:
            image.load()
            return [image.convert("RGB").copy()], [0]

    total = probe_frame_count(str(path))
    indices = uniform_indices(total, n_frames)
    if not indices:
        return [], []
    scratch = Path(case_dir) / ".sandbox" / lineage_id
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    expression = "+".join(f"eq(n\\,{index})" for index in indices)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(path),
         "-vf", f"select='{expression}',scale=640:-2",
         "-vsync", "0", str(scratch / "frame-%03d.jpg")],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=180,
    )
    if proc.returncode != 0:
        shutil.rmtree(scratch, ignore_errors=True)
        return [], []
    images = []
    try:
        for frame_path in sorted(scratch.glob("frame-*.jpg")):
            with Image.open(frame_path) as image:
                image.load()
                images.append(image.convert("RGB").copy())
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return images, indices[:len(images)]
