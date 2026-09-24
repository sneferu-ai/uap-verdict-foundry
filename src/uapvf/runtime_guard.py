"""Startup import guard (fail-closed module integrity check).

``cli.py`` and ``server.py`` call ``install_import_guard()`` as the very
first executable statement. It eagerly imports every product module so a
missing or broken module fails the process at startup with a precise,
auditable error — instead of surfacing as a 500 on the first request or a
NameError halfway through a case.

The guard never swallows an import error: it re-raises an ``ImportError``
naming the exact module, preserving the original exception as the cause.
Lazy third-party dependencies (skyfield, onnxruntime, filetype) are NOT
imported here — those are runtime-optional and guarded at their own call
sites.
"""
from __future__ import annotations

import importlib

# Every module that constitutes the runnable MVP product (spec §6). Order
# is dependency-light first so the first failure names the deepest missing
# piece. Phase-2/deferred subsystems are deliberately NOT startup-mandatory
# (spec §1 deferred table): media_sandbox lineage isolation (spec.md:85 —
# the module itself still ships the case-scoped intake decode path, which
# uapvf.intake imports directly), retention auto-deletion
# (spec.md:86 — uapvf.retention now holds only the FR-021 manual-delete
# primitive, imported lazily by the delete call sites), automatic TLE
# refresh (spec.md:87 — uapvf.tle catalog consumption is imported lazily
# by the satellite adapter), canon (spec.md:82 — not in the MVP at all),
# signed-model + operator-eval certification tooling, and the lineage
# certification/execution support modules (CLI/subprocess-only).
GUARDED_MODULES = (
    "uapvf.config",
    "uapvf.db",
    "uapvf.audit",
    "uapvf.auth",
    "uapvf.spend",
    "uapvf.egress",
    "uapvf.media_check",
    "uapvf.quality_gate",
    "uapvf.references",
    "uapvf.intake",
    "uapvf.adapters",
    "uapvf.adapters.battery_config_loader",
    "uapvf.adapters.geo",
    "uapvf.adapters.adsb_adapter",
    "uapvf.adapters.satellite_adapter",
    "uapvf.adapters.archive_adapter",
    "uapvf.adapters.lineage_adapter",
    "uapvf.adapters.taxonomy_adapter",
    "uapvf.adapters.weather_adapter",
    "uapvf.adapters.sneferu_adapter",
    "uapvf.battery",
    "uapvf.verdict",
    "uapvf.rigor",
    "uapvf.lineages",
    "uapvf.lineages.protocol",
    "uapvf.lineages.registry",
    "uapvf.lineages.taxonomy",
    "uapvf.render",
    "uapvf.render.fiction",
    "uapvf.render.html_render",
    "uapvf.render.report_json",
    "uapvf.render.linter",
    "uapvf.benchmark",
    "uapvf.pipeline",
    "uapvf.web_json",
    "uapvf.server",
)

_installed = False


def install_import_guard() -> None:
    """Eagerly import all guarded modules; raise ImportError naming the
    first module that fails. Idempotent — repeated calls are cheap."""
    global _installed
    if _installed:
        return
    for name in GUARDED_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            raise ImportError(f"import error in {name}: {exc}") from exc
    _installed = True
