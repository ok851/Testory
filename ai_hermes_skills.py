# -*- coding: utf-8 -*-
"""Hermes Skills 与平台用例计划互转（agentskills.io SKILL.md 格式）。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_config import hermes_skills_dir


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "-", (name or "skill").strip(), flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-").lower()
    return s[:64] or "skill"


def list_skills() -> List[Dict[str, Any]]:
    root = hermes_skills_dir()
    out: List[Dict[str, Any]] = []
    if not root.is_dir():
        return out
    for skill_md in sorted(root.rglob("SKILL.md")):
        rel = skill_md.relative_to(root)
        module = str(rel.parent).replace("\\", "/")
        meta = _parse_skill_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        out.append(
            {
                "id": module,
                "path": str(skill_md),
                "name": meta.get("name") or module,
                "description": meta.get("description") or "",
                "updated": datetime.fromtimestamp(skill_md.stat().st_mtime).isoformat(),
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
    body = f"""---
name: {case_name}
description: {desc[:240]}
format: agentskills.io/v1
source: testory-ai-plan
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
    path = skill_root / "SKILL.md"
    path.write_text(body, encoding="utf-8")
    return path, {"id": mod, "path": str(path), "name": case_name, "step_count": len(steps)}


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
    from agent_gateway_client import get_agent_gateway_client

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
