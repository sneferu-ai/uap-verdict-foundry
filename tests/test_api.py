"""HTTP surface tests: auth, CSRF, intake, downloads, spend (AC-005,
AC-030, S1–S12, S8)."""
from __future__ import annotations

import hashlib
import io
import json
import pathlib
import re

import pytest

from conftest import SEEDS, base_fields

TOKEN = "test-operator-token"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(var_dir, monkeypatch):
    monkeypatch.setenv("UAPV_WORKER_THREADS", "0")
    from fastapi.testclient import TestClient
    from uapvf.server import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def web(client):
    """Logged-in web client; returns (client, csrf)."""
    r = client.post("/login", data={"token": TOKEN}, follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/cases/new")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return client, csrf


def media_bytes(name="nmm1.jpg"):
    return (SEEDS / name).read_bytes()


class TestHealth:
    def test_healthz(self, client):
        assert client.get("/healthz").json() == {"status": "ok"}

    def test_readyz_mock(self, client):
        r = client.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is True
        assert body["mode"] == "mock"
        assert body["sdk"] == "reachable"

    def test_metrics_are_low_cardinality_and_identifier_free(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "uapvf_build_info" in response.text
        assert "uapvf_reference_integrity" in response.text
        assert "case_id" not in response.text

    def test_terms_page(self, client):
        r = client.get("/terms")
        assert r.status_code == 200
        assert "non-evidence" in r.text.lower() or "evidence" in r.text.lower()


class TestMvpSurfaceBoundary:
    """spec.md:92 OUT + spec.md:84 deferred: no buyer-facing delivery
    surface in the MVP — the operator exports and delivers manually. The
    phase-2 token routes must not be mounted at all."""

    def test_buyer_delivery_page_not_mounted(self, client):
        assert client.get("/d/anything").status_code == 404

    def test_delivery_asset_route_not_mounted(self, client):
        r = client.get("/delivery/assets/t/x.png?exp=1&sig=s")
        assert r.status_code == 404

    def test_delivery_issue_and_revoke_routes_not_mounted(self, client):
        assert client.post("/cases/missing/delivery").status_code == 404
        assert client.post(
            "/cases/missing/delivery/revoke").status_code == 404
        assert client.post("/api/v1/cases/missing/delivery",
                           headers=BEARER).status_code == 404
        assert client.delete("/api/v1/cases/missing/delivery",
                             headers=BEARER).status_code == 404

    def test_story_asset_route_not_mounted(self, client):
        assert client.get("/cases/missing/story-assets/a1").status_code == 404

    def test_metrics_carry_no_delivery_gauge(self, client):
        assert "delivery" not in client.get("/metrics").text


class TestAuth:
    def test_unauthenticated_redirects_to_login(self, client):
        r = client.get("/cases", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"

    def test_bad_token_401(self, client):
        r = client.post("/login", data={"token": "nope"})
        assert r.status_code == 401

    def test_login_logout_cycle(self, client, web):
        c, csrf = web
        r = c.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
        assert r.status_code == 302
        r = c.get("/cases", follow_redirects=False)
        assert r.status_code == 302

    def test_bearer_required_for_api(self, client):
        assert client.get("/api/v1/cases").status_code == 401
        assert client.get("/api/v1/cases", headers={"Authorization": "Bearer wrong"}).status_code == 401

    def test_csrf_enforced(self, web):
        c, csrf = web
        r = c.post("/cases/new", data={"csrf_token": "wrong"},
                   files={"media": ("x.jpg", io.BytesIO(media_bytes()), "image/jpeg")})
        assert r.status_code == 403

    def test_malformed_session_cookies_are_rejected_without_error(self, client):
        from uapvf.auth import make_cookie, parse_cookie

        malformed = (".", "!!!!.AAAA", "Lg==.AA==", "AAAA.!!!!", "AAAA.AAAA")
        assert all(parse_cookie(value, TOKEN) is None for value in malformed)
        valid = make_cookie("a" * 64, TOKEN)
        assert parse_cookie(valid, TOKEN) == "a" * 64
        r = client.get(
            "/cases",
            headers={"Cookie": "uapv_session=Lg==.AA=="},
            follow_redirects=False,
        )
        assert r.status_code == 302


class TestWebIntake:
    def test_submit_and_detail_page(self, web):
        c, csrf = web
        fields = base_fields()
        r = c.post("/cases/new",
                   data={"csrf_token": csrf, "terms_accepted": "true",
                         "observed_at": fields["observed_at"],
                         "latitude": fields["latitude"],
                         "longitude": fields["longitude"],
                         "viewing_direction": fields["viewing_direction"]},
                   files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                    "image/jpeg")},
                   follow_redirects=False)
        assert r.status_code == 303
        case_id = r.headers["location"].rsplit("/", 1)[-1]
        detail = c.get(f"/cases/{case_id}")
        assert detail.status_code == 200
        assert case_id in detail.text

    def test_inline_validation_errors(self, web):
        c, csrf = web
        r = c.post("/cases/new",
                   data={"csrf_token": csrf, "terms_accepted": "true",
                         "observed_at": "bad", "latitude": "999",
                         "longitude": "-118"},
                   files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                    "image/jpeg")})
        assert r.status_code == 400
        assert "observed_at" in r.text and "latitude" in r.text

    def test_terms_required_inline(self, web):
        c, csrf = web
        r = c.post("/cases/new",
                   data={"csrf_token": csrf,
                         "observed_at": "2026-01-15T21:15:00+00:00",
                         "latitude": "34.5", "longitude": "-118.0"},
                   files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                    "image/jpeg")})
        assert r.status_code == 400
        assert "terms" in r.text.lower()

    def test_no_javascript_in_pages(self, web, client):
        c, _ = web
        for path in ("/cases", "/cases/new", "/login", "/terms"):
            r = c.get(path)
            assert "<script" not in r.text.lower(), path


