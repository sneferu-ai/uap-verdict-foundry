"""CLI tests (S7): direct-SQLite commands, token possession, backup/restore."""
from __future__ import annotations

import glob
import io
import json
import pathlib
import tarfile

import pytest

from conftest import SEEDS, base_fields

from uapvf import pipeline


@pytest.fixture
def cli_env(var_dir, monkeypatch):
    monkeypatch.setenv("UAPV_CLI_TOKEN", "test-operator-token")
    monkeypatch.setenv("UAPV_WORKER_THREADS", "0")
    return var_dir


def run(*argv):
    from uapvf.cli import main

    try:
        return main(list(argv))
    except SystemExit as e:
        return e.code


def drain(settings):
    from uapvf import db as dbmod, pipeline

    conn = dbmod.connect(settings.db_path)
    try:
        return pipeline.drain_queue(settings, conn)
    finally:
        conn.close()


class TestBasics:
    def test_version(self, cli_env, capsys):
        assert run("version") == 0
        out = json.loads(capsys.readouterr().out)
        assert out["version"]

    def test_db_init(self, cli_env, settings, capsys):
        assert run("db", "init") == 0
        assert settings.db_path.exists()

    def test_db_init_seeds_default_configs_on_clean_install(self, tmp_path,
                                                            monkeypatch):
        """AC-013 / §7 smallest deploy: a wheel install has no repo var/
        tree, so `db init` must materialize the packaged config templates
        (battery_config.yaml above all — without it every case dies at the
        battery stage)."""
        fresh = tmp_path / "fresh_var"
        monkeypatch.setenv("UAPV_VAR_DIR", str(fresh))
        monkeypatch.setenv("UAPV_CLI_TOKEN", "test-operator-token")
        assert run("db", "init") == 0
        for name in ("battery_config.yaml", "quality_config.json",
                     "mock_fixtures.json", "fiction_prompt_template.txt",
                     "fiction_constraints.json"):
            assert (fresh / name).exists(), name
        # The seeded battery config must be loadable by the real loader.
        # Round 4: exactly the four FR-006 MVP categories — drones/birds/
        # weather/meteors/balloons stay excluded so the §1 uncovered-mundane
        # amendment (AC-033) cannot be defeated by battery positives.
        from uapvf.adapters.battery_config_loader import load_battery_config

        cfg = load_battery_config(fresh / "battery_config.yaml")
        assert {c["name"] for c in cfg["categories"]} == {
            "aircraft", "satellites", "lens_artifacts", "astronomical"}
        # Seeding must never clobber existing operator files.
        (fresh / "battery_config.yaml").write_text("version: 99\n")
        assert run("db", "init") == 0
        assert (fresh / "battery_config.yaml").read_text() == "version: 99\n"

    def test_unknown_command_usage_error(self, cli_env):
        assert run() == 2


class TestTokenPossession:
    def test_wrong_cli_token_exits_1(self, cli_env, monkeypatch):
        monkeypatch.setenv("UAPV_CLI_TOKEN", "wrong-token")
        run("db", "init")
        assert run("spend") == 1

    def test_local_admin_bypass(self, cli_env, monkeypatch):
        monkeypatch.setenv("UAPV_CLI_TOKEN", "wrong-token")
        monkeypatch.setenv("UAPV_ALLOW_LOCAL_ADMIN", "1")
        run("db", "init")
        assert run("spend") == 0


