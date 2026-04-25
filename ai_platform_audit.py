"""
AI 计划类操作的审计落库（与人工改 case 区分，action 以 AI_ 为前缀）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from database import Database
from logger import uat_logger

# 与 audit 页筛选兼容
AUDIT_TARGET_TYPE_AI_PLAN = "ai_plan"


def log_ai_plan_to_audit(
    user_id: int,
    username: str,
    action: str,
    plan: Optional[Dict[str, Any]],
    ip_address: Optional[str] = None,
) -> None:
    """
    记录一次 AI 生成/精炼结果摘要（不存全量 steps，避免表膨胀）。

    action 建议: AI_PLAN_GENERATE, AI_PLAN_REFINE, AI_PLAN_PAGE_GENERATE
    """
    try:
        p = plan or {}
        steps = p.get("steps")
        n = len(steps) if isinstance(steps, list) else 0
        meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
        details = {
            "source": "ai",
            "action": action,
            "step_count": n,
            "model": meta.get("model", "") or meta.get("profile_id", ""),
            "case_name": (p.get("case_name") or "")[:200],
        }
        Database().add_audit_log(
            user_id,
            username,
            action,
            AUDIT_TARGET_TYPE_AI_PLAN,
            None,
            json.dumps(details, ensure_ascii=False),
            ip_address,
        )
    except Exception as e:
        uat_logger.debug("log_ai_plan_to_audit: %s", e)