class TestApiIntake:
    def test_api_submit_202(self, client):
        r = client.post("/api/v1/cases",
                        data={"terms_accepted": "true",
                              "observed_at": "2026-01-15T21:15:00+00:00",
                              "latitude": "34.5", "longitude": "-118.0"},
                        files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                         "image/jpeg")},
                        headers=BEARER)
        assert r.status_code == 202
        body = r.json()
        assert body["status"] in ("queued", "spend_capped")
        assert body["case_id"]

    def test_api_terms_400(self, client):
        r = client.post("/api/v1/cases",
                        data={"observed_at": "2026-01-15T21:15:00+00:00",
                              "latitude": "34.5", "longitude": "-118.0"},
                        files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                         "image/jpeg")},
                        headers=BEARER)
        assert r.status_code == 400
        assert "terms" in r.json()["detail"]

    def test_api_validation_failure_422(self, client):
        """S1: per-field inline errors are 400 for the form, 422 for the
        API surface."""
        r = client.post("/api/v1/cases",
                        data={"terms_accepted": "true",
                              "observed_at": "not-a-date",
                              "latitude": "999", "longitude": "-118.0"},
                        files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                         "image/jpeg")},
                        headers=BEARER)
        assert r.status_code == 422
        body = r.json()
        assert "observed_at" in body["field_errors"]
        assert "latitude" in body["field_errors"]

    def test_api_list_pagination_header(self, client):
        for _ in range(3):
            client.post("/api/v1/cases",
                        data={"terms_accepted": "true",
                              "observed_at": "2026-01-15T21:15:00+00:00",
                              "latitude": "34.5", "longitude": "-118.0"},
                        files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                         "image/jpeg")},
                        headers=BEARER)
        r = client.get("/api/v1/cases?limit=2&offset=0", headers=BEARER)
        assert r.status_code == 200
        assert r.headers["X-Total-Count"] == "3"
        assert len(r.json()) == 2

    def test_api_list_default_limit_50(self, client):
        # docs/API.md: default 50, max 200 for the API list too.
        # Use a benchmark seed buyer_ref so >20 submissions are exempt from the
        # hourly rate limit (FR-016) without mutating the production config.
        for _ in range(25):
            client.post("/api/v1/cases",
                        data={"terms_accepted": "true",
                              "buyer_ref": "__benchmark_seed__",
                              "observed_at": "2026-01-15T21:15:00+00:00",
                              "latitude": "34.5", "longitude": "-118.0"},
                        files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                         "image/jpeg")},
                        headers=BEARER)
        r = client.get("/api/v1/cases", headers=BEARER)
        assert r.status_code == 200
        assert r.headers["X-Total-Count"] == "25"
        assert len(r.json()) == 25

    def test_api_list_limit_clamped_to_200(self, client):
        for _ in range(3):
            client.post("/api/v1/cases",
                        data={"terms_accepted": "true",
                              "buyer_ref": "__benchmark_seed__",
                              "observed_at": "2026-01-15T21:15:00+00:00",
                              "latitude": "34.5", "longitude": "-118.0"},
                        files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                         "image/jpeg")},
                        headers=BEARER)
        r = client.get("/api/v1/cases?limit=300", headers=BEARER)
        assert r.status_code == 200
        assert r.headers["X-Total-Count"] == "3"
        assert len(r.json()) == 3


