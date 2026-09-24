"""l6-resnet50: operator-installed CNN classification lineage (spec §3.2).

Runs ONLY when the four operator-supplied ResNet50 artifacts (ONNX weights,
ImageNet-taxonomy mapping, install receipt, trusted public key) are
configured and the receipt verifies through ``uapvf.model_weights``.
Missing/unverified artifacts -> status ``unavailable`` (fail closed: an
untrusted model is never substituted, and no claims are fabricated).

ONNX inference is imported lazily inside ``run`` so module import works in
environments without onnxruntime. Zero shared implementation code with
other lineages.
"""
from __future__ import annotations

from uapvf.lineages.evidence_vocabulary import load_vocabulary, select_claims
from uapvf.lineages.frame_sampling import LINEAGE_FRAME_COUNTS
from uapvf.lineages.protocol import (
    EvidenceClaims,
    LineageOutput,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    make_non_comparable,
)

LINEAGE_ID = "l6-resnet50"

_CONFIDENCE_FLOOR = 0.35     # softmax top-1 floor for a confident call
_POINT_FRACTION = 0.005
_EXTENDED_FRACTION = 0.02


class Lineage:
    lineage_id = LINEAGE_ID
    frames_required = LINEAGE_FRAME_COUNTS[LINEAGE_ID]

    def run(self, case_id: str, media_path: str, case_dir: str) -> LineageOutput:
        artifacts = _artifact_paths()
        if artifacts is None:
            return LineageOutput(
                lineage_id=LINEAGE_ID,
                status=STATUS_UNAVAILABLE,
                rationale=("resnet50 artifacts not configured or receipt "
                           "verification unavailable"),
            )
        try:
            result = _classify(case_id, media_path, case_dir, artifacts)
        except Exception as exc:
            return LineageOutput(
                lineage_id=LINEAGE_ID,
                status=STATUS_UNAVAILABLE,
                rationale=f"resnet50 inference failed: {type(exc).__name__}",
            )
        if result is None:
            return make_non_comparable(LINEAGE_ID)

        supported_shared = []
        if result["point"]:
            supported_shared.append("point_source")
        elif result["extended"]:
            supported_shared.append("extended_source")
        if result["bright"]:
            supported_shared.append("luminous")
        if result["sharp"]:
            supported_shared.append("sharp_boundary")
        else:
            supported_shared.append("diffuse_boundary")
        if result["symmetric"]:
            supported_shared.append("symmetric_form")
        else:
            supported_shared.append("asymmetric_form")
        if result["class_anomalous"]:
            supported_shared.append("anomalous_signature")

        supported_specific = []
        if result["confident"]:
            supported_specific.append("cnn_classification_confident")
            supported_specific.append("cnn_feature_pattern_matched")

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
            confidence=round(min(0.95, result["top1"]), 4),
            defense_claims=[],
            rationale=(
                f"resnet50 top-1 {result['top_class']!r} "
                f"(p={result['top1']:.3f}) on {result['frames']} sampled "
                "frames"
            ),
            provenance={
                "model_id": result["model_id"],
                "weights_sha256": result["weights_sha256"],
                "top_class": result["top_class"],
                "top1_probability": round(result["top1"], 6),
                "frames": result["frames"],
            },
            frame_indices_used=result["frame_indices"],
        )


def _artifact_paths():
    import os

    keys = ("UAPV_RESNET50_ONNX", "UAPV_RESNET50_MAPPING",
            "UAPV_RESNET50_RECEIPT", "UAPV_RESNET50_TRUSTED_PUBLIC_KEY")
    values = {k: os.environ.get(k) for k in keys}
    if not all(values.values()):
        return None
    try:
        from uapvf.model_weights import verify_resnet50

        status = verify_resnet50(
            weights=values["UAPV_RESNET50_ONNX"],
            mapping=values["UAPV_RESNET50_MAPPING"],
            receipt=values["UAPV_RESNET50_RECEIPT"],
            trusted_public_key=values["UAPV_RESNET50_TRUSTED_PUBLIC_KEY"],
        )
    except Exception:
        return None
    if not status.get("verified"):
        return None
    return {"onnx": values["UAPV_RESNET50_ONNX"],
            "mapping": values["UAPV_RESNET50_MAPPING"],
            "model_id": status.get("model_id"),
            "weights_sha256": status.get("weights_sha256")}


