"""Structured media intake (FR-001, FR-024, part of S1/S7/API).

Full validation happens BEFORE any row is inserted. Decoder-facing work
(Pillow, ffprobe, ffmpeg, metadata, normalization, and quality scoring) runs
inside the same case-scoped OS sandbox used by the seven lineages.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple

from uapvf import audit, spend
from uapvf.config import (
    BENCHMARK_BUYER_REF,
    Settings,
    get_settings,
    parse_iso,
    utcnow_iso,
)
from uapvf.media_check import probe_video
from uapvf.media_sandbox import SandboxError, run_intake_sandboxed
from uapvf.quality_gate import load_quality_config

IMAGE_MAX_BYTES = 50 * 1024 * 1024
VIDEO_MAX_BYTES = 250 * 1024 * 1024
# FR-001: "MP4/MOV/WebM ≤ 60 s". Enforced in the sandboxed intake worker
# (lineages/subprocess_worker.py) as a 413 before any row is inserted, and
# mirrored verbatim to the SPA via web_json.py's media_caps payload.
VIDEO_MAX_DURATION_S = 60.0
VIDEO_MIN_FRAMES = 10
IMAGE_MAX_MP = 20_000_000
MIN_FREE_DISK_BYTES = 500 * 1024 * 1024
NOTES_MAX_CHARS = 4000

ALLOWED_IMAGE_EXTS = {"jpeg", "jpg", "png", "webp"}
ALLOWED_VIDEO_EXTS = {"mp4", "mov", "webm"}

REQUIRED_FIELDS = ("observed_at", "latitude", "longitude")
PAYMENT_STATUSES = ("unpaid", "paid", "comped")


class IntakeError(Exception):
    """Validation failure with the HTTP status code the surfaces should use."""

    def __init__(self, status_code: int, message: str, field_errors: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.field_errors = field_errors or {}


# ---------------------------------------------------------------------------
# MIME sniffing (magic bytes). Uses the `filetype` library when installed;
# otherwise an internal sniffer with the same contract (FR-001 / FR-017).
# ---------------------------------------------------------------------------

def _sniff_magic(header: bytes) -> Optional[str]:
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in (b"M4V ", b"qt  "):
            return "mov"
        return "mp4"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return None


def _sniff_executable(header: bytes) -> bool:
    if header.startswith(b"MZ") or header.startswith(b"\x7fELF"):
        return True
    if header.startswith(b"#!"):
        return True
    if header[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe"):
        return True  # Mach-O / universal binaries
    return False


def sniff_kind(path: Path) -> Tuple[Optional[str], bool]:
    """Return (kind, is_executable); kind in {jpeg,png,webp,mp4,mov,webm}."""
    with open(path, "rb") as fh:
        header = fh.read(64)
    if _sniff_executable(header):
        return None, True
    try:
        import filetype  # type: ignore

        guess = filetype.guess(str(path))
        if guess is not None:
            ext = guess.extension.lower()
            if ext == "jpg":
                ext = "jpeg"
            if ext in ALLOWED_IMAGE_EXTS or ext in ALLOWED_VIDEO_EXTS:
                return ext, False
    except Exception:
        pass
    return _sniff_magic(header), False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def disk_free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------

def parse_viewing_direction(value) -> Optional[Tuple[float, float]]:
    """'azimuth' or 'azimuth,elevation'. az in [0,360), el in [-90,90],
    elevation defaults to 45. Returns None for blank; raises ValueError."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = [p.strip() for p in text.split(",")]
    az = float(parts[0])
    el = float(parts[1]) if len(parts) > 1 and parts[1] else 45.0
    if not (0.0 <= az < 360.0):
        raise ValueError("azimuth must be in [0, 360)")
    if not (-90.0 <= el <= 90.0):
        raise ValueError("viewing elevation must be in [-90, 90]")
    return (az, el)


