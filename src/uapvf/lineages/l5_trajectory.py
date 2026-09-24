"""l5-trajectory: motion-dynamics lineage over sampled video frames
(spec §3.2, 60 uniformly sampled frames).

Tracks the brightest region's centroid across ffmpeg-extracted sample
frames and measures displacement, velocity stability, and direction
reversals. Images (single frame) carry no trajectory evidence — l5
abstains. Zero shared implementation code with other lineages.
"""
from __future__ import annotations

from uapvf.lineages.evidence_vocabulary import load_vocabulary, select_claims
from uapvf.lineages.frame_sampling import LINEAGE_FRAME_COUNTS, uniform_indices
from uapvf.lineages.protocol import (
    EvidenceClaims,
    LineageOutput,
    STATUS_OK,
    make_non_comparable,
)

LINEAGE_ID = "l5-trajectory"

_FRAME_SIDE = 96             # analysis thumbnail side
_SAMPLE_FRAMES = 12          # extracted thumbnails (uniform indices below)
_MIN_TRACK_HITS = 6          # centroid detections needed for a trajectory
_DISPLACEMENT_PX = 3.0       # total path length (px) => motion detected
_REVERSAL_COUNT = 2          # sign changes in dx => periodic behavior


class Lineage:
    lineage_id = LINEAGE_ID
    frames_required = LINEAGE_FRAME_COUNTS[LINEAGE_ID]

    def run(self, case_id: str, media_path: str, case_dir: str) -> LineageOutput:
        from pathlib import Path

        if Path(media_path).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            return make_non_comparable(
                LINEAGE_ID, "single_frame_media_has_no_trajectory")
        try:
            track = _track_centroids(case_id, media_path, case_dir)
        except Exception as exc:
            return make_non_comparable(
                LINEAGE_ID, f"trajectory_unreadable:{type(exc).__name__}")
        if track is None or track["hits"] < _MIN_TRACK_HITS:
            return make_non_comparable(LINEAGE_ID)

        supported_shared = []
        if track["motion"]:
            supported_shared.append("motion_detected")
            supported_shared.append("velocity_profile")
        if track["consistent"]:
            supported_shared.append("temporal_consistency")
        if track["periodic"]:
            supported_shared.append("periodic_behavior")

        supported_specific = []
        if track["steady"]:
            supported_specific.append("steady_trajectory_detected")
        if track["hover"]:
            supported_specific.append("hover_behavior_detected")
        if track["ballistic"]:
            supported_specific.append("ballistic_trajectory_detected")

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
            confidence=round(min(0.9, 0.35 + 0.05 * track["hits"]), 4),
            defense_claims=[],
            rationale=(
                f"{track['hits']} tracked samples; path length "
                f"{track['path_length']:.1f}px on a {_FRAME_SIDE}px grid; "
                f"{track['reversals']} direction reversals"
            ),
            provenance={
                "sample_frames": track["sample_count"],
                "frame_indices_used": track["frame_indices"],
                "path_length_px": round(track["path_length"], 4),
                "reversals": track["reversals"],
                "thresholds": {
                    "displacement_px": _DISPLACEMENT_PX,
                    "min_track_hits": _MIN_TRACK_HITS,
                },
            },
            frame_indices_used=track["frame_indices"],
        )


def _probe_video(media_path: str) -> dict:
    import json
    import subprocess

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(media_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=60,
    )
    if proc.returncode != 0:
        return {}
    try:
        payload = json.loads(proc.stdout.decode("utf-8", "replace"))
    except Exception:
        return {}
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "video":
            frames = int(stream.get("nb_frames") or 0)
            return {"frames": frames}
    return {}


def _track_centroids(case_id: str, media_path: str, case_dir: str):
    import shutil
    import subprocess
    from pathlib import Path

    probe = _probe_video(media_path)
    total_frames = int(probe.get("frames") or 0)
    if total_frames <= 0:
        return None
    indices = uniform_indices(total_frames, LINEAGE_FRAME_COUNTS[LINEAGE_ID])
    # Extract a bounded thumbnail set (uniform over the declared budget).
    step = max(1, len(indices) // _SAMPLE_FRAMES)
    extract = indices[::step][:_SAMPLE_FRAMES]
    if not extract:
        extract = indices[:1]
    workdir = Path(case_dir) / ".l5_frames"
    workdir.mkdir(parents=True, exist_ok=True)
    thumbnails = []
    try:
        for pos, idx in enumerate(extract):
            out = workdir / f"t{pos:02d}.png"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(media_path),
                 "-vf", f"select=eq(n\\,{idx}),scale={_FRAME_SIDE}:-1",
                 "-frames:v", "1", "-f", "image2", str(out)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=60,
            )
            if proc.returncode == 0 and out.is_file():
                thumbnails.append((idx, out))
        if not thumbnails:
            return None
        return _measure(thumbnails, extract)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _measure(thumbnails, extract):
    from PIL import Image

    centroids = []
    frame_indices = []
    for idx, path in thumbnails:
        with Image.open(path) as im:
            gray = im.convert("L")
            w, h = gray.size
            px = list(gray.getdata())
        n = len(px)
        mean = sum(px) / float(n)
        var = sum((p - mean) ** 2 for p in px) / float(n)
        threshold = mean + 2.0 * (var ** 0.5)
        xs = [i % w for i, p in enumerate(px) if p > threshold]
        ys = [i // w for i, p in enumerate(px) if p > threshold]
        if xs:
            centroids.append((sum(xs) / len(xs), sum(ys) / len(ys)))
            frame_indices.append(idx)
    hits = len(centroids)
    if hits < 2:
        return {"hits": hits, "sample_count": len(thumbnails),
                "frame_indices": frame_indices, "path_length": 0.0,
                "motion": False, "consistent": hits >= _MIN_TRACK_HITS,
                "periodic": False, "steady": False, "hover": False,
                "ballistic": False, "reversals": 0}
    path_length = 0.0
    steps = []
    for i in range(1, hits):
        dx = centroids[i][0] - centroids[i - 1][0]
        dy = centroids[i][1] - centroids[i - 1][1]
        dist = (dx * dx + dy * dy) ** 0.5
        path_length += dist
        steps.append((dx, dy, dist))
    mean_step = path_length / max(1, hits - 1)
    var_step = sum((s[2] - mean_step) ** 2 for s in steps) / max(1, len(steps))
    reversals = 0
    for i in range(1, len(steps)):
        if steps[i][0] * steps[i - 1][0] < 0 and abs(steps[i][0]) > 0.5:
            reversals += 1
    net = ((centroids[-1][0] - centroids[0][0]) ** 2 +
           (centroids[-1][1] - centroids[0][1]) ** 2) ** 0.5
    motion = path_length > _DISPLACEMENT_PX
    return {
        "hits": hits,
        "sample_count": len(thumbnails),
        "frame_indices": frame_indices,
        "path_length": path_length,
        "motion": motion,
        "consistent": hits >= _MIN_TRACK_HITS,
        "periodic": reversals >= _REVERSAL_COUNT,
        "steady": motion and (var_step ** 0.5) < max(0.5, 0.4 * mean_step),
        "hover": not motion and hits >= _MIN_TRACK_HITS,
        "ballistic": motion and net > 0.8 * path_length and reversals == 0,
        "reversals": reversals,
    }
