from __future__ import annotations

import json
import shutil
import sqlite3
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from conftest import base_fields
from uapvf import retention


def _make_pre_purge_backup(conn, settings, case_id):
    backup_dir = settings.var_dir / "backup"; backup_dir.mkdir(exist_ok=True)
    staging = settings.var_dir / "backup-staging"; staging.mkdir()
    dst = sqlite3.connect(str(staging / "uapvf.db"))
    conn.backup(dst); dst.close()
    shutil.copytree(settings.cases_dir, staging / "cases")
    archive = backup_dir / "pre-purge.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging / "uapvf.db", arcname="uapvf.db")
        tar.add(staging / "cases", arcname="cases")
    shutil.rmtree(staging)
    return archive


@pytest.mark.skip(reason="phase 2 / removed: automatic retention policy and independent restore-verification of scrubbed backups were cut in the MVP scope-down (manual purge + legal holds remain)")
def test_policy_requires_exact_eleven_keys(settings, conn):
    config = retention.load_config(settings)
    assert set(config["ttl_days"]) == set(retention.TTL_KEYS)
    config["ttl_days"].pop("story_assets")
    (settings.var_dir / "retention_config.json").write_text(json.dumps(config))
    with pytest.raises(retention.RetentionConfigError, match="missing"):
        retention.load_config(settings)


def test_legal_hold_blocks_material_purge(complete_case, settings, conn):
    case_id, _ = complete_case("nmm1.jpg")
    retention.set_legal_hold(conn, case_id, "investigation")
    result = retention.purge_case(conn, settings, case_id)
    assert result["status"] == "held"
    assert (settings.cases_dir / case_id).exists()
    retention.release_legal_hold(conn, case_id)


@pytest.mark.skip(reason="phase 2 / removed: automatic retention policy and independent restore-verification of scrubbed backups were cut in the MVP scope-down (manual purge + legal holds remain)")
def test_purge_scrubs_and_restore_verifies_preexisting_backup(
        complete_case, settings, conn):
    case_id, _ = complete_case("nmm1.jpg", base_fields(weather="clear"))
    archive = _make_pre_purge_backup(conn, settings, case_id)
    result = retention.purge_case(conn, settings, case_id)
    assert result["purged"] is True
    assert result["restore_verification"][0]["restore_attempted"] is True
    assert result["restore_verification"][0]["passed"] is True
    assert conn.execute("SELECT 1 FROM cases WHERE case_id=?", (case_id,)).fetchone() is None
    # Independent second restore attempt against the rewritten backup.
    assert retention.verify_backup_absence(archive, case_id)["passed"] is True


@pytest.mark.skip(reason="phase 2 / removed: automatic retention policy and independent restore-verification of scrubbed backups were cut in the MVP scope-down (manual purge + legal holds remain)")
def test_run_retention_revokes_expired_delivery(
        complete_case, settings, conn, monkeypatch):
    from uapvf import delivery
    monkeypatch.setenv("UAPV_ARCHIVE_MIRROR_PATH",
                       str(settings.var_dir / "benchmark" / "seed" /
                           "catalog_fixture.sqlite"))
    settings.UAPV_SIGNING_PASSPHRASE = "test-only-passphrase"
    case_id, _ = complete_case("nmm1.jpg", base_fields(weather="clear"))
    issued = delivery.issue_token(conn, settings, case_id)
    config = retention.load_config(settings)
    config["ttl_days"]["delivery_tokens"] = 0
    (settings.var_dir / "retention_config.json").write_text(json.dumps(config))
    result = retention.run_retention(conn, settings,
                                     datetime.now(timezone.utc) + timedelta(seconds=1))
    assert any(item["action"] == "delivery_tokens_revoked" for item in result["actions"])
    row = conn.execute("SELECT revoked_at FROM delivery_tokens WHERE token_id=?",
                       (issued["token_id"],)).fetchone()
    assert row["revoked_at"]


@pytest.mark.skip(reason="phase 2 / removed: automatic retention policy and independent restore-verification of scrubbed backups were cut in the MVP scope-down (manual purge + legal holds remain)")
def test_retention_sweeps_frames_previews_temp_fiction_and_rerun_archives(
        complete_case, settings, conn):
    case_id, _ = complete_case("nmm1.jpg", base_fields(weather="clear"))
    case_dir = settings.cases_dir / case_id
    governed = [
        case_dir / "frames" / "frame-001.jpg",
        case_dir / "thumbnails" / "thumb.jpg",
        case_dir / "previews" / "preview.jpg",
        case_dir / "tmp" / "decoder.bin",
        case_dir / "report_case_v1.html",
        case_dir / "report_case_v1.json",
    ]
    for path in governed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"sensitive-derived-data")
    config = retention.load_config(settings)
    for key in ("normalized_media", "forensic_reports", "report_sidecars",
                "xenoscience_outputs"):
        config["ttl_days"][key] = 0
    (settings.var_dir / "retention_config.json").write_text(json.dumps(config))
    result = retention.run_retention(
        conn, settings, datetime.now(timezone.utc) + timedelta(seconds=1))
    assert result["ok"] is True
    assert all(not path.exists() for path in governed)
    assert not list(case_dir.glob("fiction_*.fic.md"))
