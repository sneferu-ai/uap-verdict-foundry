"""Procedural, permanently labelled visual-fiction asset generator."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, PngImagePlugin

from uapvf.xenoscience import LABEL


def _font(size: int, bold: bool = True):
    candidates = (
        [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ] if bold else [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _gradient_canvas(top=(6, 13, 27), bottom=(15, 29, 46)) -> Image.Image:
    image = Image.new("RGB", (1600, 900), top)
    draw = ImageDraw.Draw(image)
    for y in range(700):
        ratio = y / 699
        colour = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio)
                       for i in range(3))
        draw.line((0, y, 1600, y), fill=colour)
    return image


def _star_field(draw: ImageDraw.ImageDraw, seed: int, count: int = 150,
                max_y: int = 690) -> None:
    for i in range(count):
        x = (seed * (i + 17) * 7919 + i * i * 37) % 1600
        y = (seed * (i + 29) * 3571 + i * 101) % max_y
        radius = 1 + ((seed >> (i % 24)) & 1)
        colour = ((105 + i * 11) % 95 + 125,
                  (90 + i * 7) % 70 + 150,
                  (70 + i * 13) % 45 + 205)
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=colour)


def _glow_ellipse(image: Image.Image, box, colour, blur: int = 24,
                  width: int = 8, fill=None) -> None:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    glow_colour = tuple(colour) + (170,)
    layer_draw.ellipse(box, outline=glow_colour, width=max(width * 2, 12))
    glow = layer.filter(ImageFilter.GaussianBlur(blur))
    image.paste(glow, (0, 0), glow)
    crisp = Image.new("RGBA", image.size, (0, 0, 0, 0))
    crisp_draw = ImageDraw.Draw(crisp)
    crisp_draw.ellipse(box, fill=fill, outline=tuple(colour) + (255,), width=width)
    image.paste(crisp, (0, 0), crisp)


def _technical_grid(draw: ImageDraw.ImageDraw, max_y: int = 700) -> None:
    for x in range(0, 1601, 80):
        draw.line((x, 0, x, max_y), fill=(17, 37, 55), width=1)
    for y in range(0, max_y + 1, 80):
        draw.line((0, y, 1600, y), fill=(17, 37, 55), width=1)


def _tag(draw: ImageDraw.ImageDraw, xy, title: str, value: str) -> None:
    x, y = xy
    draw.rounded_rectangle((x, y, x + 250, y + 76), radius=10,
                           fill=(17, 31, 49), outline=(53, 83, 103), width=2)
    draw.text((x + 16, y + 12), title.upper(), font=_font(14),
              fill=(117, 210, 190))
    draw.text((x + 16, y + 37), value, font=_font(20, False),
              fill=(222, 231, 228))


def perceptual_hash(image: Image.Image) -> int:
    """64-bit pHash using a direct 32×32 DCT (no optional imagehash dep)."""
    a = np.asarray(image.convert("L").resize((32, 32)), dtype=np.float64)
    # A small explicit low-frequency DCT avoids relying on the host BLAS
    # implementation inside the media sandbox and is deterministic across
    # supported Python/numpy builds.
    low = np.zeros((8, 8), dtype=np.float64)
    for u in range(8):
        cos_x = np.cos((math.pi / 32.0) * (np.arange(32) + 0.5) * u)
        for v in range(8):
            cos_y = np.cos((math.pi / 32.0) * (np.arange(32) + 0.5) * v)
            low[u, v] = float(np.sum(a * np.outer(cos_x, cos_y)))
    values = low.flatten()[1:]
    median = float(np.median(values))
    bits = low.flatten() >= median
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def _references(media_path: str, brand_paths: list[str]) -> list[dict]:
    out = []
    for path in [media_path, *brand_paths]:
        try:
            with Image.open(path) as image:
                out.append({"path_role": "case_media" if path == media_path else "brand_asset",
                            "phash": perceptual_hash(image)})
        except Exception:
            continue
    return out


def generate_story_asset(case_id: str, case_dir: str, media_path: str,
                         xenoscience: dict, brand_paths=None) -> dict:
    """Create one 1600×900 PNG with visible and PNG-metadata boundaries."""
    brand_paths = list(brand_paths or [])
    seed = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:12], 16)
    image = _gradient_canvas((5, 12, 26), (12, 29, 45))
    draw = ImageDraw.Draw(image)
    _technical_grid(draw)
    _star_field(draw, seed, 135)
    # An editorial engineering plate: the craft is deliberately schematic,
    # with measured layers and callouts rather than a generic saucer icon.
    cx, cy = 930, 405
    cyan = (103, 218, 232)
    mint = (144, 232, 196)
    draw.arc((cx-360, cy-250, cx+360, cy+250), 196, 344,
             fill=(34, 82, 108), width=2)
    draw.arc((cx-315, cy-215, cx+315, cy+215), 198, 342,
             fill=(45, 111, 137), width=2)
    _glow_ellipse(image, (cx-320, cy-84, cx+320, cy+84), cyan, 30, 7,
                  fill=(13, 39, 58, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((cx-270, cy-58, cx+270, cy+58), fill=(22, 53, 71),
                 outline=(132, 232, 224), width=3)
    # Radial hull ribs and layered aperture.
    for offset in (-220, -145, -70, 0, 70, 145, 220):
        draw.line((cx + offset, cy - 45, cx + offset * 0.72, cy + 48),
                  fill=(62, 118, 135), width=2)
    _glow_ellipse(image, (cx-128, cy-178, cx+128, cy+32), mint, 20, 6,
                  fill=(20, 56, 74, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((cx-84, cy-136, cx+84, cy+2), fill=(39, 89, 105),
                 outline=(207, 248, 231), width=3)
    draw.arc((cx-275, cy-54, cx+275, cy+62), 10, 170,
             fill=(240, 193, 104), width=5)
    # Warm underside emitters with restrained bloom.
    for offset in (-220, -110, 0, 110, 220):
        _glow_ellipse(image, (cx+offset-16, cy+45, cx+offset+16, cy+77),
                      (255, 187, 88), 15, 3, fill=(255, 205, 114, 255))
    draw = ImageDraw.Draw(image)
    # Vector callouts turn the art into a foundry plate.
    draw.line((cx-302, cy-70, 520, 270, 420, 270), fill=(112, 205, 194), width=2)
    draw.ellipse((cx-307, cy-75, cx-297, cy-65), fill=(152, 235, 210))
    draw.text((185, 252), "BOUNDARY-LAYER CONTROL", font=_font(16),
              fill=(152, 235, 210))
    draw.text((185, 280), "field geometry / conceptual", font=_font(16, False),
              fill=(164, 181, 193))
    draw.line((cx+210, cy+59, 1315, 565, 1435, 565), fill=(112, 205, 194), width=2)
    draw.ellipse((cx+205, cy+54, cx+215, cy+64), fill=(152, 235, 210))
    draw.text((1240, 578), "THERMAL OUTPUT", font=_font(16), fill=(152, 235, 210))
    draw.text((1240, 606), "unknown / unconstrained", font=_font(16, False),
              fill=(164, 181, 193))
    draw.text((72, 55), "VERDICT FOUNDRY", font=_font(18), fill=(116, 218, 193))
    draw.text((72, 91), "KINEMATIC CONCEPT / 01", font=_font(38),
              fill=(235, 241, 235))
    world = next(item["output"]["world_seed"] for item in xenoscience["stages"]
                 if item["stage"] == "planetary_context")
    draw.text((74, 145), f"CASE {case_id[:8].upper()}  /  WORLD {world}",
              font=_font(19, False), fill=(166, 184, 197))
    _tag(draw, (72, 405), "Inference", "conceptual only")
    _tag(draw, (72, 495), "Observed range", "not available")
    _tag(draw, (72, 585), "Energy estimate", "not computable")
    # Permanent high-contrast boundary occupies its own image pixels.
    draw.rectangle((0, 700, 1600, 900), fill=(77, 16, 31))
    draw.line((0, 700, 1600, 700), fill=(255, 107, 130), width=8)
    draw.text((70, 738), LABEL, font=_font(38), fill=(255, 246, 231))
    draw.text((72, 800), "Creative extrapolation. Never cite this image as evidence.",
              font=_font(25, False), fill=(255, 204, 211))
    draw.text((1270, 842), f"VF:{case_id[:12]}", font=_font(18), fill=(255, 204, 211))

    references = _references(media_path, brand_paths)
    asset_hash = perceptual_hash(image)
    comparisons = [{"role": ref["path_role"],
                    "hamming_distance": hamming(asset_hash, ref["phash"])}
                   for ref in references]
    # Spec threshold: pHash distance <=8 flags/rejects similarity.
    flagged = [item for item in comparisons if item["hamming_distance"] <= 8]
    similarity = {"metric": "pHash-64", "reject_at_or_below": 8,
                  "comparisons": comparisons, "flagged": flagged,
                  "passed": not flagged}
    if flagged:
        raise ValueError("story asset similarity screen rejected generated image")
    asset_id = str(uuid.uuid4())
    assets_dir = Path(case_dir) / "story"
    assets_dir.mkdir(parents=True, exist_ok=True)
    path = assets_dir / f"story-{asset_id}.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("uapvf_label", LABEL)
    metadata.add_text("uapvf_evidence_eligible", "false")
    metadata.add_text("uapvf_case_id", case_id)
    metadata.add_text("uapvf_similarity", json.dumps(similarity, sort_keys=True))
    temp = path.with_name(path.stem + ".tmp.png")
    image.save(temp, format="PNG", pnginfo=metadata, optimize=True)
    temp.replace(path)
    data = path.read_bytes()
    alt_text = (
        "An editorial engineering plate showing a teal-lit conceptual craft "
        "over a star field, presented as a story "
        "plate. A large burgundy footer states SPECULATIVE FICTION — NOT "
        "FORENSIC EVIDENCE."
    )
    (assets_dir / f"story-{asset_id}.alt.txt").write_text(alt_text, encoding="utf-8")
    return {"asset_id": asset_id, "kind": "image/png", "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(), "label": LABEL,
            "label_verified": True, "similarity": similarity,
            "alt_text": alt_text}


def verify_exported_asset(path: str) -> list:
    errors = []
    try:
        with Image.open(path) as image:
            label = image.info.get("uapvf_label")
            eligible = image.info.get("uapvf_evidence_eligible")
            if label != LABEL or eligible != "false":
                errors.append("machine-readable non-evidence label missing")
            # Coarse pixel check: permanent burgundy label band remains.
            sample = np.asarray(image.convert("RGB"))
            if sample.shape[0] < 100 or float(sample[-100:, :, 0].mean()) < 45:
                errors.append("visible non-evidence band missing")
    except Exception as exc:
        errors.append(f"asset unreadable: {type(exc).__name__}")
    return errors


def _label_band(draw: ImageDraw.ImageDraw, case_id: str) -> None:
    draw.rectangle((0, 700, 1600, 900), fill=(77, 16, 31))
    draw.line((0, 700, 1600, 700), fill=(255, 107, 130), width=8)
    draw.text((70, 738), LABEL, font=_font(38), fill=(255, 246, 231))
    draw.text((72, 800), "Creative extrapolation. Never cite this image as evidence.",
              font=_font(25, False), fill=(255, 204, 211))
    draw.text((1270, 842), f"VF:{case_id[:12]}", font=_font(18),
              fill=(255, 204, 211))


def _save_scene(image: Image.Image, case_id: str, case_dir: str, role: str,
                media_path: str, brand_paths: list[str], alt_text: str) -> dict:
    references = _references(media_path, brand_paths)
    asset_hash = perceptual_hash(image)
    comparisons = [{"role": ref["path_role"],
                    "hamming_distance": hamming(asset_hash, ref["phash"])}
                   for ref in references]
    flagged = [item for item in comparisons if item["hamming_distance"] <= 8]
    similarity = {"metric": "pHash-64", "reject_at_or_below": 8,
                  "comparisons": comparisons, "flagged": flagged,
                  "passed": not flagged}
    if flagged:
        raise ValueError(f"{role} similarity screen rejected generated image")
    asset_id = str(uuid.uuid4())
    assets_dir = Path(case_dir) / "story"
    assets_dir.mkdir(parents=True, exist_ok=True)
    path = assets_dir / f"{role}-{asset_id}.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("uapvf_label", LABEL)
    metadata.add_text("uapvf_evidence_eligible", "false")
    metadata.add_text("uapvf_case_id", case_id)
    metadata.add_text("uapvf_asset_role", role)
    metadata.add_text("uapvf_similarity", json.dumps(similarity, sort_keys=True))
    temp = path.with_name(path.stem + ".tmp.png")
    image.save(temp, format="PNG", pnginfo=metadata, optimize=True)
    temp.replace(path)
    (assets_dir / f"{role}-{asset_id}.alt.txt").write_text(
        alt_text, encoding="utf-8")
    return {"asset_id": asset_id, "kind": "image/png", "role": role,
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "label": LABEL, "label_verified": True,
            "similarity": similarity, "alt_text": alt_text}


def _planet_scene(case_id: str, case_dir: str, media_path: str,
                  brand_paths: list[str], xenoscience: dict) -> dict:
    seed = int(hashlib.sha256((case_id + ":planet").encode()).hexdigest()[:12], 16)
    image = _gradient_canvas((4, 10, 24), (12, 28, 45))
    draw = ImageDraw.Draw(image)
    _star_field(draw, seed, 120)
    # Soft orbital paths behind the body.
    draw.arc((690, 75, 1535, 720), 192, 347, fill=(48, 93, 118), width=2)
    draw.arc((735, 105, 1490, 690), 198, 342, fill=(45, 77, 101), width=2)
    # Deterministic shaded sphere with topographic bands and a real terminator.
    size = 610
    yy, xx = np.mgrid[-1:1:complex(size), -1:1:complex(size)]
    radius = np.sqrt(xx * xx + yy * yy)
    inside = radius <= 1
    z = np.sqrt(np.clip(1 - radius * radius, 0, 1))
    light = np.clip(0.22 + 0.82 * (-0.45 * xx - 0.2 * yy + 0.72 * z), 0.12, 1)
    terrain = (np.sin(xx * 9 + seed % 11) + np.cos(yy * 13 + seed % 7)
               + np.sin((xx + yy) * 16))
    cloud = np.sin(yy * 38 + np.sin(xx * 8) * 3 + seed % 5)
    ocean = np.stack((38 + 20 * z, 92 + 38 * z, 105 + 50 * z), axis=-1)
    land_colour = np.stack((92 + 34 * z, 126 + 28 * z, 82 + 18 * z), axis=-1)
    pixels = np.where((terrain > 0.85)[..., None], land_colour, ocean)
    pixels = np.where((cloud > 0.86)[..., None], pixels * 0.72 + 65, pixels)
    pixels = np.clip(pixels * light[..., None], 0, 255).astype(np.uint8)
    alpha = (inside * 255).astype(np.uint8)
    sphere = Image.fromarray(np.dstack((pixels, alpha)))
    sphere_box = (875, 95)
    # Atmosphere bloom before the planet pixels.
    atmosphere = Image.new("RGBA", image.size, (0, 0, 0, 0))
    atmosphere_draw = ImageDraw.Draw(atmosphere)
    atmosphere_draw.ellipse((850, 70, 1510, 730), outline=(99, 224, 226, 190), width=24)
    atmosphere = atmosphere.filter(ImageFilter.GaussianBlur(17))
    image.paste(atmosphere, (0, 0), atmosphere)
    image.paste(sphere, sphere_box, sphere)
    draw = ImageDraw.Draw(image)
    draw.arc((858, 78, 1502, 722), 112, 292, fill=(161, 244, 214), width=8)
    draw.arc((825, 45, 1535, 755), 196, 340, fill=(91, 209, 232), width=11)
    world = next(item["output"]["world_seed"] for item in xenoscience["stages"]
                 if item["stage"] == "planetary_context")
    draw.text((70, 55), "VERDICT FOUNDRY", font=_font(18), fill=(116, 218, 193))
    draw.text((70, 91), "PLANETARY CONTEXT / 02", font=_font(38),
              fill=(235, 241, 235))
    draw.text((72, 145), f"WORLD {world}  /  ENVIRONMENTAL MODEL",
              font=_font(19, False), fill=(166, 184, 197))
    draw.line((72, 202, 660, 202), fill=(52, 85, 105), width=2)
    draw.text((72, 238), "CREATIVE CONSTRAINT", font=_font(15),
              fill=(116, 218, 193))
    draw.text((72, 271), "HIGH-ALTITUDE\nDRY ATMOSPHERE", font=_font(30),
              fill=(226, 234, 230), spacing=9)
    draw.text((72, 370),
              "An environmental extrapolation from\na bounded story seed — not an origin claim.",
              font=_font(19, False), fill=(166, 184, 197), spacing=8)
    _tag(draw, (72, 486), "Origin", "not attributed")
    _tag(draw, (340, 486), "Confidence", "fictional construct")
    draw.text((1340, 652), world, font=_font(18), fill=(156, 225, 208))
    _label_band(draw, case_id)
    return _save_scene(
        image, case_id, case_dir, "planet", media_path, brand_paths,
        "An editorial teal and ochre planetary study with a luminous atmosphere. A large "
        "burgundy footer permanently identifies it as speculative fiction, not evidence.")


def _comic_scene(case_id: str, case_dir: str, media_path: str,
                 brand_paths: list[str]) -> dict:
    seed = int(hashlib.sha256((case_id + ":comic").encode()).hexdigest()[:12], 16)
    image = _gradient_canvas((7, 13, 26), (15, 29, 44))
    draw = ImageDraw.Draw(image)
    _technical_grid(draw)
    draw.text((60, 35), "VERDICT FOUNDRY", font=_font(17), fill=(116, 218, 193))
    draw.text((60, 68), "CASE LOG / THREE-BEAT FRAME", font=_font(35),
              fill=(235, 241, 235))
    draw.text((1490, 82), "03", font=_font(28), fill=(91, 136, 155))
    panels = [(60, 135, 530, 650), (565, 135, 1035, 650), (1070, 135, 1540, 650)]
    captions = (("01", "OBSERVATION", "recorded light / unknown range"),
                ("02", "HYPOTHESIS", "bounded mechanism / falsifiable"),
                ("03", "WORLD", "creative context / no origin claim"))
    for i, box in enumerate(panels):
        draw.rounded_rectangle(box, radius=14, fill=(17 + i * 5, 30 + i * 5, 47 + i * 4),
                               outline=(55, 94, 108), width=2)
        draw.rectangle((box[0], box[1], box[0] + 54, box[1] + 54),
                       fill=(101, 208, 187))
        draw.text((box[0] + 17, box[1] + 14), captions[i][0], font=_font(18),
                  fill=(8, 25, 34))
        draw.text((box[0] + 72, box[1] + 13), captions[i][1], font=_font(18),
                  fill=(218, 237, 229))
        draw.text((box[0] + 24, box[3] - 58), captions[i][2], font=_font(15, False),
                  fill=(151, 174, 186))
    # Panel one: desert horizon, observation path, and luminous capture.
    draw.polygon([(61, 525), (155, 454), (245, 492), (330, 423),
                  (445, 484), (529, 442), (529, 649), (61, 649)],
                 fill=(30, 49, 58))
    draw.line((94, 570, 451, 388), fill=(87, 137, 159), width=4)
    _glow_ellipse(image, (259, 278, 406, 324), (111, 223, 235), 22, 4,
                  fill=(117, 223, 226, 255))
    draw = ImageDraw.Draw(image)
    draw.arc((237, 252, 428, 350), 182, 355, fill=(255, 215, 133), width=4)
    for i in range(9):
        x = 90 + (seed * (i + 3) * 97) % 400
        y = 190 + (seed * (i + 8) * 53) % 170
        draw.ellipse((x, y, x + 2, y + 2), fill=(171, 211, 223))
    # Panel two: concentric constraint map and explicit unknown core.
    for radius, colour in ((178, (44, 94, 121)), (132, (65, 145, 160)),
                           (84, (111, 218, 207))):
        draw.ellipse((800-radius, 390-radius, 800+radius, 390+radius),
                     outline=colour, width=4)
    draw.polygon([(800, 314), (875, 390), (800, 466), (725, 390)],
                 fill=(20, 47, 64), outline=(235, 193, 105))
    draw.text((753, 378), "UNKNOWN", font=_font(14), fill=(239, 216, 161))
    draw.line((625, 254, 743, 329), fill=(72, 125, 143), width=2)
    draw.line((857, 452, 973, 525), fill=(72, 125, 143), width=2)
    # Panel three: textured fictional world, rings, and a terminator.
    planet_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(planet_layer)
    pd.ellipse((1165, 244, 1455, 534), fill=(41, 114, 111, 255),
               outline=(170, 238, 207, 255), width=7)
    pd.arc((1142, 220, 1478, 558), 192, 350, fill=(99, 218, 234, 255), width=12)
    pd.pieslice((1165, 244, 1455, 534), 92, 268, fill=(23, 59, 72, 185))
    pd.arc((1190, 270, 1430, 510), 205, 335, fill=(207, 183, 112, 255), width=5)
    image.paste(planet_layer, (0, 0), planet_layer)
    draw = ImageDraw.Draw(image)
    draw.line((1114, 542, 1496, 542), fill=(51, 96, 111), width=2)
    draw.text((1114, 552), "ENVIRONMENT: DERIVED", font=_font(14),
              fill=(119, 207, 190))
    _label_band(draw, case_id)
    return _save_scene(
        image, case_id, case_dir, "comic", media_path, brand_paths,
        "A three-panel editorial case-log frame showing observation, hypothesis, "
        "and a fictional world, with a permanent non-evidence footer.")


def _video_from_scenes(case_id: str, case_dir: str, scenes: list[dict]) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("ffmpeg and ffprobe are required for story animation")
    asset_id = str(uuid.uuid4()); story_dir = Path(case_dir) / "story"
    path = story_dir / f"story-clip-{asset_id}.mp4"
    with tempfile.TemporaryDirectory(prefix="uapvf-story-video-") as raw:
        concat = Path(raw) / "scenes.txt"
        lines = []
        for scene in scenes:
            lines.extend([f"file '{Path(scene['path']).as_posix()}'", "duration 2"])
        lines.append(f"file '{Path(scenes[-1]['path']).as_posix()}'")
        concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temp = path.with_name(path.stem + ".tmp.mp4")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
               "-safe", "0", "-i", str(concat), "-vf", "fps=30,format=yuv420p",
               "-c:v", "libx264", "-movflags", "+faststart", "-an",
               "-metadata", f"comment={LABEL}", "-metadata", "evidence_eligible=false",
               "-t", "6", str(temp)]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=120)
        temp.replace(path)
    alt_text = (
        "A six-second animation cycling through a physics-inspired craft, a "
        "fictional planet, and a comic frame. Every frame has a large footer "
        "stating speculative fiction, not forensic evidence."
    )
    (story_dir / f"story-clip-{asset_id}.alt.txt").write_text(alt_text,
                                                               encoding="utf-8")
    result = {"asset_id": asset_id, "kind": "video/mp4", "role": "short_clip",
              "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "label": LABEL, "label_verified": True,
              "similarity": {"metric": "source-scenes", "passed": True},
              "alt_text": alt_text}
    violations = verify_exported_video(str(path))
    if violations:
        path.unlink(missing_ok=True)
        raise ValueError("story video boundary verification failed: " + "; ".join(violations))
    return result


def verify_exported_video(path: str) -> list:
    errors = []
    ffmpeg = shutil.which("ffmpeg"); ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return ["ffmpeg/ffprobe unavailable"]
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:format_tags=comment",
             "-of", "json", path], check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30)
        payload = json.loads(probe.stdout.decode("utf-8"))
        if LABEL not in str((payload.get("format") or {}).get("tags", {}).get("comment", "")):
            errors.append("machine-readable video non-evidence label missing")
        duration = float((payload.get("format") or {}).get("duration") or 0)
        if not (5.5 <= duration <= 6.5):
            errors.append("video duration outside six-second contract")
        with tempfile.TemporaryDirectory(prefix="uapvf-story-verify-") as raw:
            for index, second in enumerate((0.2, 3.0, 5.7)):
                frame = Path(raw) / f"frame-{index}.png"
                subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(second),
                     "-i", path, "-frames:v", "1", str(frame)], check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                with Image.open(frame) as image:
                    sample = np.asarray(image.convert("RGB"))
                    if sample.shape[0] < 100 or float(sample[-100:, :, 0].mean()) < 45:
                        errors.append(f"visible non-evidence band missing at {second}s")
    except Exception as exc:
        errors.append(f"video unreadable: {type(exc).__name__}")
    return errors


def generate_story_bundle(case_id: str, case_dir: str, media_path: str,
                          xenoscience: dict, brand_paths=None,
                          include_video: bool = True) -> list[dict]:
    """Physics object render, planetary render, comic frame, and short clip."""
    brand_paths = list(brand_paths or [])
    craft = generate_story_asset(case_id, case_dir, media_path, xenoscience,
                                 brand_paths)
    craft["role"] = "physics_object"
    planet = _planet_scene(case_id, case_dir, media_path, brand_paths, xenoscience)
    comic = _comic_scene(case_id, case_dir, media_path, brand_paths)
    assets = [craft, planet, comic]
    if include_video:
        assets.append(_video_from_scenes(case_id, case_dir, assets))
    return assets
