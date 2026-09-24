"""Content-negotiation + SPA serving plane tests (UI spec §3.3–§3.9,
BE-03/04/05/06/13/17/19/20, D-20/D-32/D-33).

These cover the exact JSON branches the React console (ui/) depends on:
the Accept matrix, the Vary: Accept middleware, the typed error codes,
the login JSON contract, the SPA shell + uapv-bootstrap injection, and
the report/fiction negotiation. DESIGN.md §10 audit cites this file.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from conftest import base_fields

from uapvf import web_json as wj

TOKEN = "test-operator-token"

SPA_BUILT = (pathlib.Path.cwd() / "ui" / "dist" / "index.html").exists()
needs_spa = pytest.mark.skipif(not SPA_BUILT, reason="ui/dist is not built")


@pytest.fixture
def client(var_dir, monkeypatch):
    monkeypatch.setenv("UAPV_WORKER_THREADS", "0")
    from fastapi.testclient import TestClient
    from uapvf.server import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def web_json_client(client):
    """Logged-in client speaking the SPA's JSON dialect."""
    r = client.post(
        "/login",
        data={"token": TOKEN},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    return client


class TestWantsJson:
    """Spec §3.3 acceptance matrix — the whole data plane hangs off it."""

    @pytest.mark.parametrize(
        ("accept", "expected"),
        [
            ("application/json", True),
            ("application/json; charset=utf-8", True),
            ("text/html, application/json;q=0.8", False),
            ("application/json;q=0.9, text/html;q=0.5", True),
            ("application/json;q=0", False),  # explicit rejection
            ("*/*", False),  # browser default → HTML (D-32)
            (None, False),
            ("", False),
            ("text/html", False),
        ],
    )
    def test_accept_matrix(self, accept, expected):
        assert wj.wants_json(accept) is expected

    def test_application_wildcard_prefers_json(self):
        # application/* counts for json, nothing matches html.
        assert wj.wants_json("application/*") is True


class TestVaryAccept:
    """§3.3 / BE-04: negotiated routes carry Vary: Accept on success AND
    error, GET AND POST."""

    def test_cases_json_error_carries_vary(self, client):
        r = client.get("/cases", headers={"Accept": "application/json"})
        assert r.status_code == 401
        assert "accept" in r.headers.get("vary", "").lower()

    def test_cases_html_carries_vary(self, web_json_client):
        r = web_json_client.get("/cases", headers={"Accept": "text/html"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "accept" in r.headers.get("vary", "").lower()

    def test_login_error_carries_vary(self, client):
        r = client.post(
            "/login",
            data={"token": "wrong"},
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 401
        assert "accept" in r.headers.get("vary", "").lower()


class TestTypedCodes:
    """§3.4 error table — the SPA switches behavior on these codes."""

    def test_login_failure_is_auth_failed(self, client):
        r = client.post(
            "/login",
            data={"token": "wrong"},
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "auth_failed"

    def test_session_expired_code_on_cases(self, client):
        r = client.get("/cases", headers={"Accept": "application/json"})
        assert r.status_code == 401
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "auth_expired"
        assert "error" in body

    def test_login_success_json_contract(self, client):
        # D-33: {ok: true} only; the SPA hard-navigates and re-reads the
        # fresh bootstrap for csrf/mode.
        r = client.post(
            "/login",
            data={"token": TOKEN},
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert "set-cookie" in {k.lower() for k in r.headers.keys()}


class TestSpaDataPlane:
    """The endpoints the React console renders, speaking JSON."""

    def test_case_list_json_shape(self, web_json_client, submit):
        submit("nmm1.jpg")
        r = web_json_client.get(
            "/cases", headers={"Accept": "application/json"}
        )
        assert r.status_code == 200
        body = r.json()
        for key in ("cases", "total", "spend", "retention_days",
                    "estimated_cost_usd"):
            assert key in body
        assert body["total"] >= 1

    def test_intake_config_json_shape(self, web_json_client):
        r = web_json_client.get(
            "/cases/new", headers={"Accept": "application/json"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["cost_mode"] in ("mock", "live")
        assert "terms_short" in body
        assert "estimate_text" in body

    def test_case_detail_json_shape(self, web_json_client, complete_case):
        case_id, _ = complete_case()
        r = web_json_client.get(
            f"/cases/{case_id}", headers={"Accept": "application/json"}
        )
        assert r.status_code == 200
        body = r.json()
        for key in ("case", "stages", "lineage_outputs", "warnings",
                    "audit_events", "downloads"):
            assert key in body

    def test_report_negotiation(self, web_json_client, complete_case):
        case_id, _ = complete_case()
        json_resp = web_json_client.get(
            f"/cases/{case_id}/report",
            headers={"Accept": "application/json"},
        )
        assert json_resp.status_code == 200
        assert json_resp.headers["content-type"].startswith("application/json")
        assert "verdict" in json_resp.json()

        html_resp = web_json_client.get(
            f"/cases/{case_id}/report", headers={"Accept": "text/html"}
        )
        assert html_resp.status_code == 200
        assert not html_resp.headers["content-type"].startswith(
            "application/json"
        )

    def test_fiction_json_shape(self, web_json_client, complete_case):
        case_id, _ = complete_case()
        r = web_json_client.get(
            f"/cases/{case_id}/fiction",
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200
        body = r.json()
        for key in ("content", "available", "withheld", "verdict"):
            assert key in body

    def test_spend_json_shape(self, web_json_client):
        # §7.3 SpendSummary — the dashboard spend bar reads exactly these.
        # Round 4: exactly the two SDK call classes the MVP makes
        # (FR-016 lineage + fiction); the removed xenoscience stage has
        # no summary line.
        r = web_json_client.get(
            "/cases", headers={"Accept": "application/json"}
        )
        spend = r.json()["spend"]
        for key in ("month", "total_usd", "spend_cap_usd", "remaining_usd",
                    "lineage_calls", "fiction_calls", "lineage_cost_usd",
                    "fiction_cost_usd"):
            assert key in spend
        assert "xenoscience_calls" not in spend
        assert "xenoscience_cost_usd" not in spend


class TestFictionWithheldDerivation:
    """The fiction stage has a designed terminal where it COMPLETES but
    its output is cleared after label-validation failure
    (pipeline.py::_stage_fiction: fiction_ready=1, fiction_path=NULL,
    status='complete', audit 'cleared_after_validation_failure'). All
    three payload sites must derive 'withheld' for exactly that state —
    reporting it as "not ready yet" is a false terminal state, the
    honesty failure SOUL.md ¶1 names. Pins the round-5 fix.
    """

    @pytest.mark.parametrize("verdict", ["no_mundane_match",
                                         "insufficient_data"])
    def test_derivation_matrix(self, verdict):
        base = {"fiction_ready": 1, "fiction_path": None,
                "status": "complete", "verdict": verdict}
        assert wj._fiction_withheld(base) is True
        # mundane cases never attempt a seed: not withheld (DESIGN §11.3).
        assert wj._fiction_withheld(
            {**base, "verdict": "mundane_identified"}) is False
        # Mid-rerun window: the old cleared state is not terminal again,
        # so the UI reads "not ready" for exactly as long as that is true.
        assert wj._fiction_withheld(
            {**base, "status": "rerun_requested"}) is False
        # Stage still running → honestly "not ready".
        assert wj._fiction_withheld(
            {**base, "fiction_ready": 0, "status": "analyzing"}) is False
        # Seed on disk → available, never withheld.
        assert wj._fiction_withheld(
            {**base, "fiction_path": "/x/fic.md"}) is False

    def test_withheld_case_detail_payload(self, web_json_client,
                                          complete_case, monkeypatch):
        monkeypatch.setenv("UAPV_MOCK_FICTION_UNFIXABLE", "1")
        case_id, _ = complete_case("nmm1.jpg")
        r = web_json_client.get(
            f"/cases/{case_id}", headers={"Accept": "application/json"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["case"]["status"] == "complete"
        assert body["case"]["verdict"] == "insufficient_data"
        assert body["case"]["fiction_available"] is False
        assert body["case"]["fiction_withheld"] is True
        # Both payload sites agree — one derivation, one truth.
        assert body["downloads"]["fiction_available"] is False
        assert body["downloads"]["fiction_withheld"] is True

    def test_withheld_fiction_payload(self, web_json_client,
                                      complete_case, monkeypatch):
        monkeypatch.setenv("UAPV_MOCK_FICTION_UNFIXABLE", "1")
        case_id, _ = complete_case("nmm1.jpg")
        r = web_json_client.get(
            f"/cases/{case_id}/fiction",
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["withheld"] is True
        assert body["content"] is None
        assert body["verdict"] == "insufficient_data"

    def test_generated_fiction_not_withheld(self, web_json_client,
                                            complete_case):
        case_id, _ = complete_case("nmm1.jpg")
        detail = web_json_client.get(
            f"/cases/{case_id}", headers={"Accept": "application/json"}
        ).json()
        assert detail["case"]["fiction_available"] is True
        assert detail["case"]["fiction_withheld"] is False
        assert detail["downloads"]["fiction_withheld"] is False
        fic = web_json_client.get(
            f"/cases/{case_id}/fiction",
            headers={"Accept": "application/json"},
        ).json()
        assert fic["available"] is True
        assert fic["withheld"] is False
        assert fic["content"]

    def test_mundane_case_not_withheld(self, web_json_client,
                                       complete_case):
        # mundane_identified lands the same cleared row shape
        # (fiction_ready=1, fiction_path=NULL, status='complete') but by
        # construction it never had a seed attempt — it must not wear
        # the withheld state.
        from conftest import base_fields

        case_id, _ = complete_case(
            "aircraft1.jpg",
            base_fields(observed_at="2026-01-15T20:30:00+00:00",
                        latitude="34.05", longitude="-118.24"),
        )
        detail = web_json_client.get(
            f"/cases/{case_id}", headers={"Accept": "application/json"}
        ).json()
        assert detail["case"]["verdict"] == "mundane_identified"
        assert detail["case"]["fiction_available"] is False
        assert detail["case"]["fiction_withheld"] is False
        assert detail["downloads"]["fiction_withheld"] is False
        fic = web_json_client.get(
            f"/cases/{case_id}/fiction",
            headers={"Accept": "application/json"},
        ).json()
        assert fic["available"] is False
        assert fic["withheld"] is False


@needs_spa
class TestSpaServing:
    def test_installed_package_contains_console(self, monkeypatch, tmp_path):
        from uapvf.server import _ui_dist

        monkeypatch.chdir(tmp_path)
        packaged = _ui_dist()
        assert packaged.name == "ui_dist"
        assert (packaged / "index.html").is_file()
        assert (packaged / "brand-mark.svg").is_file()

    """§3.5–§3.8: shell serving, bootstrap injection, infra routes."""

    def test_ui_redirects_to_cases(self, client):
        r = client.get("/ui", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/ui/cases"

    def test_shell_unauthenticated_bootstrap(self, client):
        r = client.get("/ui/login")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert 'id="uapv-bootstrap"' in r.text
        assert '"authenticated": false' in r.text

    def test_shell_authenticated_bootstrap(self, web_json_client):
        r = web_json_client.get("/ui/cases")
        assert r.status_code == 200
        assert '"authenticated": true' in r.text
        # The SPA reads csrf + mode out of this block — never null here.
        assert '"csrf_token":' in r.text
        assert '"mode": "mock"' in r.text

    def test_spa_fallback_serves_shell_for_client_routes(
        self, web_json_client
    ):
        for path in ("/ui/cases/some-case-id", "/ui/benchmark", "/ui/terms"):
            r = web_json_client.get(path)
            assert r.status_code == 200, path
            assert 'id="uapv-bootstrap"' in r.text, path

    def test_brand_mark_route(self, client):
        r = client.get("/ui/brand-mark.png")
        assert r.status_code == 200
        assert r.headers["content-type"] in (
            "image/png",
            "image/png; charset=utf-8",
        )
        # PNG magic bytes — the route serves the real asset, not the shell.
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_brand_mark_root_route(self, client):
        # The shell's favicon link is href="/brand-mark.png"; the SPA
        # catch-all is /ui/{path} only, so the root path must be
        # registered explicitly or the favicon 404s in production.
        r = client.get("/brand-mark.png")
        assert r.status_code == 200
        assert r.headers["content-type"] in (
            "image/png",
            "image/png; charset=utf-8",
        )
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


class TestCaseListPagination:
    """Spec §7.3 / docs/API.md: GET /cases JSON defaults limit=50, max 200."""

    def _seed_n(self, submit, n):
        # Benchmark seeds are exempt from the 20/hour rate limit, so a single
        # test can submit >20 cases without mutating the rate-limit config.
        for _ in range(n):
            submit("nmm1.jpg", base_fields(buyer_ref="__benchmark_seed__"))

    def test_json_default_limit_not_truncated_at_20(self, web_json_client, submit):
        # A default of 20 would return only the first 20 of 25 rows; 50 must
        # return all 25.
        self._seed_n(submit, 25)
        r = web_json_client.get("/cases", headers={"Accept": "application/json"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 25
        assert len(body["cases"]) == 25

    def test_json_limit_query(self, web_json_client, submit):
        self._seed_n(submit, 3)
        r = web_json_client.get(
            "/cases?limit=1", headers={"Accept": "application/json"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert len(body["cases"]) == 1

    def test_json_offset_query(self, web_json_client, submit):
        self._seed_n(submit, 3)
        r = web_json_client.get(
            "/cases?limit=1&offset=2", headers={"Accept": "application/json"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert len(body["cases"]) == 1

    def test_json_limit_clamped_to_200(self, web_json_client, submit):
        self._seed_n(submit, 3)
        r = web_json_client.get(
            "/cases?limit=300", headers={"Accept": "application/json"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert len(body["cases"]) == 3
