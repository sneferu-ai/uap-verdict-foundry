"""Operator authentication: cookie sessions, CSRF, bearer tokens (spec §2).

Cookie format: base64(session_id) + "." + base64(hmac_sha256(session_id,
UAPV_OPERATOR_TOKEN)). Sessions and CSRF tokens are 256-bit values
(32 bytes from os.urandom, hex-encoded). Expired sessions are cleaned on
login attempts and at startup. No JavaScript participates in any of this.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from typing import Optional

from uapvf.config import utcnow_iso, parse_iso

COOKIE_NAME = "uapv_session"
SESSION_TTL_HOURS = 12


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64d(text: str) -> Optional[bytes]:
    try:
        return base64.b64decode(
            text.encode("ascii"), altchars=b"-_", validate=True
        )
    except Exception:
        return None


def cookie_mac(session_id: str, token: str) -> bytes:
    return hmac.new(
        token.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256
    ).digest()


def make_cookie(session_id: str, token: str) -> str:
    return _b64e(session_id.encode("utf-8")) + "." + _b64e(cookie_mac(session_id, token))


def parse_cookie(cookie_value: str, token: str) -> Optional[str]:
    """Return the session_id if the cookie MAC verifies, else None."""
    if not cookie_value or "." not in cookie_value or not token:
        return None
    sid_part, mac_part = cookie_value.split(".", 1)
    if not sid_part or not mac_part:
        return None
    sid_bytes = _b64d(sid_part)
    mac_bytes = _b64d(mac_part)
    if sid_bytes is None or mac_bytes is None or len(mac_bytes) != hashlib.sha256().digest_size:
        return None
    try:
        session_id = sid_bytes.decode("ascii")
    except UnicodeDecodeError:
        return None
    if len(session_id) != 64 or any(c not in "0123456789abcdef" for c in session_id):
        return None
    expected = cookie_mac(session_id, token)
    if not hmac.compare_digest(expected, mac_bytes):
        return None
    return session_id


def create_session(conn: sqlite3.Connection) -> dict:
    session_id = secrets.token_hex(32)  # 256-bit
    csrf_token = secrets.token_hex(32)  # 256-bit
    created = utcnow_iso()
    from datetime import timedelta, datetime, timezone

    expires = (
        datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    conn.execute(
        "INSERT INTO sessions (session_id, csrf_token, created_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (session_id, csrf_token, created, expires),
    )
    conn.commit()
    return {"session_id": session_id, "csrf_token": csrf_token, "expires_at": expires}


def get_session(conn: sqlite3.Connection, session_id: str) -> Optional[sqlite3.Row]:
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    try:
        expires = parse_iso(row["expires_at"])
    except Exception:
        return None
    from datetime import datetime, timezone

    if datetime.now(timezone.utc) >= expires:
        return None
    return row


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()


def cleanup_expired_sessions(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "DELETE FROM sessions WHERE expires_at <= ?", (utcnow_iso(),)
    )
    conn.commit()
    return cur.rowcount


def check_csrf(session_row: sqlite3.Row, provided: Optional[str]) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(session_row["csrf_token"], provided)


def check_bearer(authorization_header: Optional[str], token: str) -> bool:
    """Validate `Authorization: Bearer <UAPV_OPERATOR_TOKEN>`."""
    if not authorization_header or not token:
        return False
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].strip(), token)


def check_operator_token(provided: str, token: str) -> bool:
    if not provided or not token:
        return False
    return hmac.compare_digest(provided, token)


def resolve_session(conn: sqlite3.Connection, cookie_header: Optional[str], token: str):
    """Find and verify the session from a Cookie header. Returns
    (session_row, session_id) or (None, None)."""
    if not cookie_header:
        return None, None
    value = None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(COOKIE_NAME + "="):
            value = part[len(COOKIE_NAME) + 1 :]
            break
    if not value:
        return None, None
    # Starlette/httpx quote the cookie because the signed value contains
    # padding characters.  RFC cookie values may be quoted, so unwrap one
    # balanced pair before applying the strict base64/MAC validation.
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    session_id = parse_cookie(value, token)
    if not session_id:
        return None, None
    row = get_session(conn, session_id)
    if row is None:
        return None, None
    return row, session_id
