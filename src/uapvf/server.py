"""Operator web server + API (spec §2 surfaces S1–S12, S8 health).

Single-operator FastAPI app bound to 127.0.0.1:8470 by default. Server-
rendered HTML everywhere; NO JavaScript is used for any functionality
(auto-refresh via <meta http-equiv="refresh">). Cookie sessions + CSRF for
web forms; Authorization: Bearer for /api/v1. The background worker pool
(4 threads, §8) starts in the lifespan.
"""
from __future__ import annotations

from uapvf.runtime_guard import install_import_guard

install_import_guard()

import asyncio
import contextlib
import hashlib
import html as html_lib
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from uapvf import __version__, audit, spend
from uapvf import auth
from uapvf import web_json as wj
from uapvf.config import Settings, get_settings, parse_iso, utcnow_iso
from uapvf.intake import IntakeError, VIDEO_MAX_DURATION_S, create_case
from uapvf import pipeline

TERMS_ITEMS = [
    "No output from this service claims extraterrestrial origin.",
    "Story material is labelled speculation and must not be cited as evidence.",
    "Do not strip non-evidence labels from fiction outputs.",
    "Verdicts are provisional and reflect only the tested mundane-explanation categories.",
    "The service does not assert that any object is extraterrestrial, non-human, or off-world.",
    "Fiction outputs are derived creative content, not forensic records.",
]
INTAKE_TERMS_TEXT = (
    "No output from this service claims extraterrestrial origin. Story "
    "material is labelled speculation and must not be cited as evidence. Do "
    "not strip non-evidence labels. Verdicts are provisional and reflect "
    "only the tested mundane-explanation categories."
)

_CSS = """
:root{--bg:#f5f3ee;--panel:#ffffff;--ink:#1a1a2e;--line:#c9c4b8;--accent:#856404;
--ok:#1b5e20;--warn:#856404;--bad:#b71c1c}
*{box-sizing:border-box}
body{font-family:Georgia,'Times New Roman',serif;background:var(--bg);color:var(--ink);
margin:0;line-height:1.5}
header{background:#22303f;color:#f5f3ee;padding:.6rem 1rem;display:flex;
justify-content:space-between;align-items:center;flex-wrap:wrap}
header a{color:#f5f3ee;margin-right:1rem;text-decoration:none}
header a:hover{text-decoration:underline}
main{max-width:1000px;margin:1.5rem auto;padding:0 1rem}
.panel{background:var(--panel);border:1px solid var(--line);padding:1rem 1.25rem;
margin-bottom:1rem;border-radius:6px}
h1{font-size:1.4rem}h2{font-size:1.1rem;border-bottom:1px solid var(--line);
padding-bottom:.25rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0}
th,td{border:1px solid var(--line);padding:.35rem .5rem;text-align:left;
font-size:.93rem;vertical-align:top}
th{background:#efece4}
label{display:block;margin:.6rem 0 .15rem;font-weight:bold}
input[type=text],input[type=number],input[type=password],select,textarea{
width:100%;padding:.4rem;border:1px solid var(--line);border-radius:4px;
font:inherit;background:#fff}
input[type=file]{margin:.3rem 0}
button,.btn{background:#22303f;color:#fff;border:none;padding:.45rem .9rem;
border-radius:4px;cursor:pointer;font:inherit;text-decoration:none;
display:inline-block}
.badge{display:inline-block;padding:.1rem .5rem;border-radius:99px;font-size:.8rem;
border:1px solid var(--line);background:#efece4}
.badge.queued{background:#e3f2fd}.badge.analyzing{background:#fff8e1}
.badge.complete{background:#e8f5e9}.badge.failed{background:#fdecea}
.badge.verdict_ready{background:#ede7f6}.badge.spend_capped{background:#fff3e0}
.badge.rerun_requested{background:#e0f2f1}
.badge.retention{background:#fff3cd;color:#856404;border-color:#856404}
.error{background:#fdecea;border:1px solid var(--bad);padding:.6rem .8rem;
border-radius:4px;margin:.5rem 0}
.hint{color:#52606d;font-size:.85rem}
.terms{background:#fff8e1;border:1px solid var(--accent);padding:.75rem;
border-radius:4px;font-size:.9rem}
code,.mono{font-family:'Courier New',monospace;font-size:.85rem;word-break:break-all}
"""


def esc(value) -> str:
    if value is None:
        return ""
    return html_lib.escape(str(value), quote=True)


