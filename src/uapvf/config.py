"""Runtime configuration (spec §7, §8, FR-016, FR-023).

Settings are read from the process environment and (if present) a `.env`
file in the current working directory, via pydantic-settings. A fresh
``Settings`` object is constructed on every ``get_settings()`` call so tests
and the worker can flip environment variables between operations without a
restart. No secret value is ever logged by this module.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except Exception:  # pragma: no cover - pydantic-settings is a hard dep
    BaseSettings = object  # type: ignore
    SettingsConfigDict = dict  # type: ignore

# FR-023: the pre-flight estimate assumes the slice's expected lineage
# count. The B13 slice runs 2-3 vision lineages (spec §1 deferred table);
# the estimate formula uses 3 with defaults $0.05 x 3 + $0.05 = $0.20
# (live) or $0.01 x 3 + $0.01 = $0.04 (mock).
EXPECTED_LINEAGE_COUNT = 3

# The spec's mode model is exactly two modes: mock fixtures
# (SNEFERU_MOCK=1, FR-018) versus the live Sneferu engine (FR-005). The
# former ``simulation`` runtime was removed (round 4): it silently routed
# to the live engine while stamping forensic records "simulation" — a
# provenance mislabel and an unexpected-spend trap. Selecting it now
# fails closed with a named error instead.
VALID_LINEAGE_RUNTIMES = ("live", "mock")
REMOVED_LINEAGE_RUNTIMES = ("simulation",)


class LineageRuntimeRemovedError(ValueError):
    """An explicitly configured lineage runtime that the spec's two-mode
    model no longer admits (fail-closed, never silently rerouted)."""


# Mode-default per-call costs (USD). Operator env overrides win.
MOCK_COST_LINEAGE_USD = 0.01
MOCK_COST_FICTION_USD = 0.01
LIVE_COST_LINEAGE_USD = 0.05
LIVE_COST_FICTION_USD = 0.05

BENCHMARK_BUYER_REF = "__benchmark_seed__"


class Settings(BaseSettings):
    """All operator-tunable knobs, named exactly as spec §7 requires."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    UAPV_OPERATOR_TOKEN: str = ""
    UAPV_CLI_TOKEN: str = ""
    UAPV_ALLOW_LOCAL_ADMIN: int = 0
    # Compatibility switch for the original Sneferu adapter. Production
    # defaults are fail-closed/live; tests opt into mock fixtures
    # themselves (the spec's two-mode model: SNEFERU_MOCK=1 or the live
    # engine — there is no third runtime).
    SNEFERU_MOCK: int = 0
    ARCHIVE_MIRROR_PATH: Optional[str] = None
    ADSB_SOURCE_URL: Optional[str] = None
    ADSB_SOURCE_API_KEY: Optional[str] = None
    OPENSKY_CLIENT_ID: Optional[str] = None
    OPENSKY_CLIENT_SECRET: Optional[str] = None
    TLE_CATALOG_URL: Optional[str] = None
    UAPV_RESNET50_ONNX: Optional[str] = None
    UAPV_RESNET50_MAPPING: Optional[str] = None
    UAPV_RESNET50_RECEIPT: Optional[str] = None
    UAPV_RESNET50_TRUSTED_PUBLIC_KEY: Optional[str] = None
    UAPV_COST_LINEAGE_USD: Optional[float] = None
    UAPV_COST_FICTION_USD: Optional[float] = None
    UAPV_SPEND_CAP_USD: float = 200.0
    RETENTION_DAYS: int = 365
    UAPV_VAR_DIR: Optional[str] = None
    UAPV_WORKER_THREADS: int = 4
    UAPV_WORKER_POLL_S: float = 0.2
    UAPV_RATE_LIMIT_PER_HOUR: int = 20
    SNEFERU_SDK_URL: str = "http://127.0.0.1:7420"
    UAPV_LINEAGE_RUNTIME: str = "live"
    UAPV_EGRESS_ALLOWLIST: str = ""
    UAPV_TLE_MAX_AGE_DAYS: int = 7
    # Dormant Phase 2 reference tooling (uapvf.references) — informational
    # only since round 5; nothing here gates intake or readiness. The
    # source base deliberately has NO default: an earlier revision shipped
    # a hardcoded LAN host (node2.local), which an unconfigured `fetch`
    # would have hit. The operator must set it explicitly to use the tool.
    UAPV_REFERENCE_MODE: str = "strict"
    UAPV_REFERENCE_SOURCE_BASE: str = ""

    @property
    def mock_mode(self) -> bool:
        return bool(self.SNEFERU_MOCK)

    @property
    def lineage_runtime(self) -> str:
        """Lineage runtime mode, constrained to the spec's two-mode model
        (``live`` engine per FR-005, ``mock`` fixtures per FR-018).
        Unknown values fail closed to ``live`` — the strictest mode, which
        requires all six rigor conditions and blocks when the operator
        certification artifacts are absent. Explicitly selecting a removed
        runtime (``simulation``) raises :class:`LineageRuntimeRemovedError`
        — fail-closed with a named error, never silently rerouted to the
        paid live engine under a stale label."""
        value = str(self.UAPV_LINEAGE_RUNTIME or "").strip().lower()
        if value in REMOVED_LINEAGE_RUNTIMES:
            raise LineageRuntimeRemovedError(
                f"UAPV_LINEAGE_RUNTIME={value!r} selects a removed runtime. "
                "The spec's mode model is exactly two modes: mock fixtures "
                "(SNEFERU_MOCK=1, FR-018) or the live Sneferu engine "
                "(FR-005). Remove UAPV_LINEAGE_RUNTIME from the environment "
                "or set it to 'live'."
            )
        return value if value in VALID_LINEAGE_RUNTIMES else "live"

    @property
    def analysis_mode(self) -> str:
        """Customer-visible mode — exactly the spec's two-mode model
        (S8's ``live|mock`` enum): mock iff SNEFERU_MOCK=1, else live.
        Never echoes the runtime env, so a stale knob can never relabel a
        forensic record."""
        return "mock" if self.mock_mode else "live"

    @property
    def var_dir(self) -> Path:
        if self.UAPV_VAR_DIR:
            return Path(self.UAPV_VAR_DIR)
        return Path.cwd() / "var"

    @property
    def db_path(self) -> Path:
        return self.var_dir / "uapvf.db"

    @property
    def cases_dir(self) -> Path:
        return self.var_dir / "cases"

    @property
    def lineage_cost_usd(self) -> float:
        if self.UAPV_COST_LINEAGE_USD is not None:
            return float(self.UAPV_COST_LINEAGE_USD)
        return MOCK_COST_LINEAGE_USD if self.mock_mode else LIVE_COST_LINEAGE_USD

    @property
    def fiction_cost_usd(self) -> float:
        if self.UAPV_COST_FICTION_USD is not None:
            return float(self.UAPV_COST_FICTION_USD)
        return MOCK_COST_FICTION_USD if self.mock_mode else LIVE_COST_FICTION_USD

    def estimated_case_cost_usd(self) -> float:
        """FR-023: ``configured_lineage_cost × expected_lineage_count +
        configured_fiction_cost``. Upper bound — always includes the fiction
        cost because the verdict is unknown at intake. With defaults:
        $0.05 × 3 + $0.05 = $0.20 (live) / $0.01 × 3 + $0.01 = $0.04 (mock).
        """
        return round(
            self.lineage_cost_usd * EXPECTED_LINEAGE_COUNT
            + self.fiction_cost_usd,
            6,
        )


def get_settings() -> Settings:
    """Construct settings from the *current* environment (cheap, no cache)."""
    return Settings()


def utcnow_iso() -> str:
    """Canonical UTC timestamp used for all persisted values. Fixed-width
    fields keep lexicographic ordering identical to chronological ordering."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_iso(ts: str):
    """Parse an ISO-8601 timestamp (accepts trailing 'Z')."""
    from datetime import datetime, timezone

    if not ts:
        raise ValueError("empty timestamp")
    t = ts.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
