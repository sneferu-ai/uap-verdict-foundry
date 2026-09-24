from __future__ import annotations

from pathlib import Path


def test_full_story_bundle_has_static_roles_and_labelled_video(settings):
    from uapvf.storyforge import (
        generate_story_bundle,
        verify_exported_asset,
        verify_exported_video,
    )
    from uapvf.xenoscience import build_xenoscience

    case_id = "11111111-2222-4333-8444-555555555555"
    case_dir = settings.cases_dir / case_id
    source = Path(__file__).parent / "fixtures" / "unexplained.jpg"
    xeno = build_xenoscience(case_id, "insufficient_data", {}, [], None)
    assets = generate_story_bundle(
        case_id, str(case_dir), str(source), xeno, [], include_video=True)
    assert {asset.get("role") for asset in assets} == {
        "physics_object", "planet", "comic", "short_clip"}
    for asset in assets:
        assert Path(asset["path"]).is_file()
        assert asset["label_verified"] is True
        assert asset["similarity"]["passed"] is True
        if asset["kind"] == "video/mp4":
            assert verify_exported_video(asset["path"]) == []
        else:
            assert verify_exported_asset(asset["path"]) == []
