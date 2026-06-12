"""Hermes bundled skills bootstrap tests."""
import json
from pathlib import Path

from ai_hermes_skills import list_skills
from hermes_config import ensure_hermes_home, hermes_skills_dir
from hermes_skill_bootstrap import (
    bundled_skills_source_dir,
    bundled_sync_status,
    load_manifest,
    sync_bundled_skills_to_hermes,
)


def test_load_manifest_has_four_skills():
    manifest = load_manifest()
    assert manifest.get("version")
    ids = {e.get("id") for e in manifest.get("skills") or [] if isinstance(e, dict)}
    assert "testory-web-browser" in ids
    assert "testory-android-mobile" in ids
    assert "testory-windows-desktop" in ids
    assert "testory-ui-design" in ids


def test_sync_bundled_skills_to_hermes(tmp_path, monkeypatch):
    src = tmp_path / "bundled_src"
    dest = tmp_path / "hermes" / "skills"
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path / "hermes_parent"))
    monkeypatch.setenv("HERMES_BUNDLED_SKILLS_DIR", str(src))
    monkeypatch.setenv("HERMES_SKILLS_DIR", str(dest))

    repo_bundled = bundled_skills_source_dir()
    if not repo_bundled.is_dir():
        repo_bundled = Path(__file__).resolve().parent.parent / "skills" / "bundled"
    import shutil

    shutil.copytree(repo_bundled, src, dirs_exist_ok=True)

    r1 = sync_bundled_skills_to_hermes(force=True)
    assert r1.get("ok") is True
    assert (dest / "testory-web-browser" / "SKILL.md").is_file()

    r2 = sync_bundled_skills_to_hermes(force=False)
    assert r2.get("skipped") is True

    status = bundled_sync_status()
    assert status.get("manifest_version")
    assert any(s.get("id") == "testory-web-browser" for s in status.get("skills") or [])


def test_user_edited_skill_not_overwritten(tmp_path, monkeypatch):
    src = tmp_path / "bundled_src"
    dest = tmp_path / "hermes" / "skills"
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path / "hermes_parent"))
    monkeypatch.setenv("HERMES_BUNDLED_SKILLS_DIR", str(src))
    monkeypatch.setenv("HERMES_SKILLS_DIR", str(dest))

    repo_bundled = Path(__file__).resolve().parent.parent / "skills" / "bundled"
    import shutil

    shutil.copytree(repo_bundled, src, dirs_exist_ok=True)

    skill_dir = dest / "testory-web-browser"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: testory-web-browser\nsource: user-edited\n---\n# user custom\n",
        encoding="utf-8",
    )

    r = sync_bundled_skills_to_hermes(force=True)
    assert "testory-web-browser" in (r.get("skipped_skills") or [])
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "user custom" in text


def test_ensure_hermes_home_syncs_bundled(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(repo_root)
    home = ensure_hermes_home(force_env=True)
    assert home.is_dir()
    assert (hermes_skills_dir() / "testory-web-browser" / "SKILL.md").is_file()


def test_list_skills_marks_bundled_source(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    ensure_hermes_home(force_env=True)
    skills = list_skills()
    bundled = [s for s in skills if s.get("id") == "testory-web-browser"]
    assert bundled
    assert bundled[0].get("source") == "bundled"


def test_build_explore_instruction():
    from hermes_skill_hints import build_explore_instruction

    text = build_explore_instruction("探索登录", {"platform": "mobile"})
    assert "testory-android-mobile" in text
    assert "探索登录" in text