class TestDownloads:
    @pytest.fixture
    def completed(self, client, settings):
        r = client.post("/api/v1/cases",
                        data={"terms_accepted": "true",
                              "observed_at": "2026-01-15T21:15:00+00:00",
                              "latitude": "34.5", "longitude": "-118.0"},
                        files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                         "image/jpeg")},
                        headers=BEARER)
        case_id = r.json()["case_id"]
        from uapvf import db as dbmod, pipeline

        conn = dbmod.connect(settings.db_path)
        try:
            pipeline.drain_queue(settings, conn)
        finally:
            conn.close()
        return case_id

    def test_report_download_with_sha_header(self, client, completed):
        r = client.get(f"/api/v1/cases/{completed}/report.json", headers=BEARER)
        assert r.status_code == 200
        assert r.headers["x-uapvf-sha256"] == hashlib.sha256(r.content).hexdigest()
        body = r.json()
        assert body["case_id"] == completed

    def test_report_not_ready_409(self, client):
        r = client.post("/api/v1/cases",
                        data={"terms_accepted": "true",
                              "observed_at": "2026-01-15T21:15:00+00:00",
                              "latitude": "34.5", "longitude": "-118.0"},
                        files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                         "image/jpeg")},
                        headers=BEARER)
        case_id = r.json()["case_id"]
        r = client.get(f"/api/v1/cases/{case_id}/report.json", headers=BEARER)
        assert r.status_code == 409

    def test_report_downloadable_during_rerun_requested(self, web, completed,
                                                        settings):
        """FR-022: while a rerun is in flight the archived report copy
        remains downloadable on both surfaces."""
        c, _csrf = web
        from uapvf import db as dbmod, pipeline

        conn = dbmod.connect(settings.db_path)
        try:
            assert pipeline.rerun_case(conn, completed)["ok"]
            row = dbmod.get_case(conn, completed)
            assert row["status"] == "rerun_requested"
        finally:
            conn.close()
        # Web surface: no worker runs in this fixture, so the archive copy
        # does not exist yet — the previous run's report.html is served.
        r = c.get(f"/cases/{completed}/report")
        assert r.status_code == 200
        assert r.headers["content-disposition"].endswith('report.html"')
        # Once the worker-claim archive exists it takes precedence.
        case_dir = settings.cases_dir / completed
        archive = case_dir / f"report_{completed}_v1.html"
        archive.write_text("<html>archived v1</html>", encoding="utf-8")
        r = c.get(f"/cases/{completed}/report")
        assert r.status_code == 200
        assert r.text == "<html>archived v1</html>"
        assert archive.name in r.headers["content-disposition"]
        assert r.headers["x-uapvf-sha256"] == hashlib.sha256(r.content).hexdigest()
        # API surface: previous run's report.json remains downloadable.
        r = c.get(f"/api/v1/cases/{completed}/report.json", headers=BEARER)
        assert r.status_code == 200
        assert r.json()["case_id"] == completed

    def test_fiction_download_labels_intact(self, client, completed):
        r = client.get(f"/api/v1/cases/{completed}/fiction", headers=BEARER)
        assert r.status_code == 200
        assert r.headers["content-disposition"].endswith(".fic.md\"")
        assert r.text.count("[SPECULATIVE FICTION") >= 3

    def test_unknown_case_404(self, client):
        assert client.get("/api/v1/cases/nope/report.json",
                          headers=BEARER).status_code == 404

    def test_spend_endpoint(self, client, completed):
        r = client.get("/api/v1/spend", headers=BEARER)
        assert r.status_code == 200
        body = r.json()
        assert "month_total_usd" in body and "payment_summary" in body