class TestCaseCommands:
    def test_submit_status_export_flow(self, cli_env, settings, capsys):
        run("db", "init")
        fields = base_fields()
        fields_path = settings.var_dir / "f.json"
        fields_path.write_text(json.dumps(fields))
        capsys.readouterr()
        assert run("case", "submit", "--media", str(SEEDS / "nmm1.jpg"),
                   "--fields", str(fields_path)) == 0
        created = json.loads(capsys.readouterr().out)
        case_id = created["case_id"]
        drain(settings)
        assert run("case", "status", case_id) == 0
        status_out = json.loads(capsys.readouterr().out)
        assert status_out["status"] == "complete"
        assert len(status_out["stages"]) == len(pipeline.STAGES)
        out_dir = settings.var_dir / "export_out"
        assert run("case", "export", case_id, "--out", str(out_dir)) == 0
        files = {p.name for p in out_dir.iterdir()}
        assert {"report.html", "report.json"} <= files
        export_out = json.loads(capsys.readouterr().out)
        assert export_out["ok"]

    def test_submit_requires_terms_exit_2(self, cli_env, settings, capsys):
        run("db", "init")
        fields = base_fields()
        del fields["terms_accepted"]
        fields_path = settings.var_dir / "f.json"
        fields_path.write_text(json.dumps(fields))
        assert run("case", "submit", "--media", str(SEEDS / "nmm1.jpg"),
                   "--fields", str(fields_path)) == 2

    def test_export_integrity_check(self, cli_env, settings, capsys):
        run("db", "init")
        fields_path = settings.var_dir / "f.json"
        fields_path.write_text(json.dumps(base_fields()))
        capsys.readouterr()
        run("case", "submit", "--media", str(SEEDS / "nmm1.jpg"),
            "--fields", str(fields_path))
        case_id = json.loads(capsys.readouterr().out)["case_id"]
        drain(settings)
        # tamper with the report file
        report = settings.cases_dir / case_id / "report.html"
        report.write_text(report.read_text() + "<!-- tampered -->")
        assert run("case", "export", case_id,
                   "--out", str(settings.var_dir / "o")) == 1

    def test_fiction_command(self, cli_env, settings, capsys):
        run("db", "init")
        fields_path = settings.var_dir / "f.json"
        fields_path.write_text(json.dumps(base_fields()))
        capsys.readouterr()
        run("case", "submit", "--media", str(SEEDS / "nmm1.jpg"),
            "--fields", str(fields_path))
        case_id = json.loads(capsys.readouterr().out)["case_id"]
        drain(settings)
        out_dir = settings.var_dir / "fic_out"
        assert run("case", "fiction", case_id, "--out", str(out_dir)) == 0
        fic_files = list(out_dir.glob("*.fic.md"))
        assert len(fic_files) == 1
        assert "[SPECULATIVE FICTION" in fic_files[0].read_text()

    def test_set_payment(self, cli_env, settings, capsys):
        run("db", "init")
        fields_path = settings.var_dir / "f.json"
        fields_path.write_text(json.dumps(base_fields()))
        capsys.readouterr()
        run("case", "submit", "--media", str(SEEDS / "nmm1.jpg"),
            "--fields", str(fields_path))
        case_id = json.loads(capsys.readouterr().out)["case_id"]
        capsys.readouterr()
        assert run("case", "set-payment", case_id, "--status", "paid") == 0
        out = json.loads(capsys.readouterr().out)
        assert out["new"] == "paid"


class TestAuditSpend:
    def test_audit_and_verify(self, cli_env, settings, capsys):
        run("db", "init")
        fields_path = settings.var_dir / "f.json"
        fields_path.write_text(json.dumps(base_fields()))
        capsys.readouterr()
        run("case", "submit", "--media", str(SEEDS / "nmm1.jpg"),
            "--fields", str(fields_path))
        case_id = json.loads(capsys.readouterr().out)["case_id"]
        assert run("audit", case_id) == 0
        assert run("audit", "verify", case_id) == 0
        assert run("audit", "verify", "--all") == 0

    def test_spend_summary(self, cli_env, settings, capsys):
        run("db", "init")
        fields_path = settings.var_dir / "f.json"
        fields_path.write_text(json.dumps(base_fields()))
        capsys.readouterr()
        run("case", "submit", "--media", str(SEEDS / "nmm1.jpg"),
            "--fields", str(fields_path))
        case_id = json.loads(capsys.readouterr().out)["case_id"]
        drain(settings)
        capsys.readouterr()
        assert run("spend") == 0
        out = json.loads(capsys.readouterr().out)
        assert out["month_total_usd"] > 0


