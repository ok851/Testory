# -*- coding: utf-8 -*-
"""场景版本管理：版本历史/导入导出/回滚/差异对比。

每个场景维护一个版本链，每次保存自动生成新版本。
支持：
- 保存新版本 / 查看版本历史 / 回滚到指定版本
- 导出为 JSON / 从 JSON 导入
- 两版本差异对比（stages/variables/断言变化）
- Git 风格 commit message
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from uat_logger import uat_logger
except ImportError:
    import logging
    uat_logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(data: Any) -> str:
    """生成内容哈希（去重用）。"""
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _versions_dir() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    root = Path(env).expanduser().resolve() if env else Path(__file__).resolve().parents[2] / "data"
    d = root / "scenario_versions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _version_file(scenario_id: str) -> Path:
    sid = "".join(c for c in str(scenario_id) if c.isalnum() or c in "-_")[:64]
    return _versions_dir() / f"{sid}.json"


def _load_versions(scenario_id: str) -> List[Dict[str, Any]]:
    p = _version_file(scenario_id)
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_versions(scenario_id: str, versions: List[Dict[str, Any]]) -> None:
    p = _version_file(scenario_id)
    p.write_text(json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8")


class ScenarioVersionManager:
    """场景版本管理器。"""

    MAX_VERSIONS = 50  # 每场景最多保留版本数

    def save_version(
        self,
        scenario_id: str,
        plan: Dict[str, Any],
        *,
        message: str = "",
        author: str = "",
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """保存新版本。如果内容与最新版本相同则跳过。"""
        versions = _load_versions(scenario_id)

        # 去重：内容哈希与最新版本相同则不重复保存
        if versions:
            latest = versions[-1]
            if latest.get("content_hash") == _content_hash(plan):
                return {
                    "success": True,
                    "skipped": True,
                    "version": latest.get("version", 0),
                    "message": "内容未变化，跳过保存",
                }

        version_num = (versions[-1].get("version", 0) + 1) if versions else 1
        entry = {
            "version": version_num,
            "version_id": f"v{version_num}-{uuid.uuid4().hex[:8]}",
            "content_hash": _content_hash(plan),
            "created_at": _utc_iso(),
            "message": message or f"版本 {version_num}",
            "author": author,
            "tags": list(tags or []),
            "plan": copy.deepcopy(plan),
            "stage_count": len(plan.get("stages") or []),
            "layers": list({s.get("layer", "") for s in (plan.get("stages") or [])}),
        }
        versions.append(entry)

        # 限制版本数
        if len(versions) > self.MAX_VERSIONS:
            versions = versions[-self.MAX_VERSIONS:]

        _save_versions(scenario_id, versions)
        uat_logger.info(
            "场景 %s 保存版本 v%d (%d stages)",
            scenario_id, version_num, entry["stage_count"],
        )
        return {"success": True, "version": version_num, "version_id": entry["version_id"]}

    def get_history(self, scenario_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """获取版本历史（不含 plan 详情，减少传输）。"""
        versions = _load_versions(scenario_id)
        out = []
        for v in versions[-limit:]:
            out.append({
                "version": v.get("version"),
                "version_id": v.get("version_id"),
                "created_at": v.get("created_at"),
                "message": v.get("message"),
                "author": v.get("author"),
                "tags": v.get("tags", []),
                "stage_count": v.get("stage_count"),
                "layers": v.get("layers"),
                "content_hash": v.get("content_hash"),
            })
        return out

    def get_version(self, scenario_id: str, version: int) -> Optional[Dict[str, Any]]:
        """获取指定版本的完整 plan。"""
        versions = _load_versions(scenario_id)
        for v in versions:
            if v.get("version") == version:
                return v
        return None

    def rollback(self, scenario_id: str, version: int) -> Dict[str, Any]:
        """回滚到指定版本（创建新版本，内容为历史版本的 plan）。"""
        target = self.get_version(scenario_id, version)
        if not target:
            return {"success": False, "error": f"版本 {version} 不存在"}
        plan = target.get("plan")
        if not isinstance(plan, dict):
            return {"success": False, "error": f"版本 {version} 的 plan 无效"}
        return self.save_version(
            scenario_id,
            plan,
            message=f"回滚到版本 {version}",
            tags=["rollback"],
        )

    def diff(self, scenario_id: str, v1: int, v2: int) -> Dict[str, Any]:
        """对比两个版本的差异。"""
        ver1 = self.get_version(scenario_id, v1)
        ver2 = self.get_version(scenario_id, v2)
        if not ver1:
            return {"error": f"版本 {v1} 不存在"}
        if not ver2:
            return {"error": f"版本 {v2} 不存在"}
        plan1 = ver1.get("plan") or {}
        plan2 = ver2.get("plan") or {}
        return _diff_plans(plan1, plan2)

    def export_version(self, scenario_id: str, version: Optional[int] = None) -> Dict[str, Any]:
        """导出场景（指定版本或最新版本）。"""
        versions = _load_versions(scenario_id)
        if not versions:
            return {"success": False, "error": "无版本历史"}
        if version is not None:
            target = self.get_version(scenario_id, version)
            if not target:
                return {"success": False, "error": f"版本 {version} 不存在"}
        else:
            target = versions[-1]
        return {
            "success": True,
            "scenario_id": scenario_id,
            "export_version": target.get("version"),
            "created_at": target.get("created_at"),
            "plan": target.get("plan"),
            "exported_at": _utc_iso(),
        }

    def import_version(
        self,
        scenario_id: str,
        data: Dict[str, Any],
        *,
        message: str = "导入",
        author: str = "",
    ) -> Dict[str, Any]:
        """从导出的 JSON 导入场景版本。"""
        plan = data.get("plan")
        if not isinstance(plan, dict) or not plan.get("stages"):
            return {"success": False, "error": "无效的导入数据（缺少 plan.stages）"}
        return self.save_version(scenario_id, plan, message=message, author=author, tags=["imported"])

    def list_all_scenarios(self) -> List[Dict[str, Any]]:
        """列出所有有版本历史的场景。"""
        out = []
        for p in _versions_dir().glob("*.json"):
            sid = p.stem
            versions = _load_versions(sid)
            if versions:
                latest = versions[-1]
                out.append({
                    "scenario_id": sid,
                    "version_count": len(versions),
                    "latest_version": latest.get("version"),
                    "latest_at": latest.get("created_at"),
                    "latest_message": latest.get("message"),
                })
        return out


def _diff_plans(plan1: Dict[str, Any], plan2: Dict[str, Any]) -> Dict[str, Any]:
    """对比两个 plan 的差异。"""
    diff: Dict[str, Any] = {
        "v1_stages": len(plan1.get("stages") or []),
        "v2_stages": len(plan2.get("stages") or []),
        "scenario_changed": plan1.get("scenario") != plan2.get("scenario"),
        "stage_changes": [],
        "variable_changes": {},
        "assertion_changes": False,
    }

    stages1 = {s.get("id", ""): s for s in (plan1.get("stages") or []) if isinstance(s, dict)}
    stages2 = {s.get("id", ""): s for s in (plan2.get("stages") or []) if isinstance(s, dict)}

    all_ids = set(stages1.keys()) | set(stages2.keys())
    for sid in sorted(all_ids):
        s1 = stages1.get(sid)
        s2 = stages2.get(sid)
        if s1 and not s2:
            diff["stage_changes"].append({"stage_id": sid, "change": "removed"})
        elif s2 and not s1:
            diff["stage_changes"].append({"stage_id": sid, "change": "added"})
        elif s1 and s2:
            changes = []
            for key in ("layer", "label", "on_failure", "cleanup"):
                if s1.get(key) != s2.get(key):
                    changes.append(key)
            steps1 = len(s1.get("steps") or [])
            steps2 = len(s2.get("steps") or [])
            if steps1 != steps2:
                changes.append(f"steps({steps1}->{steps2})")
            if changes:
                diff["stage_changes"].append({
                    "stage_id": sid,
                    "change": "modified",
                    "fields": changes,
                })

    # 变量差异
    vars1 = plan1.get("variables") or plan1.get("initial_variables") or {}
    vars2 = plan2.get("variables") or plan2.get("initial_variables") or {}
    all_var_keys = set(vars1.keys()) | set(vars2.keys())
    for k in sorted(all_var_keys):
        v1 = vars1.get(k)
        v2 = vars2.get(k)
        if v1 != v2:
            diff["variable_changes"][k] = {"v1": v1, "v2": v2}

    # 断言差异
    a1 = plan1.get("cross_end_assertions") or plan1.get("assertions") or []
    a2 = plan2.get("cross_end_assertions") or plan2.get("assertions") or []
    diff["assertion_changes"] = a1 != a2
    diff["v1_assertions"] = len(a1)
    diff["v2_assertions"] = len(a2)

    diff["has_changes"] = bool(
        diff["scenario_changed"]
        or diff["stage_changes"]
        or diff["variable_changes"]
        or diff["assertion_changes"]
    )
    return diff
