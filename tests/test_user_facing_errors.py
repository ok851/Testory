# -*- coding: utf-8 -*-
"""用户可读错误提示映射。"""

from ai_modules.plan.user_facing_errors import (
    enrich_result_with_user_hint,
    user_hint_for_code,
)


def test_hint_for_empty_selector():
    hint = user_hint_for_code("EMPTY_SELECTOR")
    assert hint
    assert "选择器" in hint or "selector" in hint.lower()


def test_enrich_stage_and_summary():
    result = {
        "success": False,
        "error_code": "SYNC_DATA_TIMEOUT",
        "error": "data_sync 超时",
        "stage_results": [
            {
                "stage_id": "s2",
                "ok_assert": False,
                "error_code": "EMPTY_SELECTOR",
                "error": "click 缺少 selector",
            }
        ],
    }
    enrich_result_with_user_hint(result)
    assert "变量" in result["user_hint"]
    assert result["stage_results"][0]["user_hint"]
    hint0 = result["stage_results"][0]["user_hint"]
    assert "页面元素" in hint0 or "selector" in hint0.lower()