class TestCaseActionsWeb:
    def test_delete_confirm_page_and_delete(self, web, settings):
        c, csrf = web
        r = c.post("/cases/new",
                   data={"csrf_token": csrf, "terms_accepted": "true",
                         "observed_at": "2026-01-15T21:15:00+00:00",
                         "latitude": "34.5", "longitude": "-118.0"},
                   files={"media": ("n.jpg", io.BytesIO(media_bytes()),
                                    "image/jpeg")},
                   follow_redirects=False)
        case_id = r.headers["location"].rsplit("/", 1)[-1]
        page = c.get(f"/cases/{case_id}/delete/confirm")
        assert page.status_code == 200
        assert "HARD delete" in page.text
        r = c.post(f"/cases/{case_id}/delete", data={"csrf_token": csrf},
                   follow_redirects=False)
        assert r.status_code == 303
        assert c.get(f"/cases/{case_id}").status_code == 404


class TestBenchmarkHttp:
    """S6: the benchmark must run off the event loop. The bearer path
    awaits a worker thread (event loop stays servable); the form path
    redirects immediately and the meta-refresh progress page is served
    while the background run proceeds."""

    def test_api_benchmark_run_threaded(self, client, settings):
        from uapvf import benchmark, db as dbmod

        conn = dbmod.connect(settings.db_path)
        try:
            benchmark.run_prereg(settings, conn)
        finally:
            conn.close()
        r = client.post("/benchmark/run", headers=BEARER)
        assert r.status_code == 200
        body = r.json()
        assert body["seeds"] == 10
        assert body["passed"] is True
        prog = json.loads(
            (settings.var_dir / "benchmark" / "progress.json").read_text())
        assert prog["done"] is True
        assert not prog.get("error")

    def test_form_benchmark_run_redirects_and_progress_page_serves(
            self, web, settings):
        import time

        from uapvf import benchmark, db as dbmod

        c, csrf = web
        conn = dbmod.connect(settings.db_path)
        try:
            benchmark.run_prereg(settings, conn)
        finally:
            conn.close()
        r = c.post("/benchmark/run", data={"csrf_token": csrf},
                   follow_redirects=False)
        # Immediate redirect — the run proceeds in a background thread.
        assert r.status_code == 303
        assert r.headers["location"] == "/benchmark"
        progress_file = settings.var_dir / "benchmark" / "progress.json"
        deadline = time.time() + 180
        while time.time() < deadline:
            # The progress page must be servable while the run is in
            # flight (the defect this regression pins: a handler-blocking
            # benchmark could never serve this page mid-run).
            page = c.get("/benchmark")
            assert page.status_code == 200
            if progress_file.exists():
                prog = json.loads(progress_file.read_text())
                if prog.get("done"):
                    break
            time.sleep(0.1)
        else:
            pytest.fail("background benchmark run did not finish in time")
        prog = json.loads(progress_file.read_text())
        assert prog["done"] is True
        assert not prog.get("error")
        # Wait for the run row to be finalized before the test exits so
        # the worker's env restoration cannot race later tests.
        deadline = time.time() + 30
        while time.time() < deadline:
            conn = dbmod.connect(settings.db_path)
            try:
                row = conn.execute(
                    "SELECT completed_at FROM benchmark_runs "
                    "ORDER BY started_at DESC LIMIT 1").fetchone()
            finally:
                conn.close()
            if row is not None and row["completed_at"]:
                break
            time.sleep(0.1)
        assert row is not None and row["completed_at"]


