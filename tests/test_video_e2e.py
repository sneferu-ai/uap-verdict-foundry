from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from conftest import base_fields

# Stale against the shipped MVP scope: expects seven local lineages plus buyer assets, both deferred to phase 2; video intake itself is covered elsewhere.
pytestmark = pytest.mark.skip(reason="phase 2 / removed: expects seven local lineages plus buyer assets, both deferred to phase 2; video intake itself is covered elsewhere")



def test_real_video_reaches_report_with_frame_times_and_story_clip(
        var_dir, conn, tmp_path, monkeypatch):
    """Real ffmpeg input → sandbox → seven lineages → report + buyer assets."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
    monkeypatch.setenv("SNEFERU_MOCK", "0")
    monkeypatch.setenv("UAPV_LINEAGE_RUNTIME", "simulation")
    monkeypatch.setenv("UAPV_REQUIRE_SNEFERU", "0")
    monkeypatch.setenv("UAPV_SIGNING_PASSPHRASE", "video-e2e-signing-secret")

    source = tmp_path / "moving-capture.mp4"
    made = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=1280x720:rate=30", "-t", "2", "-an",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    assert made.returncode == 0, made.stderr.decode("utf-8", "replace")

    from uapvf.config import get_settings
    from uapvf.intake import create_case
    from uapvf.pipeline import process_one_case

    settings = get_settings()
    created = create_case(
        source,
        base_fields(duration_seconds="2", weather="clear"),
        settings,
        conn,
    )
    completed = process_one_case(created["case_id"], settings=settings, conn=conn)
    assert completed["status"] == "complete"

    report_path = settings.cases_dir / created["case_id"] / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["analysis_contract"]["observed_lineages"] == 7
    trajectory = next(
        item for item in report["lineage_outputs"]
        if item["lineage_id"] == "l5-trajectory"
    )
    assert trajectory["provenance"]["frame_indices_used"]
    assert trajectory["provenance"]["frame_timestamps_seconds"]
    assets = report["interpretation_section"]["story_assets"]
    assert {item["kind"] for item in assets} >= {"image/png", "video/mp4"}
    assert all(item["label_verified"] for item in assets)
