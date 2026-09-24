"""Operator CLI `uapvf` (spec S7).

Execution model: `db init`, `version`, `--help` need no server and no token.
All case/benchmark-results/audit/spend/backup/restore commands operate
directly on SQLite + filesystem and require UAPV_OPERATOR_TOKEN possession
(UAPV_CLI_TOKEN env or interactive prompt) unless UAPV_ALLOW_LOCAL_ADMIN=1
(loopback bypass, audit-warned). `benchmark run` requires the running
server. `case submit` performs full intake validation before any row exists.

Exit codes: 0 success, 1 operational error, 2 usage error.
"""
from __future__ import annotations

from uapvf.runtime_guard import install_import_guard

install_import_guard()

import argparse
import getpass
import json
import os
import shutil
import signal
import sqlite3
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

from uapvf import __version__, audit, spend
from uapvf.config import Settings, get_settings, utcnow_iso


# ---------------------------------------------------------------------------
# Token possession check (S7: local comparison, not a server round-trip)
# ---------------------------------------------------------------------------

def require_token(settings: Settings, conn=None) -> None:
    import hmac as hmac_mod

    if int(settings.UAPV_ALLOW_LOCAL_ADMIN or 0) == 1:
        if conn is not None:
            try:
                audit.append_event(conn, None, "operator", "local_admin_bypass",
                                   {"command": sys.argv[1] if len(sys.argv) > 1 else ""})
            except Exception:
                pass
        return
    if not settings.UAPV_OPERATOR_TOKEN:
        print("error: UAPV_OPERATOR_TOKEN is not configured", file=sys.stderr)
        raise SystemExit(1)
    provided = settings.UAPV_CLI_TOKEN
    if not provided and sys.stdin.isatty():
        try:
            provided = getpass.getpass("operator token: ")
        except Exception:
            provided = ""
    if not provided or not hmac_mod.compare_digest(
        provided, settings.UAPV_OPERATOR_TOKEN
    ):
        print("error: operator token mismatch", file=sys.stderr)
        raise SystemExit(1)


def _connect(settings: Settings) -> sqlite3.Connection:
    from uapvf import db as dbmod

    if not settings.db_path.exists():
        print("error: database not initialized (run `uapvf db init`)",
              file=sys.stderr)
        raise SystemExit(1)
    return dbmod.connect(settings.db_path)