class TestBackupRestore:
    def test_backup_restore_roundtrip(self, cli_env, settings, capsys):
        run("db", "init")
        fields_path = settings.var_dir / "f.json"
        fields_path.write_text(json.dumps(base_fields()))
        capsys.readouterr()
        run("case", "submit", "--media", str(SEEDS / "nmm1.jpg"),
            "--fields", str(fields_path))
        case_id = json.loads(capsys.readouterr().out)["case_id"]
        drain(settings)
        capsys.readouterr()
        assert run("backup") == 0
        tarballs = glob.glob(str(settings.var_dir / "backup" / "*.tar.gz"))
        assert tarballs
        # mutate state, then restore
        from uapvf import db as dbmod

        conn = dbmod.connect(settings.db_path)
        conn.execute("DELETE FROM cases")
        conn.commit()
        conn.close()
        capsys.readouterr()
        assert run("restore", tarballs[0]) == 0
        conn = dbmod.connect(settings.db_path)
        n = conn.execute("SELECT COUNT(*) n FROM cases").fetchone()["n"]
        audit_rows = conn.execute(
            "SELECT action FROM audit_events WHERE action='restore_genesis'"
        ).fetchall()
        conn.close()
        assert n == 1
        assert audit_rows

    def test_restore_rejects_link_members_before_touching_live_db(
        self, cli_env, settings, capsys
    ):
        run("db", "init")
        from uapvf import auth, db as dbmod

        conn = dbmod.connect(settings.db_path)
        auth.create_session(conn)
        conn.close()
        bad = settings.var_dir / "link-backup.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            link = tarfile.TarInfo("cases/escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../outside"
            tar.addfile(link)
            payload = b"owned"
            member = tarfile.TarInfo("cases/escape/payload")
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
        capsys.readouterr()
        assert run("restore", str(bad)) == 1
        assert "invalid backup" in capsys.readouterr().err
        conn = dbmod.connect(settings.db_path)
        assert conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 1
        conn.close()
        assert not (settings.var_dir.parent / "outside" / "payload").exists()

    def test_restore_rejects_corrupt_database_before_touching_live_db(
        self, cli_env, settings, capsys
    ):
        run("db", "init")
        from uapvf import auth, db as dbmod

        conn = dbmod.connect(settings.db_path)
        auth.create_session(conn)
        conn.close()
        bad = settings.var_dir / "corrupt-backup.tar.gz"
        payload = b"not a sqlite database"
        with tarfile.open(bad, "w:gz") as tar:
            member = tarfile.TarInfo("uapvf.db")
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
        capsys.readouterr()
        assert run("restore", str(bad)) == 1
        assert "invalid backup" in capsys.readouterr().err
        conn = dbmod.connect(settings.db_path)
        assert conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 1
        conn.close()


def _write_operator_independence_manifest(var_dir):
    """560-item (multiple of 7 -> kappa == 0 exactly) operator eval set
    meeting the §3.2 pt 1 composition and the pt 7 >=30% hard rule."""
    from uapvf.lineages.registry import declared_lineage_ids
    from uapvf.lineages.taxonomy import TAXONOMY

    ids = declared_lineage_ids()
    cats = sorted(TAXONOMY)[:7]
    plan = [("general", 210), ("l6_enriched", 210),
            ("l4_enriched", 70), ("l7_enriched", 70)]
    items = []
    idx = 0
    for subset, count in plan:
        for _ in range(count):
            difficulty = None
            if subset == "general":
                difficulty = "hard" if idx % 3 == 0 else "easy"
            items.append({
                "item_id": f"{subset}-{idx:04d}",
                "subset": subset,
                "difficulty": difficulty,
                "classifications": {
                    lid: cats[(idx * (k + 1) + k) % 7]
                    for k, lid in enumerate(ids)
                },
            })
            idx += 1
    eval_dir = var_dir / "operator" / "eval_set_independence"
    eval_dir.mkdir(parents=True)
    (eval_dir / "manifest.json").write_text(
        json.dumps({"eval_set": "independence", "items": items})
    )


