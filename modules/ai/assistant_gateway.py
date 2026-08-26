"""
OpenClaw 式轻编排：在单入口中补全 chat 请求体（从 case_id 装载 current_plan）。

WebSocket 对话（与 embedded_browser_gateway 的画布 WS 分离）：
- Flask 2 同步进程下原生 WebSocket 需 gevent / 独立 ASGI 进程；
- 若需服务端推送 AI token 流，优先考虑独立小服务（FastAPI + WS）或沿用现有
  /api/ai/task/chat-async + GET /api/ai/task/job/<id> 轮询，避免与当前 WSGI 部署强耦合。

渐进式「对话→执行」：见 POST /api/ai/agent/gateway-stream（SSE），由 agent_intent 做轻量意图识别，
在 **HuFirst 后台 Playwright 自动化会话** 或（若带 embedded_session_id）**远程画布同一会话**中逐步执行；左栏「识别并执行」已对接。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _row_to_plan_step(s: Dict[str, Any]) -> Dict[str, Any]:
    lc = s.get("locator_candidates")
    if lc is not None and not isinstance(lc, str):
        try:
            import json

            lc = json.dumps(lc, ensure_ascii=False)
        except Exception:
            lc = ""
    elif lc is None:
        lc = ""
    return {
        "action": (s.get("action") or "click"),
        "selector_type": s.get("selector_type") or "css",
        "selector_value": s.get("selector_value") or "",
        "input_value": s.get("input_value") or "",
        "description": s.get("description") or "",
        "step_order": s.get("step_order"),
        "url": s.get("url") or "",
        "enter_iframe": bool(s.get("enter_iframe")),
        "iframe_selector": s.get("iframe_selector") or "",
        "locator_candidates": lc,
        "click_repeat_count": s.get("click_repeat_count"),
    }


def build_current_plan_from_case(db: Any, case_id: int) -> Optional[Dict[str, Any]]:
    case = db.get_test_case_v2(int(case_id))
    if not case:
        return None
    steps: List[Dict[str, Any]] = db.get_case_steps(int(case_id))
    rows = [_row_to_plan_step(s) for s in steps if isinstance(s, dict)]
    pn = ""
    try:
        pid = case.get("project_id")
        if pid:
            proj = db.get_project(int(pid))
            if isinstance(proj, dict):
                pn = (proj.get("name") or "").strip()
    except Exception:
        pn = ""
    return {
        "case_name": case.get("name") or "",
        "case_url": case.get("url") or "",
        "description": case.get("description") or "",
        "precondition": case.get("precondition") or "",
        "expected_result": case.get("expected_result") or "",
        "steps": rows,
        "_project_name_hint": pn,
    }


def merge_case_into_chat_payload(db: Any, data: Dict[str, Any]) -> Optional[str]:
    """
    若 data 无有效 current_plan.steps 且含 case_id，则从 DB 写入 current_plan 与 project_name。
    返回错误字符串；成功返回 None。
    """
    data_dict = data
    cp = data_dict.get("current_plan")
    has_steps = isinstance(cp, dict) and isinstance(cp.get("steps"), list) and len(cp.get("steps") or []) > 0
    if has_steps:
        return None
    cid = data_dict.get("case_id")
    if cid is None or str(cid).strip() == "":
        return "缺少 current_plan 或 case_id"
    try:
        case_id = int(cid)
    except (TypeError, ValueError):
        return "case_id 无效"
    plan = build_current_plan_from_case(db, case_id)
    if not plan:
        return "测试用例不存在"
    pn = plan.pop("_project_name_hint", "") or ""
    data_dict["current_plan"] = plan
    if pn and not (data_dict.get("project_name") or "").strip():
        data_dict["project_name"] = pn
    return None