def validate_fields(fields: dict) -> Tuple[dict, dict]:
    """Validate structured intake fields. Returns (parsed, field_errors)."""
    errors: dict = {}
    parsed: dict = {}

    observed_raw = fields.get("observed_at")
    if not observed_raw:
        errors["observed_at"] = "capture datetime is required (ISO 8601 with offset)"
    else:
        try:
            dt = parse_iso(str(observed_raw))
            if "Z" not in str(observed_raw) and "+" not in str(observed_raw) and "-" not in str(observed_raw)[10:]:
                raise ValueError("missing offset")
            parsed["observed_at_dt"] = dt
            parsed["observed_at"] = dt.isoformat()
        except Exception:
            errors["observed_at"] = "capture datetime must be ISO 8601 with offset"

    for key in ("latitude", "longitude"):
        value = fields.get(key)
        if value is None or str(value).strip() == "":
            errors[key] = f"{key} is required (decimal degrees)"
            continue
        try:
            num = float(value)
        except Exception:
            errors[key] = f"{key} must be a decimal number"
            continue
        limit = 90.0 if key == "latitude" else 180.0
        if not (-limit <= num <= limit):
            errors[key] = f"{key} out of range"
        else:
            parsed[key] = num

    if "viewing_direction" in fields and fields.get("viewing_direction") not in (None, ""):
        try:
            parsed["viewing_direction"] = parse_viewing_direction(fields["viewing_direction"])
        except Exception as exc:
            errors["viewing_direction"] = str(exc)

    notes = fields.get("behavior_notes")
    if notes is not None and len(str(notes)) > NOTES_MAX_CHARS:
        errors["behavior_notes"] = f"behavior notes must be <= {NOTES_MAX_CHARS} chars"

    payment = fields.get("payment_status") or "unpaid"
    if payment not in PAYMENT_STATUSES:
        errors["payment_status"] = "payment_status must be unpaid|paid|comped"
    parsed["payment_status"] = payment

    for key in ("shape", "count", "duration_seconds", "weather", "buyer_ref", "location_text"):
        if fields.get(key) not in (None, ""):
            parsed[key] = fields[key]

    terms = fields.get("terms_accepted")
    parsed["terms_accepted"] = terms is True or str(terms).lower() == "true"

    # FR-024(1): plausible timestamp range (hard rejection).
    if "observed_at_dt" in parsed:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        dt = parsed["observed_at_dt"]
        years_past = (now - dt).total_seconds() / (365.25 * 24 * 3600)
        years_future = (dt - now).total_seconds() / (365.25 * 24 * 3600)
        if years_future > 1.0 or years_past > 50.0:
            errors["observed_at"] = "metadata timestamp out of plausible range"

    return parsed, errors


def compute_warnings(fields: dict, parsed: dict, original_path: Optional[Path],
                     kind: str, probe_result: Optional[dict] = None) -> list:
    """FR-024(2,3): null island + duration mismatch warnings (never rejects)."""
    warnings = []
    lat = parsed.get("latitude")
    lon = parsed.get("longitude")
    if lat == 0.0 and lon == 0.0:
        warnings.append(
            {
                "type": "null_island",
                "message": "coordinates at null island; verify accuracy",
            }
        )
    reported = fields.get("duration_seconds")
    if reported not in (None, "") and kind == "video" and original_path is not None:
        try:
            reported_s = float(reported)
        except Exception:
            reported_s = None
        if reported_s is not None:
            probe = probe_result or probe_video(str(original_path))
            measured = probe.get("duration_s") or 0.0
            if measured > 0 and abs(reported_s - measured) / measured > 0.5:
                warnings.append(
                    {
                        "type": "duration_mismatch",
                        "message": (
                            "user-reported duration differs significantly from "
                            "media duration"
                        ),
                    }
                )
    return warnings


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_image(src: Path, dst: Path) -> None:
    """Convert to sRGB JPEG quality 85, max 4096 px long edge."""
    from PIL import Image, ImageCms

    with Image.open(src) as im:
        im.load()
        try:
            if im.mode != "RGB":
                im = im.convert("RGB")
            icc = im.info.get("icc_profile")
            if icc:
                try:
                    src_profile = ImageCms.ImageCmsProfile(ImageCms.core.profile_frombytes(icc))
                    srgb = ImageCms.createProfile("sRGB")
                    im = ImageCms.profileToProfile(im, src_profile, srgb)
                except Exception:
                    pass
        except Exception:
            pass
        im.thumbnail((4096, 4096))
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        im.save(tmp, format="JPEG", quality=85, optimize=True)
        _fsync_rename(tmp, dst)