def page(title: str, body: str, session_row=None, active: str = "") -> str:
    nav = ""
    if session_row is not None:
        nav = (
            '<nav>'
            f'<a href="/cases">Cases</a>'
            f'<a href="/cases/new">New case</a>'
            f'<a href="/benchmark">Benchmark</a>'
            '</nav>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — UAP Verdict Foundry</title>
<style>{_CSS}</style>
</head>
<body>
<header>
<div><strong>UAP Verdict Foundry</strong> <span class="hint">v{esc(__version__)}</span></div>
{nav}
</header>
<main>
{body}
</main>
</body>
</html>"""


def _settings(request: Request) -> Settings:
    return get_settings()


def _ui_dist() -> Path:
    """Location of the built SPA (source checkout or installed wheel)."""
    checkout_bundle = Path.cwd() / "ui" / "dist"
    if (checkout_bundle / "index.html").is_file():
        return checkout_bundle
    return Path(__file__).resolve().parent / "ui_dist"


def _db(request: Request):
    from uapvf import db as dbmod

    return dbmod.connect(request.app.state.settings.db_path)


def _session_for(request: Request, conn) -> tuple:
    settings = get_settings()
    return auth.resolve_session(conn, request.headers.get("cookie"),
                                settings.UAPV_OPERATOR_TOKEN)


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=302)


def _api_401() -> JSONResponse:
    return JSONResponse({"detail": "unauthorized"}, status_code=401)


def _bearer_ok(request: Request) -> bool:
    settings = get_settings()
    return auth.check_bearer(request.headers.get("authorization"),
                             settings.UAPV_OPERATOR_TOKEN)


# ---------------------------------------------------------------------------
# App factory + lifespan
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    from uapvf import db as dbmod

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        app.state.settings = settings
        dbmod.init_db(settings.var_dir)
        conn = dbmod.connect(settings.db_path)
        try:
            pipeline.reset_analyzing_on_startup(conn)
            pipeline.cleanup_orphan_tmp_files(settings)
            pipeline.cleanup_uncommitted_case_dirs(settings, conn)
            auth.cleanup_expired_sessions(conn)
        finally:
            conn.close()
        try:
            (settings.var_dir / "server.pid").write_text(str(os.getpid()))
        except Exception:
            pass
        pool = pipeline.WorkerPool(settings)
        pool.start()
        # No background schedulers in the MVP: automatic TLE catalog
        # refresh and automatic retention enforcement are deferred to
        # phase 2 (spec §1 deferred table; spec.md:86/87; §5 retention
        # "no auto-deletion"; A-012 manual refresh). The worker pool is
        # the only background component.
        app.state.worker_pool = pool
        app.state.benchmark_thread = None
        try:
            yield
        finally:
            benchmark_thread = getattr(app.state, "benchmark_thread", None)
            if benchmark_thread is not None and benchmark_thread.is_alive():
                benchmark_thread.join()
            pool.stop()
            try:
                pid_file = settings.var_dir / "server.pid"
                if pid_file.exists():
                    pid_file.unlink()
            except Exception:
                pass

    app = FastAPI(title="UAP Verdict Foundry", version=__version__,
                  lifespan=lifespan)
    _register_ui_routes(app)
    _register_routes(app)

    # UI spec §3.3 / BE-04: every content-negotiated route response —
    # success AND error, GET AND POST — carries Vary: Accept. A middleware
    # guarantees coverage on every return path of those handlers.
    _vary_exact = {"/login", "/logout", "/terms", "/cases",
                   "/benchmark", "/benchmark/run"}

    @app.middleware("http")
    async def vary_accept(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path in _vary_exact or path.startswith("/cases/"):
            existing = response.headers.get("vary")
            if existing is None:
                response.headers["Vary"] = "Accept"
            elif "accept" not in existing.lower():
                response.headers["Vary"] = existing + ", Accept"
        return response

    return app


# ---------------------------------------------------------------------------
# SPA infrastructure (UI spec §3.5–§3.8, §1.6 proposed additions)
# ---------------------------------------------------------------------------

def _register_ui_routes(app: FastAPI) -> None:
    from fastapi.staticfiles import StaticFiles

    ui_dist = _ui_dist()

    # 1. /ui (no trailing slash) → 302 to /ui/cases (BE-15).
    @app.get("/ui", include_in_schema=False)
    async def ui_redirect():
        return RedirectResponse(
            "/ui/cases", status_code=302,
            headers={"Cache-Control": "no-store"})

    # 2. /ui/assets/* — hashed SPA assets. Conditional registration
    #    (D-42): when dist/assets is absent a 503 handler takes its place
    #    so the server still boots (BE-22/BE-23).
    assets_dir = ui_dist / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/ui/assets",
            StaticFiles(directory=str(assets_dir)),
            name="ui-assets")
    else:
        @app.get("/ui/assets/{path:path}", include_in_schema=False)
        async def ui_assets_missing(path: str):
            return Response("UI not built — run 'npm run build' in ui/",
                            status_code=503, media_type="text/plain")

    # 2b. Public root files (the byte-exact brand mark). The SPA catch-all
    #     would otherwise serve index.html for these.
    mark_path = ui_dist / "brand-mark.png"
    if mark_path.is_file():
        @app.get("/ui/brand-mark.png", include_in_schema=False)
        async def ui_brand_mark():
            from fastapi.responses import FileResponse

            return FileResponse(
                str(mark_path), media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400"})

        @app.get("/brand-mark.png", include_in_schema=False)
        async def root_brand_mark():
            from fastapi.responses import FileResponse

            return FileResponse(
                str(mark_path), media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400",
                         "X-Content-Type-Options": "nosniff"})

    vector_mark_path = ui_dist / "brand-mark.svg"
    if vector_mark_path.is_file():
        @app.get("/ui/brand-mark.svg", include_in_schema=False)
        async def ui_vector_brand_mark():
            from fastapi.responses import FileResponse

            return FileResponse(
                str(vector_mark_path), media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=86400",
                         "X-Content-Type-Options": "nosniff"})

    app_icon_path = ui_dist / "app-icon.svg"
    if app_icon_path.is_file():
        @app.get("/ui/app-icon.svg", include_in_schema=False)
        async def ui_app_icon():
            from fastapi.responses import FileResponse

            return FileResponse(
                str(app_icon_path), media_type="image/svg+xml",
                headers={"Cache-Control": "public, max-age=86400",
                         "X-Content-Type-Options": "nosniff"})

    # 3. /ui/{path:path} — SPA fallback with the bootstrap block, CSP,
    #    and no-store caching (§3.5).
    @app.get("/ui/{path:path}", include_in_schema=False)
    async def ui_spa(request: Request, path: str):
        shell = wj.read_shell(ui_dist)
        if shell is None:
            return Response("UI not built — run 'npm run build' in ui/",
                            status_code=503, media_type="text/plain")
        settings = get_settings()
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
        finally:
            conn.close()
        csrf = session_row["csrf_token"] if session_row is not None else None
        mode = settings.analysis_mode
        html = wj.render_shell(shell, session_row is not None, csrf, mode)
        return Response(
            html, media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": wj.CSP_HEADER,
            })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _register_routes(app: FastAPI) -> None:

    # ---------------- S8 health -------------------------------------------
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        """Low-cardinality Prometheus metrics; contains no case identifiers."""
        settings = get_settings()
        from uapvf import db as dbmod
        conn = dbmod.connect(settings.db_path)
        try:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM cases GROUP BY status"
            ).fetchall()
            statuses = {row["status"]: int(row["n"]) for row in status_rows}
            audit_events = int(conn.execute(
                "SELECT COUNT(*) AS n FROM audit_events"
            ).fetchone()["n"])
            last_tle = conn.execute(
                "SELECT status FROM tle_catalogs ORDER BY catalog_id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        from uapvf.references import resolve as resolve_references
        references_valid = 1 if resolve_references(settings)["valid"] else 0
        lines = [
            "# HELP uapvf_build_info Product build information.",
            "# TYPE uapvf_build_info gauge",
            f'uapvf_build_info{{version="{__version__}"}} 1',
            "# HELP uapvf_cases Cases by durable state.",
            "# TYPE uapvf_cases gauge",
        ]
        for status in sorted(statuses):
            safe = str(status).replace('"', "")
            lines.append(f'uapvf_cases{{status="{safe}"}} {statuses[status]}')
        lines.extend([
            "# HELP uapvf_audit_events Total immutable audit events.",
            "# TYPE uapvf_audit_events counter",
            f"uapvf_audit_events {audit_events}",
            "# HELP uapvf_reference_integrity Frozen controlling references valid.",
            "# TYPE uapvf_reference_integrity gauge",
            f"uapvf_reference_integrity {references_valid}",
            "# HELP uapvf_tle_refresh_last Last TLE refresh status.",
            "# TYPE uapvf_tle_refresh_last gauge",
            f'uapvf_tle_refresh_last{{status="{last_tle["status"] if last_tle else "never"}"}} 1',
        ])
        return Response("\n".join(lines) + "\n",
                        media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/readyz")
    async def readyz():
        settings = get_settings()
        from uapvf import db as dbmod
        from uapvf.adapters.sneferu_adapter import sdk_reachable

        checks = {"mode": settings.analysis_mode}
        # DB write probe on a NEW connection.
        try:
            conn = dbmod.connect(settings.db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ROLLBACK")
            finally:
                conn.close()
            checks["db"] = "ok"
        except Exception:
            return JSONResponse(
                {"ready": False, "reason": "db not ok", **checks}, status_code=503
            )
        checks["sdk"] = "reachable" if sdk_reachable(settings) else "unreachable"
        checks["ffmpeg"] = (
            "ok"
            if shutil.which("ffmpeg") and shutil.which("ffprobe")
            else "missing"
        )

        def _probe_archive() -> str:
            path = settings.ARCHIVE_MIRROR_PATH
            if not path or not Path(path).exists():
                return "unreachable"
            try:
                import sqlite3

                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
                conn.execute("SELECT COUNT(*) FROM catalog_objects").fetchone()
                conn.close()
                return "reachable"
            except Exception:
                return "unreachable"

        checks["archive"] = _probe_archive()
        if not settings.ADSB_SOURCE_URL:
            checks["adsb"] = "unconfigured"
        else:
            from urllib.parse import urlparse
            adsb_host = (urlparse(settings.ADSB_SOURCE_URL).hostname or "").lower()
            if adsb_host.endswith("adsbexchange.com"):
                checks["adsb"] = (
                    "configured" if settings.ADSB_SOURCE_API_KEY
                    else "credentials_missing"
                )
            elif adsb_host.endswith("opensky-network.org"):
                checks["adsb"] = (
                    "configured" if settings.OPENSKY_CLIENT_ID
                    and settings.OPENSKY_CLIENT_SECRET
                    else "configured_anonymous_evaluation_only"
                )
            else:
                checks["adsb"] = "configured"

        conn = dbmod.connect(settings.db_path)
        try:
            checks["pending_cases"] = conn.execute(
                "SELECT COUNT(*) AS n FROM cases WHERE status = 'queued'"
            ).fetchone()["n"]
            if not settings.TLE_CATALOG_URL:
                checks["tle"] = "unconfigured"
            else:
                from uapvf.tle import latest_catalog
                catalog = latest_catalog(conn, settings)
                if catalog is None or not catalog["path"] or not Path(
                    catalog["path"]
                ).is_file():
                    checks["tle"] = "unavailable"
                else:
                    try:
                        from uapvf.config import parse_iso
                        age_days = (
                            parse_iso(utcnow_iso()) - parse_iso(catalog["fetched_at"])
                        ).total_seconds() / 86400.0
                    except Exception:
                        age_days = float("inf")
                    checks["tle"] = (
                        "fresh" if age_days <= settings.UAPV_TLE_MAX_AGE_DAYS
                        else "stale"
                    )
        finally:
            conn.close()
        # S8 contract: readiness requires exactly the DB write probe
        # (checked above), SDK reachability in live mode (mock mode counts
        # as reachable), and ffmpeg/ffprobe on PATH. Archive, ADS-B, and
        # TLE reachability are REPORTED above but are never required for
        # `ready: true` — no other 503 gate exists.
        if checks["sdk"] != "reachable" and not settings.mock_mode:
            return JSONResponse(
                {"ready": False, "reason": "sdk unreachable", **checks},
                status_code=503,
            )
        if checks["ffmpeg"] != "ok":
            return JSONResponse(
                {"ready": False, "reason": "ffmpeg missing", **checks},
                status_code=503,
            )
        return {"ready": True, **checks}

    # ---------------- S9 login / S10 logout / S12 terms --------------------
    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request):
        body = f"""
<div class="panel">
<h1>Operator login</h1>
<form method="post" action="/login">
<label for="token">Operator token</label>
<input type="password" id="token" name="token" autocomplete="off" required>
<p class="hint">The token from <code>.env</code> (UAPV_OPERATOR_TOKEN).</p>
<button type="submit">Log in</button>
</form>
</div>"""
        return page("Login", body)

    @app.post("/login")
    async def login_submit(request: Request, token: str = Form(default="")):
        settings = get_settings()
        conn = _db(request)
        try:
            auth.cleanup_expired_sessions(conn)
            wants = wj.wants_json_request(request)
            if not auth.check_operator_token(token, settings.UAPV_OPERATOR_TOKEN):
                if wants:
                    # Spec §3.4 / BE-21: login failure is auth_failed,
                    # never auth_expired.
                    return wj.jerr("Invalid operator token.", "auth_failed",
                                   401, ok=False)
                return Response(
                    page(
                        "Login",
                        '<div class="panel"><h1>Operator login</h1>'
                        '<div class="error">Invalid operator token.</div>'
                        '<p><a href="/login">Try again</a></p></div>',
                    ),
                    status_code=401,
                    media_type="text/html",
                )
            session = auth.create_session(conn)
            cookie = auth.make_cookie(session["session_id"],
                                      settings.UAPV_OPERATOR_TOKEN)
            if wants:
                # Spec D-33: {ok: true} only — the SPA hard-navigates and
                # reads the fresh bootstrap for csrf/mode.
                resp = JSONResponse({"ok": True})
            else:
                resp = RedirectResponse("/cases", status_code=303)
            resp.set_cookie(auth.COOKIE_NAME, cookie, httponly=True, samesite="lax")
            return resp
        finally:
            conn.close()

    @app.post("/logout")
    async def logout(request: Request, csrf_token: str = Form(default="")):
        settings = get_settings()
        conn = _db(request)
        try:
            if wj.wants_json_request(request):
                session_row, session_id = _session_for(request, conn)
                if session_row is None:
                    return wj.jerr("Session expired", "auth_expired", 401,
                                   ok=False)
                if not auth.check_csrf(session_row, csrf_token):
                    return wj.jerr("CSRF validation failed", "csrf_expired",
                                   403, ok=False)
                auth.delete_session(conn, session_id)
                auth.cleanup_expired_sessions(conn)
                resp = JSONResponse({"ok": True})
                resp.set_cookie(auth.COOKIE_NAME, "", max_age=0)
                return resp
            session_row, session_id = _session_for(request, conn)
            if session_row is not None:
                auth.delete_session(conn, session_id)
            auth.cleanup_expired_sessions(conn)
        finally:
            conn.close()
        resp = RedirectResponse("/login", status_code=302)
        resp.set_cookie(auth.COOKIE_NAME, "", max_age=0)
        return resp

    @app.get("/terms", response_class=HTMLResponse)
    async def terms(request: Request):
        if wj.wants_json_request(request):
            return JSONResponse(wj.terms_payload(TERMS_ITEMS))
        items = "".join(f"<li>{esc(t)}</li>" for t in TERMS_ITEMS)
        body = f"""
<div class="panel">
<h1>Terms of service</h1>
<ol>{items}</ol>
</div>"""
        return page("Terms", body)

    # ---------------- S1 intake --------------------------------------------
    def _intake_form(settings: Settings, errors: Optional[dict] = None,
                     error_message: str = "", values: Optional[dict] = None,
                     session_row=None) -> str:
        values = values or {}
        estimate = settings.estimated_case_cost_usd()
        mode_label = "mock mode" if settings.mock_mode else "live mode"
        error_html = ""
        if error_message:
            error_html = f'<div class="error">{esc(error_message)}</div>'
        field_errors = errors or {}

        def fe(name):
            msg = field_errors.get(name)
            return f'<div class="error">{esc(msg)}</div>' if msg else ""

        def val(name, default=""):
            return esc(values.get(name, default))

        csrf = session_row["csrf_token"] if session_row is not None else ""
        return f"""
<div class="panel">
<h1>New case</h1>
<p>Estimated cost: up to <strong>${estimate:.2f}</strong> per case ({mode_label}).
The estimate is an upper bound (fiction cost is always included because the
verdict is unknown at intake).</p>
<div class="terms">
<p>{esc(INTAKE_TERMS_TEXT)} <a href="/terms">Full terms</a>.</p>
</div>
{error_html}
<form method="post" action="/cases/new" enctype="multipart/form-data">
<input type="hidden" name="csrf_token" value="{esc(csrf)}">
<label for="media">Media file (JPEG/PNG/WebP ≤ 50 MB; MP4/MOV/WebM ≤ {int(VIDEO_MAX_DURATION_S)} s,
≤ 250 MB)</label>
<input type="file" id="media" name="media" accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.webm" required>
{fe("media")}
<label for="observed_at">Capture datetime (ISO 8601 with offset) *</label>
<input type="text" id="observed_at" name="observed_at" value="{val('observed_at')}" required>
{fe("observed_at")}
<label for="latitude">Latitude (decimal degrees) *</label>
<input type="text" id="latitude" name="latitude" value="{val('latitude')}" required>
{fe("latitude")}
<label for="longitude">Longitude (decimal degrees) *</label>
<input type="text" id="longitude" name="longitude" value="{val('longitude')}" required>
{fe("longitude")}
<label for="viewing_direction">Viewing direction (azimuth[,elevation], degrees)</label>
<input type="text" id="viewing_direction" name="viewing_direction" value="{val('viewing_direction')}">
{fe("viewing_direction")}
<label for="shape">Shape</label>
<input type="text" id="shape" name="shape" value="{val('shape')}">
<label for="count">Count</label>
<input type="text" id="count" name="count" value="{val('count')}">
<label for="duration_seconds">Duration (seconds)</label>
<input type="text" id="duration_seconds" name="duration_seconds" value="{val('duration_seconds')}">
{fe("duration_seconds")}
<label for="weather">Weather</label>
<input type="text" id="weather" name="weather" value="{val('weather')}">
<label for="behavior_notes">Behavior notes (≤ 4000 chars)</label>
<textarea id="behavior_notes" name="behavior_notes" rows="4">{val('behavior_notes')}</textarea>
{fe("behavior_notes")}
<label for="buyer_ref">Buyer reference code</label>
<input type="text" id="buyer_ref" name="buyer_ref" value="{val('buyer_ref')}">
<label for="location_text">Location text (place name)</label>
<input type="text" id="location_text" name="location_text" value="{val('location_text')}">
<label for="payment_status">Payment status</label>
<select id="payment_status" name="payment_status">
<option value="unpaid">unpaid</option>
<option value="paid">paid</option>
<option value="comped">comped</option>
</select>
<p><label style="display:inline"><input type="checkbox" name="terms_accepted" value="true" required>
I accept the terms above</label></p>
{fe("terms_accepted")}
<button type="submit">Submit case</button>
</form>
</div>"""

    @app.get("/cases/new", response_class=HTMLResponse)
    async def cases_new(request: Request):
        settings = get_settings()
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
            if session_row is None:
                if wj.wants_json_request(request):
                    return wj.jerr("Session expired", "auth_expired", 401)
                return _login_redirect()
            if wj.wants_json_request(request):
                return JSONResponse(wj.intake_config_payload(settings))
            return page("New case", _intake_form(settings, session_row=session_row),
                        session_row)
        finally:
            conn.close()

    async def _fields_from_form(request: Request) -> dict:
        form = await request.form()
        fields = {}
        for key in ("observed_at", "latitude", "longitude", "viewing_direction",
                    "shape", "count", "duration_seconds", "weather",
                    "behavior_notes", "buyer_ref", "location_text",
                    "payment_status", "terms_accepted"):
            value = form.get(key)
            if value not in (None, ""):
                fields[key] = value
        return fields

    async def _store_upload(upload: UploadFile, settings: Settings) -> Path:
        settings.var_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(settings.var_dir),
                                        prefix="upload_", suffix=".bin")
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await upload.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        return Path(tmp_name)

    def _intake_error_response(exc: IntakeError, api: bool, request=None,
                               settings=None, session_row=None):
        if api:
            # S1: per-field inline errors on validation failure are 400 for
            # the form but 422 for the API surface. (The explicit
            # "terms acceptance required" 400 mandated by S1/FR-001 is
            # raised directly in the handlers and does not flow through
            # here.)
            status_code = 422 if exc.status_code == 400 else exc.status_code
            return JSONResponse(
                {"detail": exc.message, "field_errors": exc.field_errors},
                status_code=status_code,
            )
        body = _intake_form(settings, errors=exc.field_errors,
                            error_message=exc.message, session_row=session_row)
        return Response(page("New case", body, session_row),
                        status_code=exc.status_code, media_type="text/html")

    @app.post("/cases/new")
    async def cases_new_submit(request: Request):
        settings = get_settings()
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
            wants = wj.wants_json_request(request)
            if session_row is None:
                if wants:
                    return wj.jerr("Session expired", "auth_expired", 401,
                                   ok=False)
                return _login_redirect()
            form = await request.form()
            if not auth.check_csrf(session_row, form.get("csrf_token")):
                if wants:
                    return wj.jerr("CSRF validation failed", "csrf_expired",
                                   403, ok=False)
                return Response(page("Forbidden",
                                     '<div class="panel"><div class="error">CSRF validation failed.</div></div>',
                                     session_row),
                                status_code=403, media_type="text/html")
            if wants:
                terms = form.get("terms_accepted")
                if not (terms is True or str(terms).lower() == "true"):
                    return wj.jerr("terms acceptance required", "bad_request",
                                   400, ok=False)
            upload = form.get("media")
            fields = await _fields_from_form(request)
            if upload is None or getattr(upload, "filename", "") == "":
                exc = IntakeError(400, "media file is required",
                                  {"media": "media file is required"})
                if wants:
                    return wj.jerr(exc.message, "validation_error", 422,
                                   ok=False, extra={"errors": [
                                       {"field": k, "message": v}
                                       for k, v in exc.field_errors.items()]})
                return _intake_error_response(exc, False, request, settings,
                                              session_row)
            tmp = await _store_upload(upload, settings)
            try:
                result = create_case(tmp, fields, settings, conn)
            except IntakeError as exc:
                if wants:
                    # Spec §3.4: 422 per-field, else typed status codes.
                    code_map = {413: "file_too_large",
                                415: "unsupported_type",
                                429: "rate_limited",
                                507: "insufficient_storage"}
                    code = code_map.get(exc.status_code)
                    if code is not None:
                        return wj.jerr(exc.message, code, exc.status_code,
                                       ok=False)
                    return wj.jerr(exc.message, "validation_error", 422,
                                   ok=False, extra={"errors": [
                                       {"field": k, "message": v}
                                       for k, v in exc.field_errors.items()]})
                return _intake_error_response(exc, False, request, settings,
                                              session_row)
            finally:
                with contextlib.suppress(Exception):
                    tmp.unlink()
            if wants:
                return JSONResponse({
                    "ok": True,
                    "case_id": result["case_id"],
                    "status": result.get("status", "queued"),
                    "estimated_cost_usd": result.get(
                        "estimated_cost_usd",
                        settings.estimated_case_cost_usd()),
                })
            return RedirectResponse(f"/cases/{result['case_id']}", status_code=303)
        finally:
            conn.close()

    # ---------------- API intake (multipart + bearer) -----------------------
    @app.post("/api/v1/cases")
    async def api_create_case(request: Request):
        settings = get_settings()
        if not _bearer_ok(request):
            return _api_401()
        conn = _db(request)
        try:
            form = await request.form()
            terms = form.get("terms_accepted")
            if not (terms is True or str(terms).lower() == "true"):
                return JSONResponse({"detail": "terms acceptance required"},
                                    status_code=400)
            upload = form.get("media")
            fields = await _fields_from_form(request)
            if upload is None or getattr(upload, "filename", "") == "":
                return JSONResponse({"detail": "media file is required"},
                                    status_code=400)
            tmp = await _store_upload(upload, settings)
            try:
                result = create_case(tmp, fields, settings, conn)
            except IntakeError as exc:
                return _intake_error_response(exc, True)
            finally:
                with contextlib.suppress(Exception):
                    tmp.unlink()
            return JSONResponse(result, status_code=202)
        finally:
            conn.close()

    # ---------------- S2 case index -----------------------------------------
    def _case_list_payload(conn, settings: Settings, limit: int, offset: int):
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        total = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()["n"]
        rows = conn.execute(
            "SELECT * FROM cases ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        items = [
            {
                "case_id": r["case_id"],
                "created_at": r["created_at"],
                "status": r["status"],
                "verdict": r["verdict"],
                "buyer_ref": r["buyer_ref"],
                "payment_status": r["payment_status"],
                "quality_score": r["quality_score"],
                "agreement_fraction": r["agreement_fraction"],
            }
            for r in rows
        ]
        return items, total, limit, offset

    @app.get("/cases", response_class=HTMLResponse)
    async def cases_index(request: Request, status: Optional[str] = None,
                          limit: int = 50, offset: int = 0):
        settings = get_settings()
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
            if session_row is None:
                if wj.wants_json_request(request):
                    return wj.jerr("Session expired", "auth_expired", 401)
                return _login_redirect()
            if wj.wants_json_request(request):
                # Spec §7.3: JSON branch defaults limit=50 (max 200) and
                # adds spend + retention + cost estimates. BE-13.
                return JSONResponse(wj.case_list_payload(
                    conn, settings, status, limit, offset))
            from datetime import datetime, timedelta, timezone

            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(days=int(settings.RETENTION_DAYS))
            ).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
            query = "SELECT * FROM cases"
            params: list = []
            if status:
                query += " WHERE status = ?"
                params.append(status)
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params += [max(1, min(int(limit), 200)), max(0, int(offset))]
            rows = conn.execute(query, params).fetchall()
            if not rows:
                body = (
                    '<div class="panel"><h1>Cases</h1>'
                    "<p>No cases yet. "
                    '<a href="/cases/new">Create your first case.</a></p></div>'
                )
                return page("Cases", body, session_row)
            trs = []
            for r in rows:
                badge_extra = ""
                if r["created_at"] < cutoff:
                    badge_extra = (
                        ' <span class="badge retention">Retention period '
                        "exceeded — consider deletion</span>"
                    )
                verdict = esc(r["verdict"]) if r["verdict"] else "—"
                trs.append(
                    "<tr>"
                    f'<td><a href="/cases/{esc(r["case_id"])}"><code>{esc(r["case_id"][:8])}</code></a></td>'
                    f"<td>{esc(r['created_at'])}</td>"
                    f'<td><span class="badge {esc(r["status"])}">{esc(r["status"])}</span>{badge_extra}</td>'
                    f"<td>{verdict}</td>"
                    f"<td>{esc(r['buyer_ref'] or '—')}</td>"
                    f"<td>{esc(r['payment_status'])}</td>"
                    "<td>"
                    f'<form method="post" action="/cases/{esc(r["case_id"])}/retry" style="display:inline">'
                    f'<input type="hidden" name="csrf_token" value="{esc(session_row["csrf_token"])}">'
                    '<button type="submit">retry</button></form> '
                    f'<form method="post" action="/cases/{esc(r["case_id"])}/rerun" style="display:inline">'
                    f'<input type="hidden" name="csrf_token" value="{esc(session_row["csrf_token"])}">'
                    '<button type="submit">rerun</button></form> '
                    f'<a href="/cases/{esc(r["case_id"])}/delete/confirm">delete</a>'
                    "</td>"
                    "</tr>"
                )
            body = f"""
<div class="panel">
<h1>Cases</h1>
<table><thead><tr><th>Case</th><th>Created</th><th>Status</th><th>Verdict</th>
<th>Buyer ref</th><th>Payment</th><th>Actions</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table>
<p><a class="btn" href="/cases/new">New case</a></p>
</div>"""
            return page("Cases", body, session_row)
        finally:
            conn.close()

    @app.get("/api/v1/cases")
    async def api_cases(request: Request, limit: int = 50, offset: int = 0):
        if not _bearer_ok(request):
            return _api_401()
        settings = get_settings()
        conn = _db(request)
        try:
            items, total, limit_u, offset_u = _case_list_payload(
                conn, settings, limit, offset
            )
            return JSONResponse(
                items, headers={"X-Total-Count": str(total)}
            )
        finally:
            conn.close()

    # ---------------- S3 case detail ----------------------------------------
    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    async def case_detail(request: Request, case_id: str):
        settings = get_settings()
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
            if session_row is None:
                if wj.wants_json_request(request):
                    return wj.jerr("Session expired", "auth_expired", 401)
                return _login_redirect()
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if row is None:
                if wj.wants_json_request(request):
                    return wj.jerr("Case not found", "not_found", 404)
                return Response(
                    page("Not found", '<div class="panel"><div class="error">Case not found.</div></div>',
                         session_row),
                    status_code=404, media_type="text/html",
                )
            if wj.wants_json_request(request):
                # Spec §7.4 + D-30: capped arrays with total_* counts.
                return JSONResponse(
                    wj.case_detail_payload(conn, settings, row))
            stages = conn.execute(
                "SELECT * FROM pipeline_stage_runs WHERE case_id = ? AND "
                "run_version = ? ORDER BY run_id",
                (case_id, row["run_version"]),
            ).fetchall()
            battery = conn.execute(
                "SELECT * FROM battery_results WHERE case_id = ? AND run_version = ?",
                (case_id, row["run_version"]),
            ).fetchall()
            audit_rows = conn.execute(
                "SELECT * FROM audit_events WHERE case_id = ? ORDER BY event_id DESC LIMIT 25",
                (case_id,),
            ).fetchall()
            stage_rows = "".join(
                f"<tr><td>{esc(s['stage'])}</td><td>{esc(s['status'])}</td>"
                f"<td>{esc(s['started_at'])}</td><td>{esc(s['completed_at'] or '—')}</td>"
                f"<td>{esc(s['error_detail'] or '')}</td></tr>"
                for s in stages
            ) or '<tr><td colspan="5">No stages recorded yet.</td></tr>'
            battery_rows = "".join(
                f"<tr><td>{esc(b['category'])}</td>"
                f'<td class="{esc(b["result"])}">{esc(b["result"])}</td>'
                f"<td>{esc(b['evidence_citation'] or '—')}</td></tr>"
                for b in battery
            ) or '<tr><td colspan="3">Battery not run yet.</td></tr>'
            audit_html = "".join(
                f"<tr><td>{esc(a['event_id'])}</td><td>{esc(a['actor'])}</td>"
                f"<td>{esc(a['action'])}</td><td>{esc(a['recorded_at'])}</td></tr>"
                for a in audit_rows
            )
            downloads = ""
            # FR-022: the previous report stays downloadable while a rerun
            # is in flight (archived copy served by _serve_report).
            if row["status"] in ("verdict_ready", "complete", "rerun_requested"):
                downloads += f'<a class="btn" href="/cases/{esc(case_id)}/report">Download report</a> '
            if row["status"] == "complete" and row["fiction_path"]:
                downloads += f'<a class="btn" href="/cases/{esc(case_id)}/fiction">Download fiction seed</a>'
            error_html = ""
            if row["status"] == "failed":
                error_html = (
                    f'<div class="error">Case failed: {esc(row["error_detail"])} '
                    "(see audit log below)</div>"
                )
            refresh = (
                '<meta http-equiv="refresh" content="5">'
                if row["status"] in ("queued", "analyzing", "rerun_requested")
                else ""
            )
            verdict_html = (
                f'<p>Verdict: <strong>{esc(row["verdict"])}</strong></p>'
                f"<p>{esc(row['verdict_text'])}</p>"
                if row["verdict"]
                else "<p>Verdict pending.</p>"
            )
            body = f"""
{refresh}
<div class="panel">
<h1>Case <code>{esc(case_id)}</code></h1>
<p>Status: <span class="badge {esc(row['status'])}">{esc(row['status'])}</span>
 · Payment: <strong>{esc(row['payment_status'])}</strong>
 · Run version {esc(row['run_version'])} · Retry count {esc(row['retry_count'])}</p>
{error_html}
{verdict_html}
{downloads}
</div>
<div class="panel">
<h2>Update payment status</h2>
<form method="post" action="/cases/{esc(case_id)}/payment">
<input type="hidden" name="csrf_token" value="{esc(session_row['csrf_token'])}">
<select name="payment_status">
<option value="unpaid">unpaid</option><option value="paid">paid</option>
<option value="comped">comped</option>
</select>
<button type="submit">Update</button>
</form>
</div>
<div class="panel">
<h2>Stage progression (run v{esc(row['run_version'])})</h2>
<table><thead><tr><th>Stage</th><th>Status</th><th>Started</th><th>Completed</th>
<th>Error</th></tr></thead><tbody>{stage_rows}</tbody></table>
<h2>Battery results</h2>
<table><thead><tr><th>Category</th><th>Result</th><th>Evidence</th></tr></thead>
<tbody>{battery_rows}</tbody></table>
<h2>Recent audit events</h2>
<table><thead><tr><th>ID</th><th>Actor</th><th>Action</th><th>At</th></tr></thead>
<tbody>{audit_html}</tbody></table>
</div>"""
            return page(f"Case {case_id[:8]}", body, session_row)
        finally:
            conn.close()

    # ---------------- S4/S5 downloads ---------------------------------------
    def _latest_archived_report(case_dir: Path, case_id: str):
        """Newest report_{case_id}_v{N}.html archive written by
        pipeline._prepare_rerun, or None if no archive exists yet."""
        best = None
        best_version = -1
        for candidate in case_dir.glob(f"report_{case_id}_v*.html"):
            try:
                version = int(candidate.stem.rsplit("_v", 1)[1])
            except (IndexError, ValueError):
                continue
            if version > best_version:
                best, best_version = candidate, version
        return best

    def _report_not_ready(api: bool):
        if api:
            return JSONResponse({"detail": "report not ready"}, status_code=409)
        return Response(page("Conflict",
                             '<div class="panel"><div class="error">Report not ready for this case status.</div></div>'),
                        status_code=409, media_type="text/html")

    def _serve_report(conn, case_id: str, api: bool):
        row = conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row is None:
            if api:
                return JSONResponse({"detail": "case not found"}, status_code=404)
            return Response(page("Not found",
                                 '<div class="panel"><div class="error">Case not found.</div></div>'),
                            status_code=404, media_type="text/html")
        status = row["status"]
        case_dir = (
            Path(row["report_path"]).parent
            if row["report_path"]
            else get_settings().cases_dir / case_id
        )
        if status == "rerun_requested":
            # FR-022: during rerun_requested the archived report copy
            # remains downloadable. Serve the newest report_{id}_v{N}.html
            # archive; before the worker claims the rerun the archive does
            # not exist yet, so fall back to the current report files,
            # which are still the previous run's.
            sha_col = "report_json_sha256" if api else "report_sha256"
            if api:
                path = case_dir / "report.json"
            else:
                path = (_latest_archived_report(case_dir, case_id)
                        or (case_dir / "report.html"))
            if not path.exists():
                return _report_not_ready(api)
        elif status in ("verdict_ready", "complete"):
            name = "report.json" if api else "report.html"
            sha_col = "report_json_sha256" if api else "report_sha256"
            path = case_dir / name
            if not path.exists():
                return JSONResponse({"detail": "report file missing on disk"},
                                    status_code=500)
        else:
            return _report_not_ready(api)
        data = path.read_bytes()
        served_sha256 = hashlib.sha256(data).hexdigest()
        media = "application/json" if api else "text/html; charset=utf-8"
        return Response(
            data,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="{path.name}"',
                # Hash the bytes actually served.  During reruns this can be
                # an archived vN report rather than the path/hash currently
                # stored on the case row.
                "X-UAPVF-SHA256": served_sha256,
            },
        )

    @app.get("/cases/{case_id}/report")
    async def case_report(request: Request, case_id: str):
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
            if session_row is None:
                if wj.wants_json_request(request):
                    return wj.jerr("Session expired", "auth_expired", 401)
                return _login_redirect()
            if wj.wants_json_request(request):
                # Spec §3.9: JSON sidecar contract (ReportJson or 404).
                row = conn.execute(
                    "SELECT * FROM cases WHERE case_id = ?", (case_id,)
                ).fetchone()
                if row is None:
                    return wj.jerr("Case not found", "not_found", 404)
                settings = get_settings()
                if not wj._report_on_disk(row, settings.cases_dir / case_id):
                    return wj.jerr("Report not yet available", "not_found", 404)
                case_dir = (Path(row["report_path"]).parent
                            if row["report_path"]
                            else settings.cases_dir / case_id)
                jpath = case_dir / "report.json"
                try:
                    data = json.loads(jpath.read_text(encoding="utf-8"))
                except Exception:
                    return wj.jerr("Report not yet available", "not_found", 404)
                return JSONResponse(data)
            return _serve_report(conn, case_id, api=False)
        finally:
            conn.close()

    @app.get("/api/v1/cases/{case_id}/report.json")
    async def api_case_report(request: Request, case_id: str):
        if not _bearer_ok(request):
            return _api_401()
        conn = _db(request)
        try:
            return _serve_report(conn, case_id, api=True)
        finally:
            conn.close()

    def _serve_fiction(conn, case_id: str, api: bool):
        row = conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        missing = (
            row is None
            or row["verdict"] == "mundane_identified"
            or not row["fiction_path"]
            or row["status"] not in ("verdict_ready", "complete")
            or not Path(row["fiction_path"]).exists()
        )
        if missing:
            if api:
                return JSONResponse({"detail": "no fiction seed for this case"},
                                    status_code=404)
            return Response(page("Not found",
                                 '<div class="panel"><div class="error">No fiction seed for this case.</div></div>'),
                            status_code=404, media_type="text/html")
        data = Path(row["fiction_path"]).read_bytes()
        return Response(
            data,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition":
                    f'attachment; filename="fiction_{case_id}.fic.md"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/cases/{case_id}/fiction")
    async def case_fiction(request: Request, case_id: str):
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
            if session_row is None:
                if wj.wants_json_request(request):
                    return wj.jerr("Session expired", "auth_expired", 401)
                return _login_redirect()
            if wj.wants_json_request(request):
                # Spec §3.9: FictionResponse {content, available, withheld,
                # verdict} — unavailable states are 200s, only a missing
                # case is a 404.
                row = conn.execute(
                    "SELECT * FROM cases WHERE case_id = ?", (case_id,)
                ).fetchone()
                if row is None:
                    return wj.jerr("Case not found", "not_found", 404)
                return JSONResponse(wj.fiction_payload(conn, case_id, row))
            return _serve_fiction(conn, case_id, api=False)
        finally:
            conn.close()

    @app.get("/api/v1/cases/{case_id}/fiction")
    async def api_case_fiction(request: Request, case_id: str):
        if not _bearer_ok(request):
            return _api_401()
        conn = _db(request)
        try:
            return _serve_fiction(conn, case_id, api=True)
        finally:
            conn.close()

    # ---------------- case actions (CSRF-protected) --------------------------
    async def _csrf_guarded(request: Request, conn):
        session_row, _ = _session_for(request, conn)
        if session_row is None:
            return None, _login_redirect()
        form = await request.form()
        if not auth.check_csrf(session_row, form.get("csrf_token")):
            return session_row, Response(
                page("Forbidden",
                     '<div class="panel"><div class="error">CSRF validation failed.</div></div>',
                     session_row),
                status_code=403, media_type="text/html",
            )
        return session_row, None

    @app.post("/cases/{case_id}/retry")
    async def case_retry(request: Request, case_id: str):
        conn = _db(request)
        try:
            if wj.wants_json_request(request):
                session_row, _ = _session_for(request, conn)
                if session_row is None:
                    return wj.jerr("Session expired", "auth_expired", 401,
                                   ok=False)
                form = await request.form()
                if not auth.check_csrf(session_row, form.get("csrf_token")):
                    return wj.jerr("CSRF validation failed", "csrf_expired",
                                   403, ok=False)
                result = pipeline.retry_case(conn, case_id, force=False)
                if not result.get("ok"):
                    if result.get("error") == "case not found":
                        return wj.jerr(result["error"], "not_found", 404,
                                       ok=False)
                    return wj.jerr(result.get("error", "conflict"),
                                   "conflict", 409, ok=False)
                return JSONResponse({"ok": True, "status": "queued"})
            session_row, err = await _csrf_guarded(request, conn)
            if err:
                return err
            pipeline.retry_case(conn, case_id, force=False)
            return RedirectResponse(f"/cases/{case_id}", status_code=303)
        finally:
            conn.close()

    @app.post("/cases/{case_id}/rerun")
    async def case_rerun(request: Request, case_id: str):
        conn = _db(request)
        try:
            if wj.wants_json_request(request):
                session_row, _ = _session_for(request, conn)
                if session_row is None:
                    return wj.jerr("Session expired", "auth_expired", 401,
                                   ok=False)
                form = await request.form()
                if not auth.check_csrf(session_row, form.get("csrf_token")):
                    return wj.jerr("CSRF validation failed", "csrf_expired",
                                   403, ok=False)
                result = pipeline.rerun_case(conn, case_id)
                if not result.get("ok"):
                    if result.get("error") == "case not found":
                        return wj.jerr(result["error"], "not_found", 404,
                                       ok=False)
                    return wj.jerr(result.get("error", "conflict"),
                                   "conflict", 409, ok=False)
                return JSONResponse({"ok": True,
                                     "status": "rerun_requested"})
            session_row, err = await _csrf_guarded(request, conn)
            if err:
                return err
            pipeline.rerun_case(conn, case_id)
            return RedirectResponse(f"/cases/{case_id}", status_code=303)
        finally:
            conn.close()

    @app.post("/cases/{case_id}/payment")
    async def case_payment(request: Request, case_id: str):
        conn = _db(request)
        try:
            if wj.wants_json_request(request):
                session_row, _ = _session_for(request, conn)
                if session_row is None:
                    return wj.jerr("Session expired", "auth_expired", 401,
                                   ok=False)
                form = await request.form()
                if not auth.check_csrf(session_row, form.get("csrf_token")):
                    return wj.jerr("CSRF validation failed", "csrf_expired",
                                   403, ok=False)
                new_status = str(form.get("payment_status"))
                result = pipeline.set_payment(conn, case_id, new_status)
                if not result.get("ok"):
                    if result.get("error") == "case not found":
                        return wj.jerr(result["error"], "not_found", 404,
                                       ok=False)
                    if result.get("error") == "invalid payment status":
                        return wj.jerr(result["error"], "validation_error",
                                       422, ok=False, extra={"errors": [
                                           {"field": "payment_status",
                                            "message": result["error"]}]})
                    return wj.jerr(result.get("error", "conflict"),
                                   "conflict", 409, ok=False)
                return JSONResponse({"ok": True,
                                     "payment_status": result["new"]})
            session_row, err = await _csrf_guarded(request, conn)
            if err:
                return err
            form = await request.form()
            pipeline.set_payment(conn, case_id, str(form.get("payment_status")))
            return RedirectResponse(f"/cases/{case_id}", status_code=303)
        finally:
            conn.close()

    # ---------------- S11 delete confirm -------------------------------------
    @app.get("/cases/{case_id}/delete/confirm", response_class=HTMLResponse)
    async def case_delete_confirm(request: Request, case_id: str):
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
            if session_row is None:
                if wj.wants_json_request(request):
                    return wj.jerr("Session expired", "auth_expired", 401)
                return _login_redirect()
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if row is None:
                if wj.wants_json_request(request):
                    return wj.jerr("Case not found", "not_found", 404)
                return Response(page("Not found",
                                     '<div class="panel"><div class="error">Case not found.</div></div>',
                                     session_row),
                                status_code=404, media_type="text/html")
            if wj.wants_json_request(request):
                return JSONResponse(wj.delete_confirm_payload(row))
            body = f"""
<div class="panel">
<h1>Delete case <code>{esc(case_id)}</code>?</h1>
<div class="error">This is a HARD delete: the case row, all battery/lineage/
stage results, and every file under <code>var/cases/{esc(case_id)}/</code>
(media, reports, fiction seeds), and recoverable copies in product
backups are removed and restore-verified. Audit entries
survive; spend history keeps the cost rows with a NULL case reference. The
exported report is the durable forensic record — export it first if needed.</div>
<form method="post" action="/cases/{esc(case_id)}/delete">
<input type="hidden" name="csrf_token" value="{esc(session_row['csrf_token'])}">
<button type="submit">Yes, delete permanently</button>
<a class="btn" href="/cases/{esc(case_id)}">Cancel</a>
</form>
</div>"""
            return page("Confirm delete", body, session_row)
        finally:
            conn.close()

    @app.post("/cases/{case_id}/delete")
    async def case_delete(request: Request, case_id: str):
        settings = get_settings()
        conn = _db(request)
        try:
            if wj.wants_json_request(request):
                session_row, _ = _session_for(request, conn)
                if session_row is None:
                    return wj.jerr("Session expired", "auth_expired", 401,
                                   ok=False)
                form = await request.form()
                if not auth.check_csrf(session_row, form.get("csrf_token")):
                    return wj.jerr("CSRF validation failed", "csrf_expired",
                                   403, ok=False)
                row = conn.execute(
                    "SELECT * FROM cases WHERE case_id = ?", (case_id,)
                ).fetchone()
                if row is None:
                    return wj.jerr("Case not found", "not_found", 404,
                                   ok=False)
                if row["status"] == "analyzing":
                    # Spec §3.4 conflict trigger: no deletion mid-analysis.
                    return wj.jerr(
                        "Case is being analyzed; wait for the run to "
                        "finish before deleting", "conflict", 409, ok=False)
                deleted = pipeline.delete_case(conn, case_id, settings)
                if not deleted.get("ok"):
                    return wj.jerr(deleted.get("error") or "Delete failed",
                                   "conflict", 409, ok=False)
                return JSONResponse({"ok": True})
            session_row, err = await _csrf_guarded(request, conn)
            if err:
                return err
            deleted = pipeline.delete_case(conn, case_id, settings)
            if not deleted.get("ok"):
                return Response(
                    page("Delete blocked",
                         f'<div class="panel"><div class="error">{esc(deleted.get("error") or "Delete failed")}</div></div>',
                         session_row), status_code=409, media_type="text/html")
            return RedirectResponse("/cases", status_code=303)
        finally:
            conn.close()

    # ---------------- S6 benchmark -------------------------------------------
    @app.get("/benchmark", response_class=HTMLResponse)
    async def benchmark_page(request: Request):
        settings = get_settings()
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
            if session_row is None:
                if wj.wants_json_request(request):
                    return wj.jerr("Session expired", "auth_expired", 401)
                return _login_redirect()
            if wj.wants_json_request(request):
                return JSONResponse(wj.benchmark_payload(conn, settings))
            row = conn.execute(
                "SELECT * FROM benchmark_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            progress_html = ""
            progress_file = settings.var_dir / "benchmark" / "progress.json"
            if progress_file.exists():
                try:
                    prog = json.loads(progress_file.read_text(encoding="utf-8"))
                    if prog.get("done") and prog.get("error"):
                        progress_html = (
                            '<div class="panel"><div class="error">'
                            f'{esc(prog.get("error"))}</div></div>'
                        )
                    elif not prog.get("done"):
                        progress_html = (
                            '<meta http-equiv="refresh" content="5">'
                            f'<div class="panel">Processing seed case '
                            f'{esc(prog.get("completed", 0))} of '
                            f'{esc(prog.get("total", 0))}…</div>'
                        )
                except Exception:
                    pass
            if row is None:
                latest = "<p>No benchmark run recorded.</p>"
            else:
                recall = {}
                try:
                    recall = json.loads(row["per_category_recall_json"] or "{}")
                except Exception:
                    pass
                recall_rows = "".join(
                    f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>"
                    for k, v in sorted(recall.items())
                )
                latest = f"""
<table>
<tr><th>Run</th><td><code>{esc(row['run_id'])}</code></td></tr>
<tr><th>Mode</th><td>{esc(row['mode'])}</td></tr>
<tr><th>Started</th><td>{esc(row['started_at'])}</td></tr>
<tr><th>Completed</th><td>{esc(row['completed_at'] or 'running…')}</td></tr>
<tr><th>Prereg hash</th><td class="mono">{esc(row['prereg_hash'])}</td></tr>
<tr><th>Validation set</th><td class="mono">{esc(row['validation_set_id'])}</td></tr>
<tr><th>False no-mundane-match rate</th><td>{esc(row['false_no_mundane_match_rate'])}</td></tr>
<tr><th>Insufficient detection rate</th><td>{esc(row['insufficient_detection_rate'])}</td></tr>
<tr><th>Passed preregistration</th><td>{'YES' if row['passed'] else 'NO'}</td></tr>
</table>
<h2>Per-category recall</h2>
<table><thead><tr><th>Category</th><th>Recall</th></tr></thead>
<tbody>{recall_rows}</tbody></table>"""
            body = f"""
{progress_html}
<div class="panel">
<h1>Benchmark &amp; calibration</h1>
{latest}
<form method="post" action="/benchmark/run">
<input type="hidden" name="csrf_token" value="{esc(session_row['csrf_token'])}">
<button type="submit">Run benchmark now</button>
</form>
</div>"""
            return page("Benchmark", body, session_row)
        finally:
            conn.close()

    # A benchmark run executes the full 8-stage pipeline on 10 seed cases —
    # minutes of work. It must never run inside the event loop: S6's
    # meta-refresh progress page ("Processing seed case X of Y…") and
    # /healthz have to keep being served while the run is in flight. The
    # work therefore runs in a worker thread with its OWN SQLite connection
    # (sqlite3 connections cannot cross threads; run_benchmark opens one
    # itself when conn=None).
    _benchmark_lock = threading.Lock()
    _benchmark_state = {"running": False}

    def _benchmark_begin() -> bool:
        with _benchmark_lock:
            if _benchmark_state["running"]:
                return False
            _benchmark_state["running"] = True
            return True

    def _benchmark_end() -> None:
        with _benchmark_lock:
            _benchmark_state["running"] = False

    @app.post("/benchmark/run")
    async def benchmark_run(request: Request):
        from uapvf import benchmark as benchmod

        settings = get_settings()
        bearer = _bearer_ok(request)
        wants = wj.wants_json_request(request)
        conn = _db(request)
        try:
            if bearer:
                pass  # Bearer token already validated above; it wins over
                #      Accept-based negotiation (API clients keep their path
                #      even when they send Accept: application/json).
            elif wants:
                # SPA branch: cookie session + CSRF (never Bearer).
                session_row, _ = _session_for(request, conn)
                if session_row is None:
                    return wj.jerr("Session expired", "auth_expired", 401,
                                   ok=False)
                form = await request.form()
                if not auth.check_csrf(session_row, form.get("csrf_token")):
                    return wj.jerr("CSRF validation failed", "csrf_expired",
                                   403, ok=False)
            else:
                session_row, err = await _csrf_guarded(request, conn)
                if err:
                    return err
        finally:
            conn.close()

        if not _benchmark_begin():
            if bearer or wants:
                return wj.jerr("A benchmark run is already in progress",
                               "conflict", 409, ok=False)
            return Response(page("Conflict",
                                 '<div class="panel"><div class="error">A benchmark run is already in progress.</div></div>'),
                            status_code=409, media_type="text/html")

        progress_file = settings.var_dir / "benchmark" / "progress.json"

        def _write_progress(payload: dict):
            try:
                progress_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = progress_file.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(payload), encoding="utf-8")
                os.replace(tmp, progress_file)
            except Exception:
                pass

        def progress(completed: int, total: int):
            _write_progress({"completed": completed, "total": total,
                             "done": completed >= total})

        if bearer:
            # API callers still wait for the full result, but the event
            # loop stays free so GET /benchmark and /healthz keep serving.
            try:
                result = await asyncio.to_thread(
                    benchmod.run_benchmark, settings, None,
                    progress_cb=progress,
                )
            except Exception as exc:
                return JSONResponse({"detail": f"benchmark failed: {exc}"},
                                    status_code=500)
            finally:
                _benchmark_end()
            return JSONResponse(result)

        # Form path: hand the run to a background thread and redirect
        # immediately — the benchmark page then renders the progress panel
        # via meta-refresh while the run proceeds.
        def _run_benchmark_in_background():
            try:
                benchmod.run_benchmark(settings, None, progress_cb=progress)
            except Exception as exc:
                try:
                    _write_progress(
                        {"completed": 0, "total": 0, "done": True,
                         "error": f"benchmark failed: {exc}"}
                    )
                except Exception:
                    pass
            finally:
                _benchmark_end()

        benchmark_thread = threading.Thread(
            target=_run_benchmark_in_background,
            name="uapvf-benchmark",
            daemon=False,
        )
        app.state.benchmark_thread = benchmark_thread
        benchmark_thread.start()
        if wants:
            # Spec §3.4: 202 with the initial progress object.
            return JSONResponse(
                {"ok": True,
                 "progress": {"completed": 0, "total": 0, "done": False,
                              "error": None}},
                status_code=202)
        return RedirectResponse("/benchmark", status_code=303)

    # ---------------- API spend ----------------------------------------------
    @app.get("/api/v1/spend")
    async def api_spend(request: Request):
        if not _bearer_ok(request):
            return _api_401()
        settings = get_settings()
        conn = _db(request)
        try:
            return JSONResponse(spend.spend_summary(conn, settings))
        finally:
            conn.close()

    # ---------------- root ----------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request):
        conn = _db(request)
        try:
            session_row, _ = _session_for(request, conn)
        finally:
            conn.close()
        # Spec §3.2: build-aware redirect — the SPA is canonical when built,
        # the scaffold paths stay the fallback when it is not.
        spa_built = (_ui_dist() / "index.html").exists()
        if session_row is None:
            return RedirectResponse("/ui/login" if spa_built else "/login",
                                    status_code=302)
        return RedirectResponse("/ui/cases" if spa_built else "/cases",
                                status_code=303)


app = create_app()