def _case_row_or_exit(conn, case_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        print(f"error: case not found: {case_id}", file=sys.stderr)
        raise SystemExit(1)
    return row


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_version(_args, settings) -> int:
    print(json.dumps({"name": "uapvf", "version": __version__}))
    return 0


def cmd_db_init(_args, settings) -> int:
    from uapvf import db as dbmod

    path = dbmod.init_db(settings.var_dir)
    print(json.dumps({"ok": True, "db_path": str(path)}))
    return 0


def cmd_serve(args, settings) -> int:
    import uvicorn

    host = "127.0.0.1" if args.dev else (args.host or "127.0.0.1")
    port = int(args.port or 8470)
    log_level = "debug" if args.dev else "info"
    if args.dev:
        uvicorn.run("uapvf.server:app", host=host, port=port, reload=True,
                    log_level=log_level, access_log=False)
    else:
        uvicorn.run("uapvf.server:app", host=host, port=port, reload=False,
                    log_level=log_level, access_log=False)
    return 0


def cmd_case_submit(args, settings) -> int:
    from uapvf import db as dbmod
    from uapvf.intake import IntakeError, create_case

    dbmod.init_db(settings.var_dir)
    conn = _connect(settings)
    try:
        require_token(settings, conn)
        try:
            fields = json.loads(Path(args.fields).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"error: cannot read fields file: {exc}", file=sys.stderr)
            return 2
        terms = fields.get("terms_accepted")
        if not (terms is True or str(terms).lower() == "true"):
            print("error: terms acceptance required", file=sys.stderr)
            return 2
        if args.buyer_ref:
            fields["buyer_ref"] = args.buyer_ref
        if args.payment_status:
            fields["payment_status"] = args.payment_status
        try:
            result = create_case(Path(args.media), fields, settings, conn)
        except IntakeError as exc:
            print(f"error: {exc.message}", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        conn.close()


def cmd_case_status(args, settings) -> int:
    conn = _connect(settings)
    try:
        require_token(settings, conn)
        row = _case_row_or_exit(conn, args.case_id)
        stages = conn.execute(
            "SELECT stage, status, started_at, completed_at, error_detail,"
            " run_version FROM pipeline_stage_runs WHERE case_id = ?"
            " ORDER BY run_version, run_id",
            (args.case_id,),
        ).fetchall()
        out = {k: row[k] for k in row.keys()}
        out["stages"] = [dict(s) for s in stages]
        print(json.dumps(out, sort_keys=True, default=str))
        return 0
    finally:
        conn.close()


def cmd_case_export(args, settings) -> int:
    conn = _connect(settings)
    try:
        require_token(settings, conn)
        row = _case_row_or_exit(conn, args.case_id)
        if row["status"] != "complete":
            print("error: case is not complete", file=sys.stderr)
            return 1
        out_dir = Path(args.out or ".").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        case_dir = settings.cases_dir / args.case_id
        files = []
        for name, stored_sha in (
            ("report.html", row["report_sha256"]),
            ("report.json", row["report_json_sha256"]),
        ):
            src = case_dir / name
            if not src.exists():
                print(f"error: file integrity check failed: {name}", file=sys.stderr)
                return 1
            if _sha256(src) != stored_sha:
                print(f"error: file integrity check failed: {name}", file=sys.stderr)
                return 1
            files.append(src)
        if row["fiction_path"]:
            fic = Path(row["fiction_path"])
            if fic.exists():
                if _sha256(fic) != row["fiction_sha256"]:
                    print(f"error: file integrity check failed: {fic.name}",
                          file=sys.stderr)
                    return 1
                files.append(fic)
        for src in files:
            shutil.copyfile(src, out_dir / src.name)
        audit.append_event(conn, args.case_id, "operator", "case_export",
                           {"files": [f.name for f in files]})
        print(json.dumps({"ok": True, "files": [str(out_dir / f.name) for f in files]}))
        return 0
    finally:
        conn.close()


def cmd_case_fiction(args, settings) -> int:
    conn = _connect(settings)
    try:
        require_token(settings, conn)
        row = _case_row_or_exit(conn, args.case_id)
        if (
            row["status"] != "complete"
            or row["verdict"] == "mundane_identified"
            or not row["fiction_path"]
            or not Path(row["fiction_path"]).exists()
        ):
            print("error: no fiction seed for this case", file=sys.stderr)
            return 1
        out_dir = Path(args.out or ".").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        src = Path(row["fiction_path"])
        if _sha256(src) != row["fiction_sha256"]:
            print(f"error: file integrity check failed: {src.name}", file=sys.stderr)
            return 1
        shutil.copyfile(src, out_dir / src.name)
        print(json.dumps({"ok": True, "file": str(out_dir / src.name)}))
        return 0
    finally:
        conn.close()


def cmd_case_retry(args, settings) -> int:
    from uapvf import pipeline

    conn = _connect(settings)
    try:
        require_token(settings, conn)
        _case_row_or_exit(conn, args.case_id)
        result = pipeline.retry_case(conn, args.case_id, force=args.force)
        if not result.get("ok"):
            print(f"error: {result['error']}", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        conn.close()


def cmd_case_rerun(args, settings) -> int:
    from uapvf import pipeline

    conn = _connect(settings)
    try:
        require_token(settings, conn)
        _case_row_or_exit(conn, args.case_id)
        result = pipeline.rerun_case(conn, args.case_id)
        if not result.get("ok"):
            print(f"error: {result['error']}", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        conn.close()


def cmd_case_delete(args, settings) -> int:
    from uapvf import pipeline

    conn = _connect(settings)
    try:
        require_token(settings, conn)
        _case_row_or_exit(conn, args.case_id)
        result = pipeline.delete_case(conn, args.case_id, settings)
        if not result.get("ok"):
            print(f"error: {result['error']}", file=sys.stderr)
            return 1
        print(json.dumps({"ok": True, "deleted": args.case_id}))
        return 0
    finally:
        conn.close()


def cmd_case_set_payment(args, settings) -> int:
    from uapvf import pipeline

    conn = _connect(settings)
    try:
        require_token(settings, conn)
        _case_row_or_exit(conn, args.case_id)
        result = pipeline.set_payment(conn, args.case_id, args.status)
        if not result.get("ok"):
            print(f"error: {result['error']}", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        conn.close()


def cmd_benchmark_prereg(_args, settings) -> int:
    from uapvf import benchmark

    conn = _connect(settings)
    try:
        require_token(settings, conn)
        try:
            prereg = benchmark.run_prereg(settings, conn)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(prereg, sort_keys=True))
        return 0
    finally:
        conn.close()


def cmd_benchmark_run(_args, settings) -> int:
    import httpx

    base = os.environ.get("UAPV_SERVER_URL", "http://127.0.0.1:8470")
    headers = {"Authorization": f"Bearer {settings.UAPV_CLI_TOKEN or settings.UAPV_OPERATOR_TOKEN}"}
    try:
        resp = httpx.post(base + "/benchmark/run", headers=headers, timeout=3600)
    except Exception as exc:
        print(f"error: benchmark run requires the server to be running ({exc})",
              file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(f"error: benchmark run failed: {resp.status_code} {resp.text[:300]}",
              file=sys.stderr)
        return 1
    print(resp.text)
    return 0


def cmd_benchmark_results(_args, settings) -> int:
    conn = _connect(settings)
    try:
        require_token(settings, conn)
        row = conn.execute(
            "SELECT * FROM benchmark_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print(json.dumps({"ok": True, "run": None,
                              "message": "No benchmark run recorded."}))
            return 0
        out = {k: row[k] for k in row.keys()}
        cal_path = settings.var_dir / "benchmark" / "calibration.json"
        if cal_path.exists():
            try:
                out["calibration"] = json.loads(cal_path.read_text(encoding="utf-8"))
            except Exception:
                out["calibration"] = None
        print(json.dumps(out, sort_keys=True, default=str))
        return 0
    finally:
        conn.close()


def cmd_audit(args, settings) -> int:
    conn = _connect(settings)
    try:
        require_token(settings, conn)
        events = audit.events_for_case(conn, args.case_id)
        print(json.dumps({"case_id": args.case_id, "events": events},
                         sort_keys=True, default=str))
        return 0
    finally:
        conn.close()


def cmd_audit_verify(args, settings) -> int:
    conn = _connect(settings)
    try:
        require_token(settings, conn)
        case_id = None if args.all else args.case_id
        result = audit.verify_chain(conn, case_id)
        print(json.dumps(result, sort_keys=True, default=str))
        return 0 if result["ok"] else 1
    finally:
        conn.close()


def cmd_spend(_args, settings) -> int:
    conn = _connect(settings)
    try:
        require_token(settings, conn)
        summary = spend.spend_summary(conn, settings)
        print(json.dumps(summary, sort_keys=True))
        return 0
    finally:
        conn.close()


def cmd_backup(_args, settings) -> int:
    from uapvf import db as dbmod

    conn = _connect(settings)
    try:
        require_token(settings, conn)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        backup_dir = settings.var_dir / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        staging = backup_dir / f".staging_{stamp}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        # Consistent SQLite snapshot via the online backup API.
        snap_path = staging / "uapvf.db"
        dst = sqlite3.connect(str(snap_path))
        try:
            conn.backup(dst)
        finally:
            dst.close()
        analyzing = {
            r["case_id"]
            for r in conn.execute(
                "SELECT case_id FROM cases WHERE status = 'analyzing'"
            ).fetchall()
        }
        cases_out = staging / "cases"
        if settings.cases_dir.exists():
            for child in settings.cases_dir.iterdir():
                if child.name in analyzing:
                    continue  # exclude in-flight case directories
                if child.is_dir():
                    shutil.copytree(child, cases_out / child.name)
        for name in ("battery_config.yaml", "quality_config.json",
                     "fiction_prompt_template.txt", "fiction_constraints.json",
                     "mock_fixtures.json", "evidence_vocabulary.json",
                     "refutation_rules.json", "rigor_config.json"):
            src = settings.var_dir / name
            if src.exists():
                shutil.copyfile(src, staging / name)
        bench_src = settings.var_dir / "benchmark"
        if bench_src.exists():
            bench_out = staging / "benchmark"
            bench_out.mkdir(exist_ok=True)
            for item in bench_src.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(bench_src)
                    target = bench_out / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, target)
        tar_path = backup_dir / f"uapvf_backup_{stamp}.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            for item in staging.iterdir():
                tar.add(item, arcname=item.name)
        shutil.rmtree(staging)
        audit.append_event(conn, None, "operator", "backup_created",
                           {"path": str(tar_path)})
        print(json.dumps({"ok": True, "backup": str(tar_path)}))
        return 0
    finally:
        conn.close()


def cmd_restore(args, settings) -> int:
    from uapvf import db as dbmod

    backup_path = Path(args.path)
    if not backup_path.exists():
        print("error: backup file not found", file=sys.stderr)
        return 1
    conn = _connect(settings)
    try:
        require_token(settings, conn)
    finally:
        conn.close()

    # Validate and extract away from the live data directory.  In particular,
    # tar links must never be materialized: a link followed by a regular file
    # can otherwise write outside var/ even when every member name is clean.
    try:
        staging_parent = settings.var_dir.parent
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging_ctx = tempfile.TemporaryDirectory(
            prefix=".uapvf-restore-", dir=str(staging_parent)
        )
        staging = Path(staging_ctx.name)
        with tarfile.open(backup_path, "r:gz") as tar:
            members = tar.getmembers()
            seen: set[Path] = set()
            for member in members:
                rel = Path(member.name)
                if (
                    not member.name
                    or rel.is_absolute()
                    or ".." in rel.parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise ValueError("backup contains unsafe archive member")
                target = (staging / rel).resolve()
                try:
                    normalized = target.relative_to(staging.resolve())
                except ValueError as exc:
                    raise ValueError("backup contains unsafe path") from exc
                if normalized in seen:
                    raise ValueError("backup contains duplicate path")
                seen.add(normalized)
            tar.extractall(staging, members=members)

        staged_db = staging / "uapvf.db"
        if not staged_db.is_file():
            raise ValueError("backup is missing uapvf.db")
        validation = sqlite3.connect(str(staged_db))
        try:
            validation.execute("PRAGMA query_only=ON")
            checks = validation.execute("PRAGMA quick_check").fetchall()
            if checks != [("ok",)]:
                raise ValueError("backup database failed SQLite integrity check")
            required_tables = {
                "cases",
                "audit_events",
                "pipeline_stage_runs",
                "spend_entries",
                "sessions",
            }
            present = {
                row[0]
                for row in validation.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing = sorted(required_tables - present)
            if missing:
                raise ValueError(
                    "backup database is missing required tables: "
                    + ", ".join(missing)
                )
        finally:
            validation.close()
    except (OSError, sqlite3.Error, tarfile.TarError, ValueError) as exc:
        if "staging_ctx" in locals():
            staging_ctx.cleanup()
        print(f"error: invalid backup: {exc}", file=sys.stderr)
        return 1

    # Stop a running server (SIGTERM, wait up to 30 s).
    pid_file = settings.var_dir / "server.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                    time.sleep(0.5)
                except OSError:
                    break
        except Exception:
            pass

    # Copy non-database payloads first, then atomically install the validated
    # database.  Old WAL/SHM files belong to the replaced database and must not
    # be replayed against the restored snapshot.
    try:
        for item in staging.iterdir():
            if item.name == "uapvf.db":
                continue
            target = settings.var_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        for suffix in ("-wal", "-shm"):
            (Path(str(settings.db_path) + suffix)).unlink(missing_ok=True)
        incoming_db = settings.var_dir / ".uapvf.db.restore"
        shutil.copy2(staged_db, incoming_db)
        os.replace(incoming_db, settings.db_path)
    except OSError as exc:
        print(f"error: restore failed: {exc}", file=sys.stderr)
        return 1
    finally:
        staging_ctx.cleanup()

    conn = dbmod.connect(settings.db_path)
    try:
        # Reconcile analyzing cases whose artifacts were excluded from backup.
        rows = conn.execute(
            "SELECT case_id, media_path FROM cases WHERE status = 'analyzing'"
        ).fetchall()
        for r in rows:
            missing = not r["media_path"] or not Path(r["media_path"]).exists()
            if missing:
                conn.execute(
                    "UPDATE cases SET status = 'failed', error_detail = ?,"
                    " updated_at = ? WHERE case_id = ?",
                    (
                        "restored from backup with missing artifacts — "
                        "re-submit or retry",
                        utcnow_iso(),
                        r["case_id"],
                    ),
                )
                conn.commit()
                audit.append_event(conn, r["case_id"], "system",
                                   "restore_reconcile_failed",
                                   {"reason": "missing artifacts"})
            else:
                conn.execute(
                    "UPDATE cases SET status = 'queued', updated_at = ?"
                    " WHERE case_id = ?",
                    (utcnow_iso(), r["case_id"]),
                )
                conn.commit()
        # New genesis continuation entry: prev_hash = restored chain tail.
        audit.append_event(conn, None, "system", "restore_genesis",
                           {"backup": str(backup_path)})
        print(json.dumps({"ok": True, "restored_from": str(backup_path)}))
        return 0
    finally:
        conn.close()


def cmd_lineages_list(_args, settings) -> int:
    from uapvf.lineages.registry import load_registry

    entries = load_registry()
    out = {
        "count": len(entries),
        "lineages": [
            {
                "lineage_id": e.lineage_id,
                "modality": e.declaration["modality"],
                "architecture": e.declaration["architecture"],
                "available": e.available,
                "unavailable_reason": e.unavailable_reason,
            }
            for e in entries
        ],
    }
    print(json.dumps(out, sort_keys=True))
    return 0


def cmd_lineages_validate(args, settings) -> int:
    """OBL-21 / AC-2: run the independence validation over the operator
    eval set (marked synthetic CI fallback when absent), store the report
    at var/lineage_independence_report.json, exit 1 if any pair fails."""
    from uapvf.lineages.eval_set import EvalSetError, load_independence_eval_set
    from uapvf.lineages.independence import validate_independence
    from uapvf.lineages.registry import load_registry

    try:
        eval_set = load_independence_eval_set(settings.var_dir)
    except EvalSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    registry = load_registry()
    kwargs = {}
    if args.resamples is not None:
        kwargs["n_resamples"] = args.resamples
    if args.seed is not None:
        kwargs["seed"] = args.seed
    report = validate_independence(registry, eval_set, **kwargs)
    report_path = settings.var_dir / "lineage_independence_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary = {
        "all_passed": report["all_passed"],
        "eval_set_marker": report["eval_set"]["marker"],
        "eval_set_source": report["eval_set"]["source"],
        "failed_pairs": report["failed_pairs"],
        "pair_count": report["pair_count"],
        "report_path": str(report_path),
        "total_items": report["total_items"],
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if report["all_passed"] else 1


def cmd_lineages_calibrate(_args, settings) -> int:
    """OBL-21: run calibration for each lineage over the operator
    calibration eval set (marked synthetic CI fallback when absent),
    store parameters at var/lineage_calibration/{lineage_id}.json, exit
    1 if any lineage fails (spec §3.2 CLI)."""
    from uapvf.lineages.calibration import calibrate_lineages
    from uapvf.lineages.eval_set import EvalSetError, load_calibration_eval_set
    from uapvf.lineages.registry import load_registry
    from uapvf.rigor import CALIBRATION_DIR_NAME

    try:
        eval_set = load_calibration_eval_set(settings.var_dir)
    except EvalSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    registry = load_registry()
    results = calibrate_lineages(registry, eval_set)
    out_dir = settings.var_dir / CALIBRATION_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    for lineage_id, result in results.items():
        (out_dir / f"{lineage_id}.json").write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    summary = {
        "all_calibrated": all(r.calibrated for r in results.values()),
        "eval_set_marker": eval_set.marker,
        "eval_set_source": eval_set.source,
        "failed_lineages": sorted(
            lid for lid, r in results.items() if not r.calibrated
        ),
        "lineage_count": len(results),
        "output_dir": str(out_dir),
        "total_items": eval_set.total_items,
        "unavailable_implementations": [
            e.lineage_id for e in registry if not e.available
        ],
        "validation_set_hash": eval_set.validation_set_hash,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["all_calibrated"] else 1


def cmd_lineages_record_eval(args, settings) -> int:
    from uapvf.operator_eval import OperatorEvalError, record_operator_eval

    require_token(settings)
    try:
        result = record_operator_eval(args.manifest, settings)
    except OperatorEvalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_model_install_resnet50(args, settings) -> int:
    """Verify supplier bytes, install them, and create an operator signature."""
    from uapvf.model_weights import ModelArtifactError, install_resnet50

    require_token(settings)
    try:
        result = install_resnet50(args.file, settings)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_model_verify_resnet50(_args, settings) -> int:
    from uapvf.model_weights import ModelArtifactError, verify_resnet50

    require_token(settings)
    required = {
        "weights": settings.UAPV_RESNET50_ONNX,
        "mapping": settings.UAPV_RESNET50_MAPPING,
        "receipt": settings.UAPV_RESNET50_RECEIPT,
        "trusted_public_key": settings.UAPV_RESNET50_TRUSTED_PUBLIC_KEY,
    }
    if any(not value for value in required.values()):
        print("error: all UAPV_RESNET50_* settings are required", file=sys.stderr)
        return 1
    try:
        result = verify_resnet50(**required)
    except ModelArtifactError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_references_status(_args, settings) -> int:
    """Show whether every frozen Phase 2 reference is locally verified."""
    from uapvf.references import resolve

    result = resolve(settings)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["valid"] else 1


def cmd_references_fetch(_args, settings) -> int:
    """Fetch the immutable controlling sources and verify hashes/excerpts."""
    from uapvf.references import ReferenceGateError, fetch

    try:
        result = fetch(settings)
    except ReferenceGateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def cmd_retention_hold(args, settings) -> int:
    from uapvf.retention import set_legal_hold

    conn = _connect(settings)
    try:
        require_token(settings, conn)
        _case_row_or_exit(conn, args.case_id)
        try:
            set_legal_hold(conn, args.case_id, args.reason)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"ok": True, "case_id": args.case_id, "held": True}))
        return 0
    finally:
        conn.close()


def cmd_retention_release(args, settings) -> int:
    from uapvf.retention import release_legal_hold

    conn = _connect(settings)
    try:
        require_token(settings, conn)
        _case_row_or_exit(conn, args.case_id)
        release_legal_hold(conn, args.case_id)
        print(json.dumps({"ok": True, "case_id": args.case_id, "held": False}))
        return 0
    finally:
        conn.close()


def cmd_tle_refresh(_args, settings) -> int:
    """A-012: ONE operator-initiated catalog fetch (manual refresh — the
    automatic refresh scheduler is deferred to phase 2)."""
    from uapvf.tle import refresh_catalog

    conn = _connect(settings)
    try:
        require_token(settings, conn)
        catalog = refresh_catalog(conn, settings)
        if catalog is None:
            print("error: no catalog fetched — check TLE_CATALOG_URL and "
                  "network reachability", file=sys.stderr)
            return 1
        print(json.dumps({"ok": True, "refreshed": True,
                          "catalog": catalog}, sort_keys=True))
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uapvf",
        description="UAP Verdict Foundry — autonomous UAP media analysis "
                    "with defensible tri-state verdicts.",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("serve", help="run the operator server + worker pool")
    p.add_argument("--dev", action="store_true",
                   help="auto-reload, DEBUG logging, 127.0.0.1 only")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8470)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("db", help="database operations")
    dbsub = p.add_subparsers(dest="db_command")
    dbsub.add_parser("init", help="create the SQLite schema").set_defaults(
        func=cmd_db_init)

    p = sub.add_parser("case", help="case operations")
    csub = p.add_subparsers(dest="case_command")

    sp = csub.add_parser("submit", help="validate + queue a new case")
    sp.add_argument("--media", required=True)
    sp.add_argument("--fields", required=True)
    sp.add_argument("--buyer-ref", dest="buyer_ref")
    sp.add_argument("--payment-status", dest="payment_status",
                    choices=["unpaid", "paid", "comped"])
    sp.set_defaults(func=cmd_case_submit)

    sp = csub.add_parser("status", help="show case state + stage history")
    sp.add_argument("case_id")
    sp.set_defaults(func=cmd_case_status)

    sp = csub.add_parser("export", help="export report + fiction with hash check")
    sp.add_argument("case_id")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_case_export)

    sp = csub.add_parser("fiction", help="export the fiction seed only")
    sp.add_argument("case_id")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_case_fiction)

    sp = csub.add_parser("retry", help="requeue a failed case (same run_version)")
    sp.add_argument("case_id")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_case_retry)

    sp = csub.add_parser("rerun", help="re-execute a complete case from scratch")
    sp.add_argument("case_id")
    sp.set_defaults(func=cmd_case_rerun)

    sp = csub.add_parser("delete", help="hard-delete a case and its files")
    sp.add_argument("case_id")
    sp.set_defaults(func=cmd_case_delete)

    sp = csub.add_parser("set-payment", help="update payment status")
    sp.add_argument("case_id")
    sp.add_argument("--status", required=True,
                    choices=["unpaid", "paid", "comped"])
    sp.set_defaults(func=cmd_case_set_payment)

    p = sub.add_parser("benchmark", help="benchmark + calibration")
    bsub = p.add_subparsers(dest="benchmark_command")
    bsub.add_parser("prereg", help="hash the preregistration thresholds").set_defaults(
        func=cmd_benchmark_prereg)
    bsub.add_parser("run", help="run the benchmark via the server").set_defaults(
        func=cmd_benchmark_run)
    bsub.add_parser("results", help="show the latest benchmark results").set_defaults(
        func=cmd_benchmark_results)

    p = sub.add_parser("lineages", help="lineage registry + certification")
    lsub = p.add_subparsers(dest="lineages_command")
    lsub.add_parser(
        "list", help="print the seven declared lineages + availability"
    ).set_defaults(func=cmd_lineages_list)
    sp = lsub.add_parser(
        "validate",
        help="run independence validation over the operator eval set "
             "(OBL-21); writes var/lineage_independence_report.json",
    )
    sp.add_argument("--resamples", type=int, default=None,
                    help="bootstrap resamples per pair (default 10000 per "
                         "spec §3.2 pt 4)")
    sp.add_argument("--seed", type=int, default=None,
                    help="bootstrap RNG seed (default: module constant)")
    sp.set_defaults(func=cmd_lineages_validate)
    lsub.add_parser(
        "calibrate",
        help="run confidence calibration over the operator calibration "
             "eval set (OBL-21); writes var/lineage_calibration/"
             "{lineage_id}.json",
    ).set_defaults(func=cmd_lineages_calibrate)
    sp = lsub.add_parser(
        "record-eval",
        help="record real sandbox outputs from an operator-labeled media collection",
    )
    sp.add_argument("--manifest", required=True,
                    help="operator collection manifest JSON")
    sp.set_defaults(func=cmd_lineages_record_eval)

    p = sub.add_parser("model", help="signed production model artifacts")
    msub = p.add_subparsers(dest="model_command")
    sp = msub.add_parser(
        "install-resnet50",
        help="verify and sign the pinned ONNX Model Zoo ResNet-50 artifact",
    )
    sp.add_argument("--file", required=True,
                    help="pre-downloaded resnet50-v1-12-int8.onnx")
    sp.set_defaults(func=cmd_model_install_resnet50)
    msub.add_parser(
        "verify-resnet50", help="verify configured weights, mapping, and receipt"
    ).set_defaults(func=cmd_model_verify_resnet50)

    p = sub.add_parser(
        "references", help="verify/fetch the frozen Phase 2 controlling sources"
    )
    rsub = p.add_subparsers(dest="references_command")
    rsub.add_parser("status", help="show reference integrity status").set_defaults(
        func=cmd_references_status)
    rsub.add_parser("fetch", help="fetch and hash-verify reference sources").set_defaults(
        func=cmd_references_fetch)

    p = sub.add_parser(
        "retention",
        help="legal holds protecting cases from manual deletion "
             "(no auto-deletion in the MVP)")
    rsub = p.add_subparsers(dest="retention_command")
    sp = rsub.add_parser("hold", help="place a case on legal hold")
    sp.add_argument("case_id")
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_retention_hold)
    sp = rsub.add_parser("release", help="release a case legal hold")
    sp.add_argument("case_id")
    sp.set_defaults(func=cmd_retention_release)

    p = sub.add_parser(
        "tle",
        help="orbital catalog operations (manual refresh per A-012)")
    tsub = p.add_subparsers(dest="tle_command")
    tsub.add_parser(
        "refresh",
        help="operator-initiated one-shot fetch of TLE_CATALOG_URL").set_defaults(
        func=cmd_tle_refresh)

    p = sub.add_parser("audit", help="audit log view + chain verification")
    p.add_argument("case_id", nargs="?")
    p.add_argument("--all", action="store_true",
                   help="verify the whole chain (with verify)")
    p.set_defaults(func=cmd_audit)

    # `audit verify` is a two-word form: implement via a nested parser check.
    p = sub.add_parser("spend", help="monthly spend + payment summary")
    p.set_defaults(func=cmd_spend)

    p = sub.add_parser("backup", help="snapshot SQLite + var/ into a tarball")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="restore a backup tarball")
    p.add_argument("path")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("version", help="print version")
    p.set_defaults(func=cmd_version)
    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Support `uapvf audit verify ...` (verify as a positional action).
    if argv and argv[0] == "audit" and len(argv) > 1 and argv[1] == "verify":
        rest = argv[2:]
        all_flag = "--all" in rest
        case_id = None
        for tok in rest:
            if tok != "--all":
                case_id = tok
                break
        settings = get_settings()
        if not all_flag and not case_id:
            print("usage: uapvf audit verify <case_id> | --all", file=sys.stderr)
            return 2

        class _A:
            pass

        args = _A()
        args.all = all_flag
        args.case_id = case_id
        return cmd_audit_verify(args, settings)

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    settings = get_settings()
    return int(args.func(args, settings) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
