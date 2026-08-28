# -*- coding: utf-8 -*-
"""Hermes Skills 与平台用例计划互转（agentskills.io SKILL.md 格式）。"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modules.hermes.hermes_config import (
    hermes_skills_dir,
    hermes_skill_versions_dir,
    hermes_selector_store_path,
    hermes_skill_max_versions,
)


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "-", (name or "skill").strip(), flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-").lower()
    return s[:64] or "skill"


def list_skills() -> List[Dict[str, Any]]:
    root = hermes_skills_dir()
    out: List[Dict[str, Any]] = []
    if not root.is_dir():
        return out
    bundled_ids = set()
    try:
        from modules.hermes.hermes_skill_bootstrap import load_manifest

        for e in load_manifest().get("skills") or []:
            if isinstance(e, dict) and e.get("id"):
                bundled_ids.add(str(e["id"]))
    except Exception:
        bundled_ids = set()
    for skill_md in sorted(root.rglob("SKILL.md")):
        rel = skill_md.relative_to(root)
        module = str(rel.parent).replace("\\", "/")
        meta = _parse_skill_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        versions = list_skill_versions(module)
        fm_source = str(meta.get("source") or "").strip()
        if fm_source == "user-edited":
            source = "user-edited"
        elif fm_source in ("testory-bundled", "bundled") or module in bundled_ids:
            source = "bundled"
        else:
            source = fm_source or "local"
        out.append(
            {
                "id": module,
                "path": str(skill_md),
                "name": meta.get("name") or module,
                "description": meta.get("description") or "",
                "source": source,
                "updated": datetime.fromtimestamp(skill_md.stat().st_mtime).isoformat(),
                "version": len(versions) + 1,
                "version_count": len(versions),
            }
        )
    return out


def _parse_skill_frontmatter(text: str) -> Dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end < 0:
        return {}
    block = text[3:end].strip()
    meta: Dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"')
    return meta


# ---------------------------------------------------------------------------
# 版本历史
# ---------------------------------------------------------------------------

def _snapshot_before_export(skill_id: str, skill_path: Path) -> None:
    """将当前 SKILL.md 快照到版本历史目录（如果存在且有变更）。"""
    if not skill_path.is_file():
        return
    versions_dir = hermes_skill_versions_dir() / skill_id.replace("/", "_")
    versions_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    dest = versions_dir / f"SKILL_{ts}.md"

    # 避免短时间内重复快照（<2s 视为同一次）
    existing = sorted(versions_dir.glob("SKILL_*.md"), reverse=True)
    if existing:
        latest = existing[0]
        try:
            if latest.read_bytes() == skill_path.read_bytes():
                return  # 内容完全相同，不重复快照
        except OSError:
            pass

    shutil.copy2(str(skill_path), str(dest))

    # 清理旧版本
    max_ver = hermes_skill_max_versions()
    if max_ver > 0:
        all_versions = sorted(versions_dir.glob("SKILL_*.md"))
        while len(all_versions) > max_ver:
            oldest = all_versions.pop(0)
            try:
                oldest.unlink()
            except OSError:
                pass


def list_skill_versions(skill_id: str) -> List[Dict[str, Any]]:
    """列出某 Skill 的历史版本快照。"""
    versions_dir = hermes_skill_versions_dir() / skill_id.replace("/", "_")
    if not versions_dir.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for f in sorted(versions_dir.glob("SKILL_*.md"), reverse=True):
        meta = _parse_skill_frontmatter(f.read_text(encoding="utf-8", errors="replace"))
        out.append({
            "file": str(f),
            "name": meta.get("name", ""),
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "size_bytes": f.stat().st_size,
        })
    return out


def get_skill_version(skill_id: str, timestamp_tag: str) -> Optional[str]:
    """读取指定版本的 SKILL.md 内容（timestamp_tag 格式：YYYYMMDDTHHMMSS）。"""
    versions_dir = hermes_skill_versions_dir() / skill_id.replace("/", "_")
    target = versions_dir / f"SKILL_{timestamp_tag}.md"
    if target.is_file():
        return target.read_text(encoding="utf-8", errors="replace")
    return None


def restore_skill_version(skill_id: str, timestamp_tag: str) -> Tuple[bool, str]:
    """将 Skill 恢复到指定历史版本。"""
    content = get_skill_version(skill_id, timestamp_tag)
    if content is None:
        return False, f"版本不存在: {timestamp_tag}"
    skill_path = hermes_skills_dir() / skill_id / "SKILL.md"
    _snapshot_before_export(skill_id, skill_path)
    skill_path.write_text(content, encoding="utf-8")
    return True, f"已恢复到版本 {timestamp_tag}"


# ---------------------------------------------------------------------------
# 导出 / 应用
# ---------------------------------------------------------------------------

def export_plan_to_skill(
    plan: Dict[str, Any],
    *,
    skill_name: str,
    module_hint: str = "",
    environment_notes: str = "",
) -> Tuple[Path, Dict[str, Any]]:
    """将 AI 计划导出为 SKILL.md，返回 (path, summary)。"""
    case_name = (plan.get("case_name") or skill_name or "test-flow").strip()
    mod = _slug(module_hint or case_name)
    skill_root = hermes_skills_dir() / mod
    skill_root.mkdir(parents=True, exist_ok=True)
    steps = plan.get("steps") or []
    steps_json = json.dumps(steps, ensure_ascii=False, indent=2)
    desc = (plan.get("description") or f"自动化流程：{case_name}").strip()
    env_block = (environment_notes or plan.get("precondition") or "").strip()

    # 快照旧版本
    skill_path = skill_root / "SKILL.md"
    _snapshot_before_export(mod, skill_path)

    existing_versions = list_skill_versions(mod)
    version_num = len(existing_versions) + 1

    body = f"""---
