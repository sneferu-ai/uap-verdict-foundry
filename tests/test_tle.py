from __future__ import annotations

import pytest

import json

from uapvf import tle
from uapvf.egress import EgressDenied, validate_url


TLE_A = """ISS (ZARYA)
1 25544U 98067A   26233.50000000  .00010000  00000-0  18000-3 0  9999
2 25544  51.6400 120.0000 0005000  40.0000  10.0000 15.50000000123456
"""

OMM_A = [{
    "OBJECT_NAME": "ISS (ZARYA)", "NORAD_CAT_ID": 25544,
    "EPOCH": "2026-08-21T00:00:00.000000", "MEAN_MOTION": 15.5,
    "ECCENTRICITY": 0.0005, "INCLINATION": 51.64,
    "RA_OF_ASC_NODE": 120.0, "ARG_OF_PERICENTER": 40.0,
    "MEAN_ANOMALY": 10.0, "EPHEMERIS_TYPE": 0,
    "CLASSIFICATION_TYPE": "U", "OBJECT_ID": "1998-067A",
    "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 1, "BSTAR": 0.0001,
    "MEAN_MOTION_DOT": 0.0, "MEAN_MOTION_DDOT": 0.0,
}]


@pytest.mark.skip(reason="phase 2 / removed: TLE refresh was simplified to the manual MVP path (`uapvf tle refresh`); substantive-hash/OMM/egress-scoping behaviour is deferred")
def test_substantive_hash_excludes_epoch_and_mean_anomaly():
    changed_only_excluded = TLE_A.replace("26233.50000000", "26234.90000000").replace(
        " 10.0000 15.500", " 99.0000 15.500")
    assert tle.substantive_hash(TLE_A) == tle.substantive_hash(changed_only_excluded)
    changed_inclination = TLE_A.replace(" 51.6400 ", " 52.6400 ")
    assert tle.substantive_hash(TLE_A) != tle.substantive_hash(changed_inclination)


@pytest.mark.skip(reason="phase 2 / removed: TLE refresh was simplified to the manual MVP path (`uapvf tle refresh`); substantive-hash/OMM/egress-scoping behaviour is deferred")
def test_omm_json_hash_and_format_support_six_digit_catalog_ids():
    original = list(OMM_A)
    original[0] = dict(original[0], NORAD_CAT_ID=100400)
    epoch_only = [dict(original[0], EPOCH="2026-08-22T00:00:00.000000",
                       MEAN_ANOMALY=99.0)]
    changed = [dict(original[0], INCLINATION=52.64)]
    original_text = json.dumps(original)
    assert tle.catalog_format(original_text) == "omm_json"
    assert tle.substantive_hash(original_text) == tle.substantive_hash(
        json.dumps(epoch_only)
    )
    assert tle.substantive_hash(original_text) != tle.substantive_hash(
        json.dumps(changed)
    )
    assert tle.substantive_records(original_text)[0]["satellite_number"] == "100400"


def test_egress_requires_explicit_host(settings):
    settings.UAPV_EGRESS_ALLOWLIST = "example.com"
    assert validate_url("https://example.com/catalog.tle", settings)
    try:
        validate_url("https://not-allowed.example/catalog.tle", settings)
    except EgressDenied as exc:
        assert "not allowlisted" in str(exc)
    else:
        raise AssertionError("unlisted host was accepted")


@pytest.mark.skip(reason="phase 2 / removed: TLE refresh was simplified to the manual MVP path (`uapvf tle refresh`); substantive-hash/OMM/egress-scoping behaviour is deferred")
def test_refresh_persists_only_substantive_change(settings, conn, monkeypatch):
    settings.TLE_CATALOG_URL = "https://tle.example/catalog"
    settings.UAPV_EGRESS_ALLOWLIST = "tle.example"

    class Response:
        text = TLE_A
        def raise_for_status(self):
            return None

    monkeypatch.setattr(tle.egress, "get", lambda *a, **k: Response())
    first = tle.refresh(conn, settings)
    assert first["status"] == "updated" and first["substantive_change"] is True
    second = tle.refresh(conn, settings)
    assert second["status"] == "unchanged" and second["substantive_change"] is False


@pytest.mark.skip(reason="phase 2 / removed: TLE refresh was simplified to the manual MVP path (`uapvf tle refresh`); substantive-hash/OMM/egress-scoping behaviour is deferred")
def test_failed_refresh_is_honest_and_persisted(settings, conn, monkeypatch):
    settings.TLE_CATALOG_URL = "https://tle.example/catalog"
    settings.UAPV_EGRESS_ALLOWLIST = "tle.example"
    monkeypatch.setattr(tle.egress, "get", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("offline")))
    result = tle.refresh(conn, settings)
    assert result == {"status": "failed", "reason": "RuntimeError"}
    row = conn.execute("SELECT status FROM tle_catalogs").fetchone()
    assert row["status"] == "failed"


@pytest.mark.skip(reason="phase 2 / removed: TLE refresh was simplified to the manual MVP path (`uapvf tle refresh`); substantive-hash/OMM/egress-scoping behaviour is deferred")
def test_latest_catalog_is_scoped_to_configured_source(settings, conn, monkeypatch):
    settings.UAPV_EGRESS_ALLOWLIST = "tle.example"

    class Response:
        text = TLE_A
        def raise_for_status(self):
            return None

    monkeypatch.setattr(tle.egress, "get", lambda *a, **k: Response())
    settings.TLE_CATALOG_URL = "https://tle.example/a"
    assert tle.refresh(conn, settings)["status"] == "updated"
    settings.TLE_CATALOG_URL = "https://tle.example/b"
    assert tle.latest_catalog(conn, settings) is None
