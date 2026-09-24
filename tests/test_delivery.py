from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from conftest import base_fields
from uapvf import delivery

# Stale against the shipped MVP scope: buyer-facing delivery surface deferred to phase 2 (README).
pytestmark = pytest.mark.skip(reason="phase 2 / removed: buyer-facing delivery surface deferred to phase 2 (README)")



@pytest.fixture
def deliverable(complete_case, settings, conn, monkeypatch):
    monkeypatch.setenv(
        "UAPV_ARCHIVE_MIRROR_PATH",
        str(settings.var_dir / "benchmark" / "seed" / "catalog_fixture.sqlite"))
    settings.UAPV_SIGNING_PASSPHRASE = "test-only-strong-passphrase"
    case_id, result = complete_case("nmm1.jpg", base_fields(weather="clear"))
    assert result["status"] == "complete"
    issued = delivery.issue_token(conn, settings, case_id)
    return case_id, issued


def test_issue_uses_32_random_bytes_and_0600_file(deliverable, settings):
    case_id, issued = deliverable
    raw = base64.urlsafe_b64decode(
        issued["token"] + "=" * (-len(issued["token"]) % 4))
    assert len(raw) == 32
    token_file = (settings.cases_dir / case_id / "delivery" /
                  f"token-{issued['token_id']}.txt")
    assert token_file.exists()
    assert oct(token_file.stat().st_mode & 0o777) == "0o600"
    receipt = json.loads(
        (settings.cases_dir / case_id / "delivery" / "package-v1" /
         "ALRC.json").read_text())
    assert delivery.verify_receipt(receipt) is True
    package = settings.cases_dir / case_id / "delivery" / "package-v1"
    evidence = list((package / "evidence").glob("normalized-capture.*"))
    assert len(evidence) == 1
    assert any(item["path"].startswith("evidence/")
               for item in receipt["manifest"]["files"])
    private = settings.var_dir / "keys" / "alrc-ed25519.pem"
    assert b"ENCRYPTED PRIVATE KEY" in private.read_bytes()
    assert oct(private.stat().st_mode & 0o777) == "0o600"


def test_raw_token_never_enters_database(deliverable, conn):
    _case_id, issued = deliverable
    token = issued["token"]
    dump = "\n".join(
        str(tuple(row)) for table in ("delivery_tokens", "delivery_events", "audit_events")
        for row in conn.execute(f"SELECT * FROM {table}").fetchall())
    assert token not in dump


def test_reissuing_token_reuses_immutable_run_package(deliverable, settings, conn):
    case_id, issued = deliverable
    receipt_path = (settings.cases_dir / case_id / "delivery" / "package-v1" /
                    "ALRC.json")
    before = receipt_path.read_bytes()
    second = delivery.issue_token(conn, settings, case_id)
    assert second["token"] != issued["token"]
    assert second["package"]["sha256"] == issued["package"]["sha256"]
    assert receipt_path.read_bytes() == before


def test_redeem_exhaustion_and_invalid_auth(deliverable, settings, conn):
    _case_id, issued = deliverable
    conn.execute("UPDATE delivery_tokens SET max_redemptions=1 WHERE token_id=?",
                 (issued["token_id"],)); conn.commit()
    row, case, stale = delivery.redeem(conn, settings, issued["token"])
    assert stale is False and row["case_id"] == case["case_id"]
    with pytest.raises(delivery.DeliveryError) as exhausted:
        delivery.redeem(conn, settings, issued["token"])
    assert (exhausted.value.status_code, exhausted.value.code) == (
        410, "delivery_exhausted")
    with pytest.raises(delivery.DeliveryError) as invalid:
        delivery.redeem(conn, settings, "not-a-real-capability")
    assert invalid.value.status_code == 401


def test_precedence_rate_before_revocation(deliverable, settings, conn):
    case_id, issued = deliverable
    delivery.revoke_case_tokens(conn, case_id)
    settings.UAPV_DELIVERY_RATE_PER_MINUTE = 0
    with pytest.raises(delivery.DeliveryError) as error:
        delivery.redeem(conn, settings, issued["token"])
    assert error.value.status_code == 429


def test_expired_and_revoked_statuses(deliverable, settings, conn):
    case_id, issued = deliverable
    delivery.revoke_case_tokens(conn, case_id)
    with pytest.raises(delivery.DeliveryError) as revoked:
        delivery.redeem(conn, settings, issued["token"])
    assert revoked.value.status_code == 403
    conn.execute(
        "UPDATE delivery_tokens SET revoked_at=NULL, expires_at=? WHERE token_id=?",
        ((datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"), issued["token_id"])); conn.commit()
    with pytest.raises(delivery.DeliveryError) as expired:
        delivery.redeem(conn, settings, issued["token"])
    assert expired.value.status_code == 410


def test_signed_asset_path_rejects_tampering(deliverable, settings):
    _case_id, issued = deliverable
    url = delivery.signed_asset_url(
        settings, issued["token_id"], "assets/example.png")
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(url); query = parse_qs(parsed.query)
    assert delivery.verify_asset_signature(
        settings, issued["token_id"], "assets/example.png",
        int(query["exp"][0]), query["sig"][0])
    assert not delivery.verify_asset_signature(
        settings, issued["token_id"], "../report.json",
        int(query["exp"][0]), query["sig"][0])