name: {case_name}
description: {desc[:240]}
format: agentskills.io/v1
source: testory-ai-plan
version: {version_num}
updated_at: {datetime.now(timezone.utc).isoformat()}
---

# {case_name}

## 环境前提

{env_block or "（无）"}

## 用例 URL

{(plan.get("case_url") or "").strip() or "（见步骤 navigate）"}

## 预期结果

{(plan.get("expected_result") or "").strip() or "（见步骤 assert）"}

## 自动化步骤（平台 JSON）

```json
{steps_json}
```

## 维护说明

UI 变更时可在 AI Heal / AI Test 对话中说明变更，由 Hermes 更新本 Skill 并同步回用例步骤。
"""
    skill_path.write_text(body, encoding="utf-8")
    return skill_path, {
        "id": mod,
        "path": str(skill_path),
        "name": case_name,
        "step_count": len(steps),
        "version": version_num,
    }


def apply_skill_to_plan(skill_id: str, *, base_plan: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """从 SKILL.md 解析步骤 JSON 合并到 plan。"""
    warnings: List[str] = []
    root = hermes_skills_dir()
    skill_path = root / skill_id.replace("\\", "/") / "SKILL.md"
    if not skill_path.is_file():
        skill_path = root / skill_id / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"Skill 不存在: {skill_id}")

    text = skill_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.DOTALL)
    if not m:
        m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        raise ValueError("SKILL.md 中未找到步骤 JSON 代码块")

    raw = json.loads(m.group(1))
    if isinstance(raw, dict) and "steps" in raw:
        steps = raw["steps"]
    elif isinstance(raw, list):
        steps = raw
    else:
        raise ValueError("步骤 JSON 格式无效")

    plan = dict(base_plan) if isinstance(base_plan, dict) else {}
    meta = _parse_skill_frontmatter(text)
    plan.setdefault("case_name", meta.get("name") or skill_id)
    plan["steps"] = steps
    plan["skill_id"] = skill_id
    warnings.append(f"已从 Skill `{skill_id}` 导入 {len(steps)} 步")
    return plan, warnings


def request_hermes_skill_update(
    skill_id: str,
    user_message: str,
    *,
    failure_context: Optional[Dict[str, Any]] = None,
) -> str:
    """通过 Hermes Agent 对话更新 Skill（自愈 / UI 变更维护）。"""
    from modules.ai.agent_gateway_client import get_agent_gateway_client

    client = get_agent_gateway_client()
    if not client.is_configured():
        return json.dumps({"ok": False, "error": "Hermes Gateway 未配置"}, ensure_ascii=False)

    skill_path = hermes_skills_dir() / skill_id / "SKILL.md"
    existing = ""
    if skill_path.is_file():
        existing = skill_path.read_text(encoding="utf-8", errors="replace")[:12000]

    fc = ""
    if failure_context:
        fc = json.dumps(failure_context, ensure_ascii=False)[:4000]

    instruction = (
        f"请更新测试 Skill（ID: {skill_id}）。用户说明：{user_message}\n\n"
        f"当前 SKILL.md 内容：\n{existing}\n\n"
    )
    if fc:
        instruction += f"失败上下文：\n{fc}\n\n"
    instruction += (
        "请输出更新后的完整 SKILL.md（含 frontmatter 与步骤 JSON 代码块），"
        "并说明修改了哪些选择器或步骤。"
    )
    return client.execute_user_instruction(instruction)


# ---------------------------------------------------------------------------
# 跨用例选择器学习
# ---------------------------------------------------------------------------

def _load_selector_store() -> Dict[str, Any]:
    """加载选择器知识库。"""
    path = hermes_selector_store_path()
    if not path.is_file():
        return {"selectors": {}, "updated_at": ""}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"selectors": {}, "updated_at": ""}
    except (json.JSONDecodeError, OSError):
        return {"selectors": {}, "updated_at": ""}


def _save_selector_store(store: Dict[str, Any]) -> None:
    """保存选择器知识库。"""
    path = hermes_selector_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _collect_selector_alternates(step: Dict[str, Any]) -> List[str]:
    """从 locator_candidates / uia_anchor.candidates 收集备选 selector（阶段5 自愈学习）。

    回放自愈命中后，主 selector 之外的候选写入学习库 alternates，供后续用例回退定位。
    """
    out: List[str] = []

    def _push(v: Any) -> None:
        v = str(v or "").strip()
        if v and v not in out:
            out.append(v)

    lc = step.get("locator_candidates")
    if isinstance(lc, str) and lc.strip():
        try:
            lc = json.loads(lc)
        except Exception:
            lc = None
    if isinstance(lc, list):
        for c in lc:
            if isinstance(c, dict):
                _push(c.get("selector_value") or c.get("value") or c.get("selector"))
            elif isinstance(c, str):
                _push(c)

    ua = step.get("uia_anchor")
    if isinstance(ua, str) and ua.strip():
        try:
            ua = json.loads(ua)
        except Exception:
            ua = None
    if isinstance(ua, dict):
        for c in ua.get("candidates") or []:
            if isinstance(c, dict):
                _push(c.get("selector_value") or c.get("value"))
    return out


def extract_selectors_from_plan(plan: Dict[str, Any]) -> List[Dict[str, str]]:
    """从 plan 步骤中提取选择器模式。"""
    selectors: List[Dict[str, str]] = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        sel = step.get("selector") or step.get("selector_value", "")
        sel_type = step.get("selector_type", "css")
        action = (step.get("action") or "").strip().lower()
        desc = step.get("description", "")
        url = step.get("url") or plan.get("case_url", "")
        if sel and action in ("click", "input", "fill", "select", "tap", "double_click"):
            selectors.append({
                "selector": sel,
                "selector_type": sel_type,
                "action": action,
                "description": desc,
                "case_url": url,
                "alternates": _collect_selector_alternates(step),
            })
    return selectors


def learn_selectors_from_plan(
    plan: Dict[str, Any],
    *,
    case_name: str = "",
) -> int:
    """从成功执行的 plan 中提取选择器并存入知识库。返回新增条目数。"""
    selectors = extract_selectors_from_plan(plan)
    if not selectors:
        return 0

    store = _load_selector_store()
    sel_db: Dict[str, Any] = store.setdefault("selectors", {})
    added = 0

    for item in selectors:
        key = item["selector"]
        if key not in sel_db:
            sel_db[key] = {
                "selector_type": item["selector_type"],
                "action": item["action"],
                "description": item["description"],
                "case_urls": [],
                "success_count": 0,
                "last_used": "",
                "alternates": [],
            }
        entry = sel_db[key]
        entry["success_count"] = int(entry.get("success_count", 0)) + 1
        entry["last_used"] = datetime.now(timezone.utc).isoformat()
        url = item.get("case_url", "")
        if url and url not in entry.get("case_urls", []):
            entry.setdefault("case_urls", []).append(url)
            entry["case_urls"] = entry["case_urls"][-10:]
        # 阶段5：候选 selector（locator_candidates / uia_anchor.candidates）写入 alternates，
        # 供 lookup_alternate_selectors 在后续用例回放定位失败时回退（dict 格式对齐 record_selector_healing）
        alts = item.get("alternates") or []
        if alts:
            cur = entry.get("alternates") or []
            existing_sel = {
                a.get("selector") for a in cur if isinstance(a, dict) and a.get("selector")
            }
            changed = False
            for a in alts:
                if a != key and a not in existing_sel:
                    cur.append({
                        "selector": a,
                        "selector_type": item.get("selector_type", "css"),
                        "action": item.get("action", ""),
                        "learned_at": datetime.now(timezone.utc).isoformat(),
                    })
                    existing_sel.add(a)
                    changed = True
            if changed:
                entry["alternates"] = cur[-20:]
        added += 1

    _save_selector_store(store)
    return added


def lookup_alternate_selectors(selector: str) -> List[Dict[str, Any]]:
    """查询某选择器的替代选择器（来自其他用例的成功经验）。"""
    store = _load_selector_store()
    entry = store.get("selectors", {}).get(selector)
    if not entry:
        return []
    alternates = entry.get("alternates", [])
    return alternates


def record_selector_healing(
    old_selector: str,
    new_selector: str,
    *,
    selector_type: str = "css",
    action: str = "click",
    case_url: str = "",
) -> None:
    """记录选择器自愈事件：旧选择器失败 → 新选择器成功。
    更新知识库，为未来跨用例提供替代选择器。"""
    store = _load_selector_store()
    sel_db: Dict[str, Any] = store.setdefault("selectors", {})

    # 更新旧选择器的 alternates
    if old_selector in sel_db:
        alts = sel_db[old_selector].setdefault("alternates", [])
        existing_sel = {a.get("selector") for a in alts}
        if new_selector not in existing_sel:
            alts.append({
                "selector": new_selector,
                "selector_type": selector_type,
                "action": action,
                "learned_at": datetime.now(timezone.utc).isoformat(),
            })
            sel_db[old_selector]["alternates"] = alts[-20:]

    # 确保新选择器也被记录
    if new_selector not in sel_db:
        sel_db[new_selector] = {
            "selector_type": selector_type,
            "action": action,
            "description": f"healed from {old_selector[:60]}",
            "case_urls": [case_url] if case_url else [],
            "success_count": 1,
            "last_used": datetime.now(timezone.utc).isoformat(),
            "alternates": [],
        }
    else:
        sel_db[new_selector]["success_count"] = int(sel_db[new_selector].get("success_count", 0)) + 1
        sel_db[new_selector]["last_used"] = datetime.now(timezone.utc).isoformat()

    _save_selector_store(store)


def get_selector_stats() -> Dict[str, Any]:
    """返回选择器知识库统计摘要。"""
    store = _load_selector_store()
    sel_db = store.get("selectors", {})
    total = len(sel_db)
    healed = sum(1 for v in sel_db.values() if v.get("alternates"))
    top_used = sorted(
        sel_db.items(),
        key=lambda x: int(x[1].get("success_count", 0)),
        reverse=True,
    )[:10]
    return {
        "total_selectors": total,
        "healed_selectors": healed,
        "updated_at": store.get("updated_at", ""),
        "top_used": [
            {"selector": k, "count": int(v.get("success_count", 0))}
            for k, v in top_used
        ],
    }
