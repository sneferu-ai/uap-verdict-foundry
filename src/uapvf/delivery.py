"""Case-scoped buyer delivery packages, capability tokens, and receipts."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from uapvf.config import Settings, parse_iso, utcnow_iso


BRAND_MARK_SVG = """<svg viewBox="0 0 256 256" role="img" aria-label="Verdict Foundry mark"><rect width="256" height="256" rx="48" fill="#1C1E25"/><path d="M-12 70H62c30 0 36 18 55 42l19 24" fill="none" stroke="#78DCE8" stroke-width="24" stroke-linecap="round"/><path d="M68-12v50c0 29 19 41 42 60l28 23M145 132c22 1 31 15 46 35l77 101" fill="none" stroke="#ABDFB9" stroke-width="24" stroke-linecap="round"/><circle cx="140" cy="128" r="43" fill="#1C1E25" stroke="#E9EDE6" stroke-width="8"/><path d="M105 111h70l-24 42h-22z" fill="#E9EDE6"/><circle cx="140" cy="128" r="10" fill="#1C1E25"/></svg>"""


class DeliveryError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _hmac_key(settings: Settings) -> bytes:
    configured = settings.UAPV_DELIVERY_HMAC_KEY
    if configured:
        return hashlib.sha256(configured.encode("utf-8")).digest()
    if not settings.UAPV_OPERATOR_TOKEN:
        raise DeliveryError(503, "delivery_key_missing",
                            "delivery signing key is not configured")
    return hashlib.sha256(
        b"uapvf-delivery-v1\0" + settings.UAPV_OPERATOR_TOKEN.encode("utf-8")
    ).digest()


def _signing_paths(settings: Settings) -> tuple[Path, Path]:
    root = settings.var_dir / "keys"
    return root / "alrc-ed25519.pem", root / "alrc-ed25519.pub"


def ensure_signing_key(settings: Settings):
    """Load/create an encrypted Ed25519 private key; never write plaintext."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    passphrase = settings.UAPV_SIGNING_PASSPHRASE
    if not passphrase:
        raise DeliveryError(503, "signing_passphrase_missing",
                            "UAPV_SIGNING_PASSPHRASE is required for delivery")
    private_path, public_path = _signing_paths(settings)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    if private_path.exists():
        key = serialization.load_pem_private_key(
            private_path.read_bytes(), password=passphrase.encode("utf-8"))
    else:
        key = Ed25519PrivateKey.generate()
        encrypted = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
        )
        temp = private_path.with_suffix(".tmp")
        temp.write_bytes(encrypted); os.chmod(temp, 0o600); temp.replace(private_path)
        public_path.write_bytes(key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        os.chmod(public_path, 0o644)
    return key


def sign_receipt(manifest: dict, settings: Settings) -> dict:
    key = ensure_signing_key(settings)
    signature = key.sign(_canonical(manifest))
    public_raw = key.public_key().public_bytes_raw()
    return {
        "algorithm": "Ed25519",
        "manifest": manifest,
        "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        "public_key": base64.urlsafe_b64encode(public_raw).decode("ascii").rstrip("="),
    }


def verify_receipt(receipt: dict) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    def decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    try:
        Ed25519PublicKey.from_public_bytes(decode(receipt["public_key"])).verify(
            decode(receipt["signature"]), _canonical(receipt["manifest"]))
        return True
    except Exception:
        return False


def build_package(conn, settings: Settings, case_id: str) -> dict:
    row = conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if row is None:
        raise DeliveryError(404, "case_not_found", "case not found")
    if row["status"] != "complete" or not row["report_path"]:
        raise DeliveryError(409, "case_not_complete", "case report is not complete")
    case_dir = settings.cases_dir / case_id
    package_version = int(row["run_version"])
    package_dir = case_dir / "delivery" / f"package-v{package_version}"
    receipt_path = package_dir / "ALRC.json"
    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            manifest = receipt["manifest"]
            if (verify_receipt(receipt)
                    and manifest.get("case_id") == case_id
                    and int(manifest.get("run_version")) == package_version):
                valid_files = True
                for item in manifest.get("files") or []:
                    target = (package_dir / item["path"]).resolve()
                    target.relative_to(package_dir.resolve())
                    if (not target.is_file()
                            or hashlib.sha256(target.read_bytes()).hexdigest()
                            != item["sha256"]):
                        valid_files = False
                        break
                if valid_files:
                    return {
                        "path": str(package_dir), "version": package_version,
                        "sha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
                        "manifest": manifest, "receipt_verified": True,
                    }
        except Exception:
            pass
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    included = []
    for name in ("report.html", "report.json"):
        source = case_dir / name
        if source.exists():
            target = package_dir / name
            shutil.copyfile(source, target)
            included.append(target)
    evidence_dir = package_dir / "evidence"
    for source in sorted((case_dir / "media").glob("working.*")):
        if not source.is_file():
            continue
        evidence_dir.mkdir(exist_ok=True)
        target = evidence_dir / f"normalized-capture{source.suffix.lower()}"
        shutil.copyfile(source, target)
        included.append(target)
    assets = conn.execute(
        "SELECT * FROM story_assets WHERE case_id=? AND run_version=? ORDER BY created_at",
        (case_id, row["run_version"])).fetchall()
    asset_dir = package_dir / "assets"
    for asset in assets:
        source = Path(asset["path"])
        if source.exists():
            asset_dir.mkdir(exist_ok=True)
            target = asset_dir / source.name
            shutil.copyfile(source, target)
            included.append(target)
            alt_source = source.with_suffix(".alt.txt")
            if alt_source.exists():
                alt_target = asset_dir / alt_source.name
                shutil.copyfile(alt_source, alt_target); included.append(alt_target)
    file_manifest = [{
        "path": str(path.relative_to(package_dir)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    } for path in sorted(included)]
    manifest = {
        "schema_version": 1, "case_id": case_id,
        "run_version": int(row["run_version"]), "verdict": row["verdict"],
        "created_at": utcnow_iso(), "files": file_manifest,
        "evidence_fiction_boundary": "separate_paths",
    }
    receipt = sign_receipt(manifest, settings)
    (package_dir / "ALRC.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2), encoding="utf-8")
    package_hash = hashlib.sha256(_canonical(manifest)).hexdigest()
    return {"path": str(package_dir), "version": package_version,
            "sha256": package_hash, "manifest": manifest,
            "receipt_verified": verify_receipt(receipt)}


def issue_token(conn, settings: Settings, case_id: str,
                ttl_hours: int | None = None) -> dict:
    from uapvf.references import ReferenceGateError, require_for_mutation
    try:
        require_for_mutation(settings)
    except ReferenceGateError as exc:
        raise DeliveryError(503, "reference_gate_blocked", str(exc)) from exc
    package = build_package(conn, settings, case_id)
    token = secrets.token_urlsafe(32)  # 32 random bytes, base64url encoded
    token_id = str(uuid.uuid4())
    created = datetime.now(timezone.utc)
    ttl = int(ttl_hours or settings.UAPV_DELIVERY_TTL_HOURS)
    expires = created + timedelta(hours=ttl)
    conn.execute(
        "INSERT INTO delivery_tokens "
        "(token_id, case_id, token_hash, created_at, expires_at, max_redemptions,"
        " package_sha256, package_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (token_id, case_id, _token_hash(token), utcnow_iso(),
         expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
         int(settings.UAPV_DELIVERY_MAX_REDEMPTIONS), package["sha256"],
         package["version"]),
    )
    conn.execute("UPDATE cases SET delivery_version=?, updated_at=? WHERE case_id=?",
                 (package["version"], utcnow_iso(), case_id))
    conn.commit()
    secret_file = settings.cases_dir / case_id / "delivery" / f"token-{token_id}.txt"
    secret_file.write_text(token + "\n", encoding="ascii"); os.chmod(secret_file, 0o600)
    return {"token": token, "token_id": token_id, "case_id": case_id,
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "max_redemptions": int(settings.UAPV_DELIVERY_MAX_REDEMPTIONS),
            "package": package}


def _event(conn, row, action: str, detail=None) -> None:
    conn.execute(
        "INSERT INTO delivery_events (token_id, case_id, action, detail_json, recorded_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (row["token_id"], row["case_id"], action,
         json.dumps(detail or {}, sort_keys=True), utcnow_iso()))
    conn.commit()


def redeem(conn, settings: Settings, token: str | None, count: bool = True):
    # Required precedence: auth -> rate -> revoked -> expired/exhausted.
    if not token:
        raise DeliveryError(401, "delivery_auth_required", "delivery token required")
    row = conn.execute("SELECT * FROM delivery_tokens WHERE token_hash=?",
                       (_token_hash(token),)).fetchone()
    if row is None:
        raise DeliveryError(401, "delivery_auth_invalid", "delivery token invalid")
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")
    recent = conn.execute(
        "SELECT COUNT(*) AS n FROM delivery_events WHERE token_id=? AND "
        "action='redeem_attempt' AND recorded_at>?", (row["token_id"], cutoff)
    ).fetchone()["n"]
    _event(conn, row, "redeem_attempt", {})
    if recent >= int(settings.UAPV_DELIVERY_RATE_PER_MINUTE):
        raise DeliveryError(429, "delivery_rate_limited", "too many delivery requests")
    if row["revoked_at"]:
        raise DeliveryError(403, "delivery_revoked", "delivery has been revoked")
    if parse_iso(row["expires_at"]) <= datetime.now(timezone.utc):
        raise DeliveryError(410, "delivery_expired", "delivery has expired")
    if int(row["redemption_count"]) >= int(row["max_redemptions"]):
        raise DeliveryError(410, "delivery_exhausted", "delivery redemption limit reached")
    case = conn.execute("SELECT * FROM cases WHERE case_id=?", (row["case_id"],)).fetchone()
    stale = int(row["package_version"]) != int(case["run_version"])
    if count:
        conn.execute(
            "UPDATE delivery_tokens SET redemption_count=redemption_count+1, "
            "grace_started_at=COALESCE(grace_started_at, ?) WHERE token_id=?",
            (utcnow_iso(), row["token_id"]))
        conn.commit(); _event(conn, row, "redeemed", {"stale": stale})
    return row, case, stale


def revoke_case_tokens(conn, case_id: str) -> int:
    cur = conn.execute(
        "UPDATE delivery_tokens SET revoked_at=? WHERE case_id=? AND revoked_at IS NULL",
        (utcnow_iso(), case_id)); conn.commit()
    return cur.rowcount


def signed_asset_url(settings: Settings, token_id: str, relative_path: str,
                     ttl_seconds: int = 600) -> str:
    expires = int(datetime.now(timezone.utc).timestamp()) + ttl_seconds
    clean = str(Path(relative_path))
    payload = f"{token_id}\n{clean}\n{expires}".encode("utf-8")
    signature = hmac.new(_hmac_key(settings), payload, hashlib.sha256).hexdigest()
    return f"/delivery/assets/{token_id}/{quote(clean)}?exp={expires}&sig={signature}"


def verify_asset_signature(settings: Settings, token_id: str, relative_path: str,
                           expires: int, signature: str) -> bool:
    if expires < int(datetime.now(timezone.utc).timestamp()):
        return False
    payload = f"{token_id}\n{str(Path(relative_path))}\n{expires}".encode("utf-8")
    expected = hmac.new(_hmac_key(settings), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def buyer_page(conn, settings: Settings, row, case, stale: bool) -> str:
    package_dir = settings.cases_dir / row["case_id"] / "delivery" / f"package-v{row['package_version']}"
    report_url = signed_asset_url(settings, row["token_id"], "report.html")
    report_json_url = signed_asset_url(settings, row["token_id"], "report.json")
    receipt_url = signed_asset_url(settings, row["token_id"], "ALRC.json")
    evidence = []
    evidence_dir = package_dir / "evidence"
    for path in sorted(evidence_dir.glob("*")) if evidence_dir.exists() else []:
        rel = str(path.relative_to(package_dir))
        url = html.escape(signed_asset_url(settings, row["token_id"], rel))
        if path.suffix.lower() == ".mp4":
            element = f'<video controls preload="metadata" src="{url}"></video>'
        else:
            element = f'<img src="{url}" alt="Normalized case evidence capture">'
        evidence.append(
            f'<figure class="evidence">{element}<figcaption>Normalized evidence media · '
            'hash recorded in the signed package receipt</figcaption></figure>')
    assets = []
    for path in sorted((package_dir / "assets").glob("*.png")) if (package_dir / "assets").exists() else []:
        rel = str(path.relative_to(package_dir))
        assets.append(f'<figure><img src="{html.escape(signed_asset_url(settings,row["token_id"],rel))}" alt="Speculative story illustration; not forensic evidence"><figcaption>SPECULATIVE FICTION — NOT FORENSIC EVIDENCE</figcaption></figure>')
    for path in sorted((package_dir / "assets").glob("*.mp4")) if (package_dir / "assets").exists() else []:
        rel = str(path.relative_to(package_dir))
        assets.append(f'<figure><video controls preload="metadata" src="{html.escape(signed_asset_url(settings,row["token_id"],rel))}"></video><figcaption>SPECULATIVE FICTION — NOT FORENSIC EVIDENCE · permanent label appears in every scene</figcaption></figure>')
    banner = '<div class="updated">VERDICT UPDATED — ask the operator for a new delivery link.</div>' if stale else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Verdict Foundry · Buyer delivery</title><style>body{{margin:0;background:#0c111d;color:#edf0e8;font:16px system-ui;line-height:1.6}}main{{max-width:1080px;margin:auto;padding:32px}}header{{border-bottom:1px solid #334154;padding:24px 0}}header svg{{width:64px;height:64px;float:left;margin:0 18px 12px 0}}.eyebrow{{color:#76dfc1;letter-spacing:.16em;text-transform:uppercase}}.updated{{background:#7c2637;padding:16px;border:2px solid #ff8da5;font-weight:800}}.links{{display:flex;gap:12px;flex-wrap:wrap;clear:both;margin:20px 0}}.links a{{border:1px solid #76dfc1;border-radius:8px;padding:8px 12px;text-decoration:none}}.report{{background:#151c29;border:1px solid #334154;border-radius:18px;overflow:hidden;margin-top:24px;clear:both}}iframe{{display:block;width:100%;height:1100px;border:0;background:#fff}}img,video{{display:block;width:100%;max-width:100%;border-radius:14px}}figure{{margin:28px 0}}figcaption{{background:#641d32;padding:10px;font-weight:800}}.evidence figcaption{{background:#203d38;color:#dff8ed}}a{{color:#76dfc1}}h2{{margin-top:42px}}@media(max-width:700px){{main{{padding:16px}}iframe{{height:900px}}}}</style></head><body><main><header>{BRAND_MARK_SVG}<div class="eyebrow">Verdict Foundry · verified delivery</div><h1>Case {html.escape(row['case_id'][:8])}</h1><p>No operator account or filesystem access is exposed by this page. Package receipt: Ed25519 verified.</p></header>{banner}<nav class="links" aria-label="Package downloads"><a href="{html.escape(report_json_url)}">Download report JSON</a><a href="{html.escape(receipt_url)}">Download signed receipt</a></nav><section class="report"><iframe title="Forensic case report" src="{html.escape(report_url)}"></iframe></section><section><h2>Evidence media</h2>{''.join(evidence)}</section><section><h2>Speculative story material</h2>{''.join(assets)}</section></main></body></html>"""
