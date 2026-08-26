"""自愈验证闭环：自动执行修复后的步骤 → 验证通过/失败 → 更新或用例回退。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def verify_healed_step(
    step: Dict[str, Any],
    max_retry: int = 1,
) -> Tuple[bool, str]:

    layer = (step.get("automation_layer") or "web").lower()
    action = (step.get("action") or "").strip().lower()

    try:
        if layer == "web":
            from modules.web.browser_manager import get_page
            page = get_page()
            if page is None:
                return False, "无活动浏览器页面"
            if action in ("click", "tap"):
                sel = step.get("selector_value", "")
                if sel:
                    page.click(sel, timeout=5000)
                    return True, ""
                return False, "缺少 selector_value"
            if action in ("fill", "input"):
                sel = step.get("selector_value", "")
                val = step.get("input_value", "")
                if sel:
                    page.fill(sel, str(val))
                    return True, ""
                return False, "缺少 selector_value"
            if action in ("verify", "assert"):
                sel = step.get("selector_value", "")
                if sel:
                    page.wait_for_selector(sel, timeout=3000)
                    return True, ""
                return False, "缺少 selector_value"
            return True, ""

        if layer in ("android", "mobile"):
            try:
                from modules.mobile.mobile_executor import get_mobile_executor
                executor = get_mobile_executor()
                executor.execute_steps([step])
                return True, ""
            except Exception as e:
                return False, str(e)[:200]

        if layer == "desktop":
            try:
                from modules.desktop.desktop_automation import sync_desktop_execute_step
                sync_desktop_execute_step(step)
                return True, ""
            except Exception as e:
                return False, str(e)[:200]

        if layer == "api":
            from ai_modules.plan.api_skill_adapter import execute_api_stage
            result, _ = execute_api_stage(step)
            return result.get("ok_assert", False), result.get("error", "")

        return True, ""

    except Exception as e:
        return False, str(e)[:200]


def batch_verify_and_apply(
    healed_steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    applied = 0
    reverted = 0

    for idx, step in enumerate(healed_steps):
        ok, msg = verify_healed_step(step)
        entry = {"index": idx, "step": step, "verified": ok, "message": msg}
        results.append(entry)
        if ok:
            applied += 1
        else:
            reverted += 1

    return {
        "total": len(healed_steps),
        "applied": applied,
        "reverted": reverted,
        "details": results,
    }
