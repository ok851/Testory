# -*- coding: utf-8 -*-
"""Hermes 技能闭环：执行成功计数、自动导出 Skill、向 curator 提交摘要。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from logger import uat_logger


def _state_path() -> Path:
    base = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if not base:
        base = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Testory")
    p = Path(base) / "hermes" / "skill_loop_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_state() -> Dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return {"success_counts": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"success_counts": {}}
    except Exception:
        return {"success_counts": {}}


def _save_state(state: Dict[str, Any]) -> None:
    _state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _flow_key(case_url: str, plan: Dict[str, Any]) -> str:
    url = (case_url or plan.get("case_url") or "").strip().lower()
    name = (plan.get("case_name") or "").strip().lower()
    step_n = len(plan.get("steps") or [])
    return f"{url}|{name}|{step_n}"


def _auto_export_threshold() -> int:
    try:
        return max(0, int(os.environ.get("HERMES_SKILL_AUTO_EXPORT_AFTER", "3") or "3"))
    except ValueError:
        return 3


def record_execution_success(
    plan: Dict[str, Any],
    *,
    case_url: str = "",
    instruction: str = "",
    outcome: str = "ok",
) -> Dict[str, Any]:
    """
    记录一次成功执行；达到阈值时自动 export Skill。
    返回 {success_count, auto_exported, skill, suggest_export}
    """
    if not isinstance(plan, dict) or not plan.get("steps"):
        return {"success_count": 0, "auto_exported": False, "suggest_export": False}

    key = _flow_key(case_url, plan)
    state = _load_state()
    counts = state.setdefault("success_counts", {})
    counts[key] = int(counts.get(key) or 0) + 1
    _save_state(state)
    count = int(counts[key])
    threshold = _auto_export_threshold()
    result: Dict[str, Any] = {
        "success_count": count,
        "auto_exported": False,
        "suggest_export": count >= 1,
        "skill": None,
    }

    if threshold > 0 and count >= threshold and count % threshold == 0:
        try:
            from ai_hermes_skills import export_plan_to_skill

            skill_name = (plan.get("case_name") or "test-flow").strip()
            path, summary = export_plan_to_skill(plan, skill_name=skill_name, module_hint=skill_name)
            result["auto_exported"] = True
            result["skill"] = summary
            uat_logger.info(f"hermes skill auto-export key={key} count={count} path={path}")
            _submit_curator_async(plan, instruction=instruction, outcome=outcome, exported_skill_id=summary.get("id"))
        except Exception as e:
            uat_logger.warning(f"hermes skill auto-export failed: {e}")
    elif count == 1:
        _submit_curator_async(plan, instruction=instruction, outcome=outcome, exported_skill_id="")

    return result


def _submit_curator_async(
    plan: Dict[str, Any],
    *,
    instruction: str,
    outcome: str,
    exported_skill_id: str,
) -> None:
    if os.environ.get("HERMES_SKILL_CURATOR_ENABLE", "1").strip().lower() in ("0", "false", "no", "off"):
        return

    def _run() -> None:
        try:
            submit_execution_to_hermes_curator(plan, instruction=instruction, outcome=outcome, exported_skill_id=exported_skill_id)
        except Exception as e:
            uat_logger.debug(f"hermes curator submit skipped: {e}")

    threading.Thread(target=_run, daemon=True, name="hermes-curator").start()


def submit_execution_to_hermes_curator(
    plan: Dict[str, Any],
    *,
    instruction: str = "",
    outcome: str = "ok",
    exported_skill_id: str = "",
) -> str:
    """通过 Hermes Gateway 提交执行摘要，供 skills/memory toolset 学习。"""
    from agent_gateway_client import get_agent_gateway_client

    client = get_agent_gateway_client()
    if not client.is_configured():
        return json.dumps({"ok": False, "error": "Hermes Gateway 未配置"}, ensure_ascii=False)

    steps = plan.get("steps") or []
    preview = json.dumps(steps[:20], ensure_ascii=False)[:6000]
    msg = (
        "【Testory 测试执行摘要】请评估是否应提炼或更新 Hermes Skill。\n"
        f"结果: {outcome}\n"
        f"用例: {(plan.get('case_name') or '').strip()}\n"
        f"URL: {(plan.get('case_url') or '').strip()}\n"
    )
    if instruction:
        msg += f"用户指令: {instruction[:2000]}\n"
    if exported_skill_id:
        msg += f"已导出 Skill ID: {exported_skill_id}\n"
    msg += f"步骤 JSON（节选）:\n{preview}\n\n"
    msg += "若模式重复且成功，请用 skill_manage 维护对应 Skill；必要时写入 memory。"
    return client.execute_user_instruction(msg)


def export_plan_skill_now(plan: Dict[str, Any], *, skill_name: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
    from ai_hermes_skills import export_plan_to_skill

    if not isinstance(plan, dict) or not plan.get("steps"):
        return None, "plan.steps 不能为空"
    name = (skill_name or plan.get("case_name") or "test-flow").strip()
    _, summary = export_plan_to_skill(plan, skill_name=name, module_hint=name)
    return summary, ""