def _write_operator_calibration_manifest(var_dir):
    """140-item operator calibration eval set: 70 general + 70
    l6-enriched (l6-covered labels), every lineage comparable with a
    deterministic 75% accuracy and raw confidences 0.85/0.35."""
    from uapvf.lineages.eval_set import L6_COVERED_CATEGORIES
    from uapvf.lineages.registry import declared_lineage_ids
    from uapvf.lineages.taxonomy import TAXONOMY

    ids = declared_lineage_ids()
    cats = sorted(TAXONOMY)
    covered = sorted(L6_COVERED_CATEGORIES)
    items = []
    for idx in range(140):
        if idx >= 70:
            label = covered[idx % 3]
            subset = "l6_enriched"
        else:
            label = cats[idx % 10]
            subset = "general"
        cls, confs = {}, {}
        for k, lid in enumerate(ids):
            correct = (idx % 4) != 0
            pred = label if correct else cats[
                (cats.index(label) + 1 + k) % 10
            ]
            cls[lid] = pred
            confs[lid] = 0.85 if correct else 0.35
        items.append({
            "item_id": f"cal-{idx:04d}",
            "subset": subset,
            "label": label,
            "classifications": cls,
            "confidences": confs,
        })
    eval_dir = var_dir / "operator" / "eval_set"
    eval_dir.mkdir(parents=True)
    (eval_dir / "manifest.json").write_text(
        json.dumps({"eval_set": "calibration", "items": items})
    )


class TestLineages:
    def test_lineages_list_prints_seven(self, cli_env, capsys):
        # AC-2: `lineages list` prints 7.
        assert run("lineages", "list") == 0
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 7
        assert {l["lineage_id"] for l in out["lineages"]} == {
            "l1-photometric", "l2-spectral", "l3-geometric", "l4-metadata",
            "l5-trajectory", "l6-resnet50", "l7-audio",
        }

    def test_lineages_validate_synthetic_fallback_fails_closed(
        self, cli_env, settings, capsys
    ):
        # §3.2 pt 8: no operator set -> marked synthetic set exercises the
        # code path; every pair fails the comparable minimum -> exit 1.
        assert run("lineages", "validate") == 1
        out = json.loads(capsys.readouterr().out)
        assert out["all_passed"] is False
        assert out["eval_set_source"] == "synthetic"
        assert out["eval_set_marker"] == (
            "synthetic_ci_not_production_independence"
        )
        assert out["total_items"] == 20
        report_path = settings.var_dir / "lineage_independence_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["pair_count"] == 21
        assert report["all_passed"] is False
        assert report["eval_set"]["marker"] == (
            "synthetic_ci_not_production_independence"
        )

    def test_lineages_validate_operator_set_passes(
        self, cli_env, settings, capsys
    ):
        # AC-2: exits 0 with a valid report over the operator eval set.
        _write_operator_independence_manifest(settings.var_dir)
        assert run("lineages", "validate", "--resamples", "400") == 0
        out = json.loads(capsys.readouterr().out)
        assert out["all_passed"] is True
        assert out["eval_set_source"] == "operator"
        assert out["eval_set_marker"] is None
        assert out["failed_pairs"] == []
        report = json.loads(
            (settings.var_dir / "lineage_independence_report.json").read_text()
        )
        assert report["all_passed"] is True
        assert report["total_items"] == 560
        assert report["difficulty_distribution"]["hard"] == 70

    def test_lineages_validate_bad_manifest_fails_closed(
        self, cli_env, settings, capsys
    ):
        eval_dir = settings.var_dir / "operator" / "eval_set_independence"
        eval_dir.mkdir(parents=True)
        (eval_dir / "manifest.json").write_text('{"items": []}')
        assert run("lineages", "validate") == 1
        assert "error:" in capsys.readouterr().err
        # A malformed operator set must not produce a report.
        assert not (
            settings.var_dir / "lineage_independence_report.json"
        ).exists()

    def test_lineages_calibrate_synthetic_fallback_fails_closed(
        self, cli_env, settings, capsys
    ):
        # No operator calibration set -> marked synthetic fallback
        # exercises the path; 20 items < 80 comparable minimum, so every
        # lineage fails closed and the command exits 1.
        assert run("lineages", "calibrate") == 1
        out = json.loads(capsys.readouterr().out)
        assert out["all_calibrated"] is False
        assert out["eval_set_source"] == "synthetic"
        assert out["eval_set_marker"] == (
            "synthetic_ci_not_production_calibration"
        )
        assert out["total_items"] == 20
        assert len(out["failed_lineages"]) == 7
        # Parameters are still stored (marked synthetic — the rigor gate
        # rejects the marker, mirroring the independence report).
        cal_dir = settings.var_dir / "lineage_calibration"
        record = json.loads(
            (cal_dir / "l1-photometric.json").read_text()
        )
        assert record["calibrated"] is False
        assert record["reason"] == "insufficient_comparable_for_calibration"
        assert record["eval_set"]["marker"] == (
            "synthetic_ci_not_production_calibration"
        )

    def test_lineages_calibrate_operator_set_passes(
        self, cli_env, settings, capsys
    ):
        # OBL-21: exits 0, stores var/lineage_calibration/{id}.json for
        # all 7 lineages, each certified at accuracy >= 0.60.
        _write_operator_calibration_manifest(settings.var_dir)
        assert run("lineages", "calibrate") == 0
        out = json.loads(capsys.readouterr().out)
        assert out["all_calibrated"] is True
        assert out["eval_set_source"] == "operator"
        assert out["eval_set_marker"] is None
        assert out["failed_lineages"] == []
        cal_dir = settings.var_dir / "lineage_calibration"
        files = sorted(p.name for p in cal_dir.iterdir())
        assert len(files) == 7
        for name in files:
            record = json.loads((cal_dir / name).read_text())
            assert record["calibrated"] is True
            assert record["accuracy"] >= 0.6
            assert record["comparable_count"] >= 80
            assert record["eval_set"]["source"] == "operator"
        # l6 gets Platt scaling, everyone else isotonic (spec §3.2).
        l6 = json.loads((cal_dir / "l6-resnet50.json").read_text())
        assert l6["method"] == "platt_temperature"
        assert l6["parameters"]["temperature"] > 0
        l1 = json.loads((cal_dir / "l1-photometric.json").read_text())
        assert l1["method"] == "isotonic"
        assert l1["parameters"]["isotonic"]

    def test_lineages_calibrate_bad_manifest_fails_closed(
        self, cli_env, settings, capsys
    ):
        eval_dir = settings.var_dir / "operator" / "eval_set"
        eval_dir.mkdir(parents=True)
        (eval_dir / "manifest.json").write_text('{"items": []}')
        assert run("lineages", "calibrate") == 1
        assert "error:" in capsys.readouterr().err
        # A malformed operator set must not produce parameter files.
        assert not (settings.var_dir / "lineage_calibration").exists()


