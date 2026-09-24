"""Shared pytest fixtures: isolated var dir seeded with the repo's operator
config files + benchmark seeds, mock mode by default, clean env."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

CONFIG_FILES = [
    "battery_config.yaml",
    "quality_config.json",
    "mock_fixtures.json",
    "fiction_prompt_template.txt",
    "fiction_constraints.json",
]


@pytest.fixture
def var_dir(tmp_path, monkeypatch):
    v = tmp_path / "var"
    v.mkdir()
    for name in CONFIG_FILES:
        shutil.copyfile(ROOT / "var" / name, v / name)
    shutil.copytree(ROOT / "var" / "benchmark", v / "benchmark")
    monkeypatch.setenv("UAPV_VAR_DIR", str(v))
    monkeypatch.setenv("SNEFERU_MOCK", "1")
    monkeypatch.setenv("UAPV_OPERATOR_TOKEN", "test-operator-token")
    # Round-5: the UAPV_REFERENCE_MODE=degraded bypass is gone. The unspec'd
    # strict-by-default reference gate was removed from the intake path, so
    # the suite now runs the SHIPPED default (strict) and proves a spec §7
    # clean install can submit cases without any reference configuration.
    for key in ("UAPV_ARCHIVE_MIRROR_PATH", "ADSB_SOURCE_URL",
                "TLE_CATALOG_URL", "UAPV_MOCK_ADSB_POSITIVE",
                "UAPV_MOCK_ZERO_LINEAGES", "UAPV_MOCK_DRONE",
                "UAPV_MOCK_ARTIFACT", "UAPV_MOCK_CLASSIFICATION",
                "UAPV_MOCK_FICTION_INVALID", "UAPV_MOCK_FICTION_FAIL",
                "UAPV_MOCK_FICTION_UNFIXABLE", "UAPV_MOCK_FICTION_TIMEOUT",
                # Round 5: the suite must exercise the SHIPPED reference
                # defaults (spec §7 clean install), never a host override.
                "UAPV_REFERENCE_MODE", "UAPV_REFERENCE_SOURCE_BASE"):
        monkeypatch.delenv(key, raising=False)
    return v


@pytest.fixture
def settings(var_dir):
    from uapvf.config import get_settings

    return get_settings()


@pytest.fixture
def conn(settings):
    from uapvf import db as dbmod

    dbmod.init_db(settings.var_dir)
    c = dbmod.connect(settings.db_path)
    yield c
    c.close()


def base_fields(**overrides) -> dict:
    fields = {
        "observed_at": "2026-01-15T21:15:00+00:00",
        "latitude": "34.5",
        "longitude": "-118.0",
        "viewing_direction": "90,45",
        "shape": "light",
        "terms_accepted": True,
    }
    fields.update(overrides)
    return fields


FIXTURES = ROOT / "tests" / "fixtures"
SEEDS = ROOT / "var" / "benchmark" / "seed"


@pytest.fixture
def submit(settings, conn):
    """Factory: submit(seed_name, fields) -> {case_id, status, ...}"""
    from uapvf.intake import create_case

    def _submit(seed_name="nmm1.jpg", fields=None, path=None):
        media = path or (SEEDS / seed_name)
        return create_case(media, fields or base_fields(), settings, conn)

    return _submit


@pytest.fixture
def process(settings, conn):
    from uapvf import pipeline

    def _process(case_id):
        return pipeline.process_one_case(case_id, settings=settings, conn=conn)

    return _process


@pytest.fixture
def complete_case(submit, process):
    """Factory returning (case_id, result) for a fully processed case."""

    def _complete(seed_name="nmm1.jpg", fields=None):
        created = submit(seed_name, fields)
        result = process(created["case_id"])
        return created["case_id"], result

    return _complete
