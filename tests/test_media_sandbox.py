"""Media sandbox boundary tests (FR-017 / spec §3.2)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

import pytest

from uapvf.media_sandbox import (
    SandboxError,
    _snapshot_tree,
    _spawn_worker,
    run_intake_sandboxed,
    sandbox_available,
)


class _FakeProc:
    """Stand-in for subprocess.Popen that executes side effects instead of a
    real worker and returns configured stdout/stderr/returncode."""

    def __init__(
        self,
        side_effect=None,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        raise_on_communicate: Optional[Exception] = None,
    ):
        self._side_effect = side_effect
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._raise = raise_on_communicate
        self.pid = 12345

    def communicate(self, input=None, timeout=None):
        if self._side_effect is not None:
            self._side_effect()
        if self._raise is not None:
            raise self._raise
        return (self._stdout.encode("utf-8"), self._stderr.encode("utf-8"))

    def kill(self):
        pass

    def wait(self, timeout=None):
        return self.returncode


class TestSnapshotTree:
    def test_inventories_files_and_subdirectories(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("b")
        entries = _snapshot_tree(tmp_path)
        assert entries == {"a.txt", os.path.join("sub", "b.txt")}

    def test_cap_truncates_inventory(self, tmp_path, monkeypatch):
        from uapvf import media_sandbox as ms

        monkeypatch.setattr(ms, "_SNAPSHOT_CAP", 3)
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text("x")
        entries = _snapshot_tree(tmp_path)
        assert len(entries) == 3

    def test_resolves_symlinked_root(self, tmp_path):
        real = tmp_path / "real_cases"
        real.mkdir()
        (real / "case1").mkdir()
        (real / "case1" / "file.txt").write_text("x")
        link = tmp_path / "cases_link"
        link.symlink_to(real)
        entries = _snapshot_tree(link)
        assert entries == {os.path.join("case1", "file.txt")}

    def test_empty_directory_returns_empty_set(self, tmp_path):
        assert _snapshot_tree(tmp_path) == set()


class TestSpawnWorker:
    def _make_popen(self, side_effect=None, returncode=0, stdout="",
                    stderr="", raise_on_communicate=None):
        def popen(*args, **kwargs):
            proc = _FakeProc(
                side_effect=side_effect,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                raise_on_communicate=raise_on_communicate,
            )
            return proc
        return popen

    def test_confined_worker_returns_result(self, tmp_path, monkeypatch):
        cases = tmp_path / "cases"
        case_dir = cases / "case1"
        case_dir.mkdir(parents=True)
        result = {"ok": True, "status_code": 200, "probe": {}, "quality": {}}

        def side_effect():
            (case_dir / "inside.txt").write_text("ok")

        monkeypatch.setattr(
            subprocess, "Popen",
            self._make_popen(side_effect=side_effect,
                             stdout=json.dumps(result)),
        )
        out, evidence = _spawn_worker(
            {"job": "intake"}, case_dir, timeout_s=10,
        )
        assert out == result
        assert evidence["confined_outputs"] is True
        assert evidence["stray_outputs"] == []
        assert evidence["boundary_verified"] is True

    def test_stray_outside_case_raises_sandbox_error(self, tmp_path,
                                                      monkeypatch):
        cases = tmp_path / "cases"
        case_dir = cases / "case1"
        other_case = cases / "case2"
        case_dir.mkdir(parents=True)
        other_case.mkdir(parents=True)
        result = {"ok": True, "status_code": 200, "probe": {}, "quality": {}}

        def side_effect():
            (other_case / "stolen.txt").write_text("bad")

        monkeypatch.setattr(
            subprocess, "Popen",
            self._make_popen(side_effect=side_effect,
                             stdout=json.dumps(result)),
        )
        with pytest.raises(SandboxError) as ei:
            _spawn_worker({"job": "intake"}, case_dir, timeout_s=10)
        assert "boundary breach" in str(ei.value)
        assert "case2" in str(ei.value)

    def test_timeout_returns_none_and_honest_evidence(self, tmp_path,
                                                       monkeypatch):
        case_dir = tmp_path / "cases" / "case1"
        case_dir.mkdir(parents=True)
        monkeypatch.setattr(
            subprocess, "Popen",
            self._make_popen(
                raise_on_communicate=subprocess.TimeoutExpired("cmd", 10),
            ),
        )
        out, evidence = _spawn_worker({"job": "intake"}, case_dir,
                                      timeout_s=10)
        assert out is None
        assert evidence["timed_out"] is True

    def test_unparseable_stdout_result_is_none(self, tmp_path, monkeypatch):
        case_dir = tmp_path / "cases" / "case1"
        case_dir.mkdir(parents=True)
        monkeypatch.setattr(
            subprocess, "Popen",
            self._make_popen(stdout="not json"),
        )
        out, evidence = _spawn_worker({"job": "intake"}, case_dir,
                                      timeout_s=10)
        assert out is None
        assert evidence["confined_outputs"] is True

    def test_spawn_failure_raises_sandbox_error(self, tmp_path, monkeypatch):
        case_dir = tmp_path / "cases" / "case1"
        case_dir.mkdir(parents=True)

        def boom(*args, **kwargs):
            raise FileNotFoundError("sandbox-exec")

        monkeypatch.setattr(subprocess, "Popen", boom)
        with pytest.raises(SandboxError) as ei:
            _spawn_worker({"job": "intake"}, case_dir, timeout_s=10)
        assert "spawn failed" in str(ei.value)

    def test_symlinked_cases_root_prefix_matches(self, tmp_path, monkeypatch):
        """Regression: the boundary check must not false-positive when the
        cases root is a symlink (snapshot and prefix must both be resolved)."""
        real_cases = tmp_path / "real_cases"
        real_cases.mkdir()
        case_dir = real_cases / "case1"
        case_dir.mkdir()
        link = tmp_path / "cases_link"
        link.symlink_to(real_cases)
        result = {"ok": True, "status_code": 200, "probe": {}, "quality": {}}

        def side_effect():
            (case_dir / "inside.txt").write_text("ok")

        monkeypatch.setattr(
            subprocess, "Popen",
            self._make_popen(side_effect=side_effect,
                             stdout=json.dumps(result)),
        )
        out, evidence = _spawn_worker({"job": "intake"}, case_dir,
                                      timeout_s=10)
        assert out == result
        assert evidence["confined_outputs"] is True


class TestRunIntakeSandboxed:
    def test_successful_worker_returns_out(self, tmp_path, monkeypatch):
        case_dir = tmp_path / "cases" / "case1"
        case_dir.mkdir(parents=True)
        result = {"ok": True, "status_code": 200, "probe": {"w": 1},
                  "quality": {"q": 2}}

        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *a, **k: _FakeProc(stdout=json.dumps(result)),
        )
        out = run_intake_sandboxed("c1", tmp_path / "media.jpg", case_dir,
                                   "image", {})
        assert out["ok"] is True
        assert out["status_code"] == 200
        assert out["probe"] == {"w": 1}
        assert out["quality"] == {"q": 2}
        assert out["sandbox"]["confined_outputs"] is True

    def test_timeout_maps_to_contained_rejection(self, tmp_path, monkeypatch):
        case_dir = tmp_path / "cases" / "case1"
        case_dir.mkdir(parents=True)
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *a, **k: _FakeProc(
                raise_on_communicate=subprocess.TimeoutExpired("cmd", 10),
            ),
        )
        out = run_intake_sandboxed("c1", tmp_path / "media.jpg", case_dir,
                                   "image", {})
        assert out["ok"] is False
        assert out["status_code"] == 422
        assert "timed out" in out["error"]

    def test_no_result_raises_sandbox_error(self, tmp_path, monkeypatch):
        case_dir = tmp_path / "cases" / "case1"
        case_dir.mkdir(parents=True)
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *a, **k: _FakeProc(stdout="no json"),
        )
        with pytest.raises(SandboxError):
            run_intake_sandboxed("c1", tmp_path / "media.jpg", case_dir,
                                 "image", {})


class TestSandboxAvailable:
    def test_non_darwin_is_false(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        assert sandbox_available() is False

    def test_darwin_without_executable_is_false(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        assert sandbox_available() is False

    def test_darwin_with_executable_is_true(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/sandbox-exec")
        assert sandbox_available() is True