def normalize_video(src: Path, dst: Path) -> None:
    """Re-encode hostile input to a predictable H.264/AAC MP4.

    The temporary file deliberately retains an ``.mp4`` suffix. ffmpeg
    selects its muxer from the output suffix; the former ``working.mp4.tmp``
    path made every real video upload fail because no output format could be
    inferred. Optional audio mapping also lets silent phone/video captures
    normalize successfully.
    """
    vf = (
        "scale=w='min(1920,iw)':h='min(1080,ih)':"
        "force_original_aspect_ratio=decrease"
    )
    tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a?", "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-crf", "28", "-c:a", "aac", "-b:a", "128k",
            "-map_metadata", "-1", "-movflags", "+faststart", str(tmp),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
    )
    if proc.returncode != 0 or not tmp.exists():
        if tmp.exists():
            tmp.unlink()
        raise IntakeError(422, "video normalization failed (undecodable content)")
    _fsync_rename(tmp, dst)


def _fsync_rename(tmp: Path, dst: Path) -> None:
    """Spec §3 persistence boundary: temp -> fsync -> rename."""
    with open(tmp, "rb") as fh:
        try:
            import os

            os.fsync(fh.fileno())
        except Exception:
            pass
    tmp.rename(dst)


# ---------------------------------------------------------------------------
# Case creation
# ---------------------------------------------------------------------------

def create_case(
    media_path: Path,
    fields: dict,
    settings: Optional[Settings] = None,
    conn=None,
) -> dict:
    """Run the full intake gauntlet and insert the case row.

    Raises IntakeError with the surface-appropriate status code. Returns
    {case_id, status, estimated_cost_usd}."""
    from uapvf import db as dbmod

    settings = settings or get_settings()
    media_path = Path(media_path)
    if not media_path.exists():
        raise IntakeError(400, "media file is required")

    own_conn = conn is None
    if own_conn:
        dbmod.init_db(settings.var_dir)
        conn = dbmod.connect(settings.db_path)
    try:
        return _create_case_inner(media_path, fields, settings, conn)
    finally:
        if own_conn:
            conn.close()


