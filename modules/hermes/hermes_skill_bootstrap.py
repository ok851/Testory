# -*- coding: utf-8 -*-
"""将 repo 内 skills/bundled 同步到 HERMES_HOME/skills。"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from modules.hermes.hermes_config import hermes_skills_dir

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _PROJECT_ROOT / "skills" / "manifest.json"
_SYNC_STATE_NAME = ".bundled-sync-state.json"


def bundled_skills_source_dir() -> Path:
    custom = (os.environ.get("HERMES_BUNDLED_SKILLS_DIR") or "").strip()
    if custom:
        return Path(custom)
    return _PROJECT_ROOT / "skills" / "bundled"


def bundled_sync_enabled() -> bool:
    return os.environ.get("HERMES_SYNC_BUNDLED_SKILLS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def load_manifest() -> Dict[str, Any]:
    if not _MANIFEST_PATH.is_file():
        return {"version": "0", "skills": []}
    try:
        raw = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"version": "0", "skills": []}
    except Exception:
        return {"version": "0", "skills": []}


def _parse_frontmatter_source(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("---", 3)
    if end < 0:
        return ""
    for line in text[3:end].splitlines():
        if line.strip().startswith("source:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def _is_user_edited_skill(dest_skill_md: Path) -> bool:
    if not dest_skill_md.is_file():
        return False
    try:
        text = dest_skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _parse_frontmatter_source(text) == "user-edited"


def _sync_state_path() -> Path:
    return hermes_skills_dir() / _SYNC_STATE_NAME


def _load_sync_state() -> Dict[str, Any]:
    path = _sync_state_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_sync_state(state: Dict[str, Any]) -> None:
    path = _sync_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _copy_tree(src: Path, dest: Path) -> List[str]:
    copied: List[str] = []
    if not src.is_dir():
        return copied
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied.append(str(rel).replace("\\", "/"))
    return copied


def sync_bundled_skills_to_hermes(*, force: bool = False) -> Dict[str, Any]:
    """
    从 skills/bundled/ 复制到 hermes_skills_dir()。
    用户标记 source: user-edited 的 skill 默认跳过（force=True 时仍跳过）。
    """
    result: Dict[str, Any] = {
        "ok": True,
        "skipped": False,
        "manifest_version": "",
        "synced": [],
        "skipped_skills": [],
        "errors": [],
    }
    if not bundled_sync_enabled():
        result["skipped"] = True
        result["reason"] = "HERMES_SYNC_BUNDLED_SKILLS=0"
        return result

    src_root = bundled_skills_source_dir()
    if not src_root.is_dir():
        result["ok"] = False
        result["errors"].append(f"bundled source missing: {src_root}")
        return result

    manifest = load_manifest()
    manifest_version = str(manifest.get("version") or "0")
    result["manifest_version"] = manifest_version

    dest_root = hermes_skills_dir()
    dest_root.mkdir(parents=True, exist_ok=True)

    prev_state = _load_sync_state()
    prev_version = str(prev_state.get("manifest_version") or "")
    if not force and prev_version == manifest_version and prev_state.get("synced"):
        result["skipped"] = True
        result["reason"] = "manifest unchanged"
        result["synced"] = list(prev_state.get("synced") or [])
        return result

    synced_ids: List[str] = []
    for entry in manifest.get("skills") or []:
        if not isinstance(entry, dict):
            continue
        skill_id = str(entry.get("id") or entry.get("name") or "").strip()
        if not skill_id:
            continue
        src_skill = src_root / skill_id
        if not src_skill.is_dir():
            result["errors"].append(f"missing bundled skill dir: {skill_id}")
            continue
        dest_skill_md = dest_root / skill_id / "SKILL.md"
        if _is_user_edited_skill(dest_skill_md):
            result["skipped_skills"].append(skill_id)
            continue
        try:
            if dest_skill_md.parent.is_dir() and force:
                shutil.rmtree(dest_skill_md.parent, ignore_errors=True)
            _copy_tree(src_skill, dest_root / skill_id)
            synced_ids.append(skill_id)
        except OSError as exc:
            result["errors"].append(f"{skill_id}: {exc}")

    result["synced"] = synced_ids
    result["ok"] = not result["errors"]
    _save_sync_state(
        {
            "manifest_version": manifest_version,
            "synced": synced_ids,
            "skipped_skills": result["skipped_skills"],
        }
    )
    return result


def bundled_sync_status() -> Dict[str, Any]:
    """供 API 展示 bundled 同步状态。"""
    manifest = load_manifest()
    state = _load_sync_state()
    dest_root = hermes_skills_dir()
    installed: List[Dict[str, Any]] = []
    for entry in manifest.get("skills") or []:
        if not isinstance(entry, dict):
            continue
        skill_id = str(entry.get("id") or "").strip()
        if not skill_id:
            continue
        skill_md = dest_root / skill_id / "SKILL.md"
        installed.append(
            {
                "id": skill_id,
                "version": entry.get("version"),
                "installed": skill_md.is_file(),
                "user_edited": _is_user_edited_skill(skill_md) if skill_md.is_file() else False,
            }
        )
    return {
        "enabled": bundled_sync_enabled(),
        "manifest_version": manifest.get("version"),
        "last_sync_version": state.get("manifest_version"),
        "source_dir": str(bundled_skills_source_dir()),
        "dest_dir": str(dest_root),
        "skills": installed,
    }
