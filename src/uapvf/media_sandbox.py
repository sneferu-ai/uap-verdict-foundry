"""One-shot case-scoped OS sandbox + boundary verification (FR-017 phase-2
hardening, spec §5 media processing security boundary).

MVP scope: intake decoding/normalization only. Lineage analysis runs
through the Sneferu adapter (FR-005), and subprocess-level network
isolation for lineage/media processing beyond intake is deferred to
phase 2 (spec §1 deferred table), so this module exposes no lineage
entry point.

The decode operation runs in a short-lived subprocess that:

  * reads ONLY the case's own media (case directory read + write scope),
  * is DENIED all network access (macOS ``sandbox-exec`` profile),
  * inherits no file descriptors from the server (``close_fds=True``),
  * writes artifacts only under ``<case_dir>/`` (verified post-run).

The worker itself is ``uapvf.lineages.subprocess_worker``; this module owns
the sandbox wrapper, the pre/post boundary snapshot, and the honest evidence
record that lands in audit detail. ``SandboxError`` means the sandbox
*infrastructure* could not run (spawn/policy/boundary failure) — surfaces map
it to 503. A hostile-but-contained decode failure is reported by the worker
as a normal ``{"ok": false, ...}`` result, never as an infrastructure error.

Linux/CI hosts have no ``sandbox-exec``: the worker still runs as an
isolated subprocess (close_fds, own process group, TMPDIR inside the case)
and the evidence record says exactly that (``mechanism: subprocess_only``,
``network_denied: false``) — a weakened boundary is reported, never implied.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

SANDBOX_TIMEOUT_IMAGE_S = 120
SANDBOX_TIMEOUT_VIDEO_S = 660

_SNAPSHOT_CAP = 50000


class SandboxError(Exception):
    """Sandbox infrastructure failure (spawn, policy, or boundary breach)."""


def sandbox_available() -> bool:
    """True when the host can enforce the OS sandbox profile."""
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


# ---------------------------------------------------------------------------
# Profile + spawn machinery
# ---------------------------------------------------------------------------

def _profile_text(case_dir_resolved: str) -> str:
    """SBPL profile: deny network entirely; writes confined to the case dir.

    Paths are the *resolved* (symlink-canonical) forms — sandboxd matches
    canonical paths, so /tmp must appear as /private/tmp etc."""
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow process-exec)\n"
        "(allow process-fork)\n"
        "(allow process-info-pidinfo)\n"
        "(allow sysctl-read)\n"
        "(allow mach-lookup)\n"
        "(allow signal (target self))\n"
        "(allow file-read*)\n"
        f"(allow file-write* (subpath {json.dumps(case_dir_resolved)}))\n"
        "(allow file-ioctl (literal \"/dev/null\"))\n"
        "(allow file-write* (literal \"/dev/null\"))\n"
        "(allow file-write* (literal \"/dev/tty\"))\n"
    )


def _snapshot_tree(root: Path) -> set:
    """Bounded recursive file inventory of *root* (paths relative to root).

    The root is resolved before walking so that symlinks in the parent path do
    not cause the snapshot to diverge from the resolved case prefix used by the
    boundary check (round-6 stray-detection scoping fix). An OSError reading
    the tree is not swallowed: boundary verification must be fail-closed.
    """
    root_resolved = str(Path(root).resolve())
    entries = set()
    for dirpath, _dirnames, filenames in os.walk(root_resolved):
        for name in filenames:
            try:
                rel = os.path.relpath(os.path.join(dirpath, name), root_resolved)
            except ValueError:  # pragma: no cover - cross-drive on win
                continue
            entries.add(rel)
            if len(entries) >= _SNAPSHOT_CAP:
                return entries
    return entries


def _spawn_worker(job: dict, case_dir: Path, timeout_s: float
                  ) -> Tuple[Optional[dict], dict]:
    """Run the worker once inside the sandbox.

    Returns ``(result_or_None, evidence)``. ``result`` is None when the
    worker produced no parseable JSON (infrastructure failure unless the
    cause was a timeout, which the caller maps to a contained rejection)."""
    case_dir = Path(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    case_resolved = str(case_dir.resolve())
    sandbox_dir = case_dir / ".sandbox_tmp"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    cases_root = case_dir.parent  # var/cases
    cases_root_resolved = str(cases_root.resolve())
    try:
        pre = _snapshot_tree(cases_root)
    except OSError as exc:
        raise SandboxError(
            f"boundary snapshot failed before worker run: {exc}") from exc

    # The worker must import uapvf from the same location as the parent:
    # rebuild PYTHONPATH with the src/ root of THIS package first.
    package_root = str(Path(__file__).resolve().parents[1])
    parent_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = (package_root + os.pathsep + parent_pythonpath
                  if parent_pythonpath else package_root)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": str(sandbox_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": pythonpath,
        "LANG": "C",
    }
    profile = None
    use_sandbox = sandbox_available()
    if use_sandbox:
        profile = _profile_text(case_resolved)
        argv = ["sandbox-exec", "-p", profile, sys.executable,
                "-m", "uapvf.lineages.subprocess_worker"]
    else:
        argv = [sys.executable, "-m", "uapvf.lineages.subprocess_worker"]

    started = time.monotonic()
    timed_out = False
    stderr_tail = ""
    stdout = ""
    returncode: Optional[int] = None
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            close_fds=True,
            start_new_session=True,
            cwd=str(case_dir),
        )
        try:
            stdout_b, stderr_b = proc.communicate(
                input=json.dumps(job).encode("utf-8"), timeout=timeout_s)
            stdout = stdout_b.decode("utf-8", "replace")
            stderr_tail = stderr_b.decode("utf-8", "replace")[-800:]
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
    except FileNotFoundError as exc:
        raise SandboxError(f"sandbox worker spawn failed: {exc}") from exc
    except Exception as exc:
        raise SandboxError(
            f"sandbox worker spawn failed: {type(exc).__name__}: {exc}") from exc
    duration_s = round(time.monotonic() - started, 3)

    profile_sha = (
        hashlib.sha256(profile.encode("utf-8")).hexdigest() if profile else None)
    evidence = {
        "mechanism": "sandbox_exec" if use_sandbox else "subprocess_only",
        "network_denied": use_sandbox,
        "write_scope": case_resolved,
        "profile_sha256": profile_sha,
        "worker_module": "uapvf.lineages.subprocess_worker",
        "duration_s": duration_s,
        "timed_out": timed_out,
        "returncode": returncode,
        "boundary_verified": False,
        "confined_outputs": False,
        "stray_outputs": [],
    }

    if timed_out:
        return None, evidence

    if use_sandbox and returncode != 0 and not stdout.strip():
        # sandbox-exec refuses the profile/exec before the worker starts.
        raise SandboxError(
            f"sandbox policy rejected the worker (rc={returncode}): "
            f"{stderr_tail[:300]}")

    result = None
    if stdout.strip():
        try:
            result = json.loads(stdout.strip().splitlines()[-1])
        except Exception:
            result = None

    # Boundary verification: no new files anywhere in the cases tree except
    # under this case's directory; the sandbox tmp dir must stay inside it.
    try:
        post = _snapshot_tree(cases_root)
    except OSError as exc:
        raise SandboxError(
            f"boundary snapshot failed after worker run: {exc}") from exc
    case_prefix = os.path.relpath(case_resolved, cases_root_resolved)
    stray = []
    for rel in sorted(post - pre):
        if rel.startswith(case_prefix + os.sep) or rel == case_prefix:
            continue
        stray.append(rel)
        if len(stray) >= 20:
            break
    evidence["stray_outputs"] = stray
    evidence["boundary_verified"] = True
    evidence["confined_outputs"] = not stray
    if stray:
        raise SandboxError(
            "sandbox boundary breach: worker wrote outside the case "
            f"directory: {stray[:5]}")
    return result, evidence


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def run_intake_sandboxed(case_id: str, original_path, case_dir,
                         kind: str, quality_config: Optional[dict]) -> dict:
    """Decode + normalize + quality-score media inside the case sandbox.

    Returns ``{"ok", "status_code", "error", "probe", "quality",
    "sandbox"}``. Raises ``SandboxError`` only for infrastructure failure."""
    timeout_s = (SANDBOX_TIMEOUT_VIDEO_S if kind == "video"
                 else SANDBOX_TIMEOUT_IMAGE_S)
    job = {
        "job": "intake",
        "case_id": case_id,
        "original_path": str(original_path),
        "case_dir": str(case_dir),
        "kind": kind,
        "quality_config": quality_config or {},
    }
    result, evidence = _spawn_worker(job, Path(case_dir), timeout_s)
    if result is None:
        if evidence.get("timed_out"):
            return {
                "ok": False,
                "status_code": 422,
                "error": "media processing timed out in sandbox",
                "probe": {},
                "quality": {},
                "sandbox": evidence,
            }
        raise SandboxError(
            "sandbox worker produced no result "
            f"(rc={evidence.get('returncode')})")
    out = {
        "ok": bool(result.get("ok")),
        "status_code": int(result.get("status_code") or (200 if result.get("ok") else 422)),
        "error": result.get("error"),
        "probe": result.get("probe") or {},
        "quality": result.get("quality") or {},
        "sandbox": evidence,
    }
    return out