class TestDeferredCliSurfaces:
    """Spec §1 deferred table: the TTL retention job (spec.md:86) and the
    canon graph (spec.md:82) are not MVP CLI surfaces; TLE refresh is a
    MANUAL operator action (A-012, spec.md:87)."""

    def test_retention_run_removed(self, cli_env):
        assert run("retention", "run") == 2  # argparse usage error

    def test_retention_hold_release_still_present(self, cli_env, settings,
                                                  capsys):
        from uapvf import db as dbmod
        from uapvf.intake import create_case

        dbmod.init_db(settings.var_dir)
        conn = dbmod.connect(settings.db_path)
        try:
            created = create_case(SEEDS / "nmm1.jpg", base_fields(),
                                  settings, conn)
        finally:
            conn.close()
        assert run("retention", "hold", created["case_id"],
                   "--reason", "litigation") == 0
        assert json.loads(capsys.readouterr().out)["held"] is True
        assert run("retention", "release", created["case_id"]) == 0
        assert json.loads(capsys.readouterr().out)["held"] is False

    def test_canon_command_removed(self, cli_env):
        assert run("canon", "status") == 2

    def test_tle_refresh_without_url_is_operational_error(self, cli_env,
                                                          settings,
                                                          capsys):
        assert run("db", "init") == 0
        assert run("tle", "refresh") == 1
        assert "TLE_CATALOG_URL" in capsys.readouterr().err