class TestS8ReadyzContract:
    """S8: `ready: true` requires exactly the DB write probe, SDK
    reachability in live mode (mock mode counts as reachable), and ffmpeg
    on PATH. Archive, ADS-B, and TLE reachability are REPORTED but never
    required (reopened blockers 80a8e3181d21 / 10af4c51241a)."""

    def test_mock_readyz_reports_all_s8_fields(self, client):
        body = client.get("/readyz").json()
        for key in ("mode", "db", "sdk", "archive", "adsb", "tle",
                    "ffmpeg", "pending_cases"):
            assert key in body, key

    def test_live_readyz_ready_without_tle_adsb_or_archive(
            self, client, monkeypatch):
        monkeypatch.setenv("SNEFERU_MOCK", "0")
        monkeypatch.setenv("UAPV_LINEAGE_RUNTIME", "live")
        import shutil as shutil_mod

        import uapvf.adapters.sneferu_adapter as sa

        monkeypatch.setattr(sa, "sdk_reachable", lambda settings: True)
        monkeypatch.setattr(shutil_mod, "which",
                            lambda name: f"/usr/bin/{name}")
        r = client.get("/readyz")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ready"] is True
        assert body["mode"] == "live"
        # Reported, never required:
        assert body["adsb"] == "unconfigured"
        assert body["tle"] == "unconfigured"
        assert body["archive"] == "unreachable"

    def test_live_readyz_503_when_sdk_unreachable(self, client, monkeypatch):
        monkeypatch.setenv("SNEFERU_MOCK", "0")
        import shutil as shutil_mod

        import uapvf.adapters.sneferu_adapter as sa

        monkeypatch.setattr(sa, "sdk_reachable", lambda settings: False)
        monkeypatch.setattr(shutil_mod, "which",
                            lambda name: f"/usr/bin/{name}")
        r = client.get("/readyz")
        assert r.status_code == 503
        assert r.json()["reason"] == "sdk unreachable"


class TestNoAutoDeletionMvp:
    """Spec §5 retention: case data is retained until the operator
    manually deletes; RETENTION_DAYS is only the S2 reminder badge; NO
    auto-deletion. The TTL deletion job is deferred to phase 2 (spec §1
    deferred table; spec.md:86 — reopened blockers fd9e4d63c322 /
    3eb26299e665)."""

    def test_retention_module_has_no_scheduler_or_ttl_job(self):
        import uapvf.retention as retention

        for name in ("RetentionScheduler", "run_retention",
                     "load_retention_config", "RetentionConfigError"):
            assert not hasattr(retention, name), name

    def test_clean_install_seeds_no_retention_config(self, tmp_path,
                                                     monkeypatch):
        from uapvf import db as dbmod
        from uapvf.config import get_settings

        monkeypatch.setenv("UAPV_VAR_DIR", str(tmp_path / "fresh"))
        settings = get_settings()
        dbmod.init_db(settings.var_dir)
        assert "retention_config.json" not in dbmod.DEFAULT_CONFIG_FILES
        assert not (settings.var_dir / "retention_config.json").exists()

    def test_app_factory_starts_no_schedulers(self):
        import inspect

        from uapvf import server as server_mod

        source = inspect.getsource(server_mod.create_app)
        assert "Scheduler" not in source