def _create_case_inner(media_path: Path, fields: dict, settings: Settings, conn) -> dict:
    # Round-5 blocker fix: the unspec'd "Frozen Phase 2 controlling-source"
    # gate no longer wraps intake. Spec §7/J1 install a clean instance with
    # only UAPV_OPERATOR_TOKEN + SNEFERU_MOCK=1 and expect the first
    # submission to succeed; the strict-by-default reference gate 503'd
    # every case on that install. uapvf.references survives as dormant
    # phase-2 operator tooling (CLI `uapvf references status/fetch` +
    # informational report/metrics projections) and does NOT gate MVP
    # mutations.
    # --- MIME by magic bytes; executables rejected (415) -------------------
    kind, is_executable = sniff_kind(media_path)
    if is_executable:
        raise IntakeError(415, "unsupported or executable MIME type rejected")
    if kind is None:
        raise IntakeError(415, "unsupported MIME type (only JPEG/PNG/WebP/MP4/MOV/WebM)")

    size = media_path.stat().st_size
    is_video = kind in ALLOWED_VIDEO_EXTS
    cap = VIDEO_MAX_BYTES if is_video else IMAGE_MAX_BYTES
    if size > cap:
        raise IntakeError(413, f"media exceeds size cap ({cap} bytes)")

    # --- structured fields --------------------------------------------------
    parsed, field_errors = validate_fields(fields)
    if field_errors:
        raise IntakeError(400, "validation failed", field_errors)
    if not parsed.get("terms_accepted"):
        raise IntakeError(
            400, "terms acceptance required",
            {"terms_accepted": "terms acceptance required"},
        )

    # --- disk space ----------------------------------------------------------
    if disk_free_bytes(settings.var_dir) < MIN_FREE_DISK_BYTES:
        raise IntakeError(507, "insufficient disk space (< 500 MB free)")

    # --- rate limit (global, benchmark seeds exempt) ------------------------
    buyer_ref = fields.get("buyer_ref") or None
    if buyer_ref != BENCHMARK_BUYER_REF:
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        ) + "Z"
        recent = conn.execute(
            "SELECT COUNT(*) AS n FROM cases WHERE created_at > ?", (cutoff,)
        ).fetchone()["n"]
        if recent >= int(settings.UAPV_RATE_LIMIT_PER_HOUR):
            raise IntakeError(429, "rate limited: 20 case submissions per hour")

    # --- copy to an uncommitted case root, then decode/normalize in sandbox --
    case_id = str(uuid.uuid4())
    case_dir = settings.cases_dir / case_id
    (case_dir / "media").mkdir(parents=True, exist_ok=True)
    suffix = "." + (kind if kind != "jpeg" else "jpg")
    original_path = case_dir / "media" / ("original" + suffix)
    shutil.copyfile(media_path, original_path)
    normalized_path = case_dir / "media" / ("working" + (".jpg" if not is_video else ".mp4"))
    qcfg = load_quality_config(settings)
    try:
        processed = run_intake_sandboxed(
            case_id, str(original_path), str(case_dir),
            "video" if is_video else "image", qcfg,
        )
    except SandboxError as exc:
        shutil.rmtree(case_dir, ignore_errors=True)
        raise IntakeError(503, f"hostile-media sandbox unavailable: {exc}") from exc
    if not processed.get("ok"):
        shutil.rmtree(case_dir, ignore_errors=True)
        raise IntakeError(
            int(processed.get("status_code") or 422),
            str(processed.get("error") or "media processing failed"),
        )
    if not normalized_path.is_file():
        shutil.rmtree(case_dir, ignore_errors=True)
        raise IntakeError(422, "sandbox did not produce normalized media")
    probe = processed.get("probe") or {}
    quality = processed.get("quality") or {}

    media_sha = sha256_file(original_path)
    working_sha = sha256_file(normalized_path)

    # --- adversarial warnings (FR-024) ---------------------------------------
    warnings = compute_warnings(
        fields, parsed, original_path, "video" if is_video else "image",
        probe_result=probe,
    )

    # --- spend cap at intake --------------------------------------------------
    estimate = settings.estimated_case_cost_usd()
    status = "queued"
    if not spend.spend_allows_case(conn, settings, estimate, buyer_ref):
        status = "spend_capped"

    now = utcnow_iso()
    fields_json = json.dumps(
        {k: v for k, v in fields.items() if v is not None},
        sort_keys=True,
        ensure_ascii=False,
    )
    conn.execute(
        "INSERT INTO cases (case_id, created_at, updated_at, media_path, media_sha256,"
        " media_kind, observed_at, latitude, longitude, location_text, fields_json, buyer_ref,"
        " payment_status, status, quality_score, quality_gate_pass, run_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        (
            case_id, now, now, str(normalized_path), media_sha,
            "video" if is_video else "image",
            parsed["observed_at"], parsed["latitude"], parsed["longitude"],
            fields.get("location_text") or None, fields_json, buyer_ref,
            parsed["payment_status"], status, quality["quality_score"],
            1 if quality["quality_gate_pass"] else 0,
        ),
    )
    conn.commit()

    audit.append_event(
        conn, case_id, "operator", "case_created",
        {
            "media_sha256": media_sha,
            "working_sha256": working_sha,
            "kind": kind,
            "media_sandbox": processed.get("sandbox"),
            "quality_score": quality["quality_score"],
            "quality_gate_pass": bool(quality["quality_gate_pass"]),
            "estimated_cost_usd": estimate,
            "initial_status": status,
        },
    )
    if warnings:
        audit.append_event(conn, case_id, "system", "intake_warnings",
                           {"warnings": warnings})
    if status == "spend_capped":
        audit.append_event(conn, case_id, "system", "spend_cap_blocked",
                           {"estimated_cost_usd": estimate,
                            "cap_usd": float(settings.UAPV_SPEND_CAP_USD)})
    return {"case_id": case_id, "status": status, "estimated_cost_usd": estimate}