def _first_frames(case_id: str, media_path: str, case_dir: str, count: int):
    from pathlib import Path

    path = Path(media_path)
    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return [str(path)], [0]
    workdir = Path(case_dir) / ".l6_frames"
    workdir.mkdir(parents=True, exist_ok=True)
    frames, indices = [], []
    try:
        import subprocess

        for pos in range(count):
            out = workdir / f"f{pos}.png"
            vf = f"select=eq(n\\,{pos * 30}),scale=224:224"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(path),
                 "-vf", vf, "-frames:v", "1", "-f", "image2", str(out)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=60,
            )
            if proc.returncode == 0 and out.is_file():
                frames.append(str(out))
                indices.append(pos)
    except Exception:
        pass
    return frames, indices


def _classify(case_id: str, media_path: str, case_dir: str, artifacts: dict):
    import json

    import numpy as np
    from PIL import Image

    try:
        import onnxruntime as ort
    except Exception:
        return None
    frames, indices = _first_frames(case_id, media_path, case_dir,
                                    LINEAGE_FRAME_COUNTS[LINEAGE_ID])
    if not frames:
        return None
    mapping = json.loads(open(artifacts["mapping"], "r",
                              encoding="utf-8").read())
    labels = mapping.get("labels") or mapping.get("classes") or []
    if not isinstance(labels, list) or not labels:
        return None

    session = ort.InferenceSession(
        artifacts["onnx"], providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    votes = {}
    probs = []
    for frame in frames:
        with Image.open(frame) as im:
            rgb = im.convert("RGB").resize((224, 224))
        arr = np.asarray(rgb, dtype=np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        arr = (arr - mean) / std
        tensor = arr.transpose(2, 0, 1)[np.newaxis, ...]
        outputs = session.run(None, {input_name: tensor})
        logits = np.asarray(outputs[0]).reshape(-1).astype(np.float64)
        exp = np.exp(logits - logits.max())
        softmax = exp / exp.sum()
        top = int(np.argmax(softmax))
        votes[top] = votes.get(top, 0) + 1
        probs.append(float(softmax[top]))
    top_class_idx = max(votes, key=lambda k: votes[k])
    top1 = sum(probs) / len(probs)
    top_class = str(labels[top_class_idx]) if top_class_idx < len(labels) else (
        f"class_{top_class_idx}")

    # Geometry on the first frame for the shared visual claims.
    with Image.open(frames[0]) as im:
        gray = im.convert("L").resize((96, 96))
        px = list(gray.getdata())
    n = len(px)
    mean = sum(px) / float(n)
    var = sum((p - mean) ** 2 for p in px) / float(n)
    threshold = mean + 2.0 * (var ** 0.5)
    bright_px = sum(1 for p in px if p > threshold)
    frac = bright_px / float(n)
    w = 96
    half = w // 2
    num = den_a = den_b = 0.0
    for y in range(w):
        for x in range(half):
            a = px[y * w + x] - mean
            b = px[y * w + (w - 1 - x)] - mean
            num += a * b
            den_a += a * a
            den_b += b * b
    mirror = num / ((den_a ** 0.5) * (den_b ** 0.5)) if den_a and den_b else 0.0
    # Edge strength on the analysis grid decides sharp vs diffuse boundary.
    std = var ** 0.5
    strong = 0
    for y in range(1, w - 1):
        for x in range(1, w - 1):
            i = y * w + x
            gx = abs(px[i + 1] - px[i - 1])
            gy = abs(px[i + w] - px[i - w])
            if (gx + gy) > 2.0 * std:
                strong += 1
    edge_fraction = strong / float((w - 2) * (w - 2))
    _NON_OBJECT_CLASSES = ("sky", "cloud", "night", "dark", "webpage",
                           "screen", "monitor")
    return {
        "top_class": top_class,
        "top1": top1,
        "confident": top1 >= _CONFIDENCE_FLOOR,
        "class_anomalous": not any(
            m in top_class.lower() for m in _NON_OBJECT_CLASSES),
        "point": frac <= _POINT_FRACTION,
        "extended": frac >= _EXTENDED_FRACTION,
        "bright": max(px) > mean * 1.5 if mean > 0 else False,
        "sharp": edge_fraction >= 0.04,
        "symmetric": mirror >= 0.75,
        "model_id": artifacts.get("model_id"),
        "weights_sha256": artifacts.get("weights_sha256"),
        "frames": len(frames),
        "frame_indices": indices,
    }
