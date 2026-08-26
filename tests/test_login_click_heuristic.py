"""登录按钮选择器修复与校验逻辑单元测试。"""

import os

from modules.ai.ai_page_probe import heuristic_repair_plan_selectors_from_registry
from modules.web.playwright_automation import PlaywrightAutomation


def test_is_generic_login_submit_selector() -> None:
    assert PlaywrightAutomation._is_generic_login_submit_selector(
        "button[type='submit'], .login-btn"
    )
    assert not PlaywrightAutomation._is_generic_login_submit_selector("button#submit-btn")


def test_heuristic_repair_generic_login_click_to_submit_btn() -> None:
    os.environ["LOCAL_AI_HEURISTIC_SELECTOR_REPAIR"] = "1"
    steps = [
        {
            "action": "click",
            "description": "点击登录",
            "selector_type": "css",
            "selector_value": "button[type='submit'], .login-btn",
        }
    ]
    registry = [
        {
            "tag": "button",
            "id": "submit-btn",
            "txt": "登录",
            "typ": "submit",
            "recommended_selector": "#submit-btn",
            "recommended_selector_type": "css",
        }
    ]
    out, hints = heuristic_repair_plan_selectors_from_registry(steps, registry)
    assert out[0]["selector_value"] == "button#submit-btn"
    assert hints


def test_login_form_still_prominent_requires_both_password_and_button() -> None:
    import asyncio

    class _El:
        def __init__(self, visible: bool):
            self._visible = visible

        async def is_visible(self) -> bool:
            return self._visible

        async def click(self, timeout=8000):
            return None

    class _Loc:
        def __init__(self, items):
            self._items = items

        async def count(self) -> int:
            return len(self._items)

        def nth(self, i: int):
            return self._items[i]

        @property
        def first(self):
            return self._items[0] if self._items else _El(False)

    class _Page:
        def __init__(self, pw_only=False, login_only=False, both=False):
            self._pw_only = pw_only
            self._login_only = login_only
            self._both = both

        def locator(self, sel: str):
            if "password" in sel or "pwd" in sel:
                if self._pw_only or self._both:
                    return _Loc([_El(True)])
                return _Loc([])
            if "submit-btn" in sel or "login-btn" in sel or "submit" in sel:
                if self._login_only or self._both:
                    return _Loc([_El(True)])
                return _Loc([])
            return _Loc([])

        def get_by_role(self, role, name=None):
            return _Loc([])

        def get_by_text(self, text, exact=False):
            return _Loc([])

    auto = PlaywrightAutomation.__new__(PlaywrightAutomation)

    async def _run() -> None:
        page_pw_only = _Page(pw_only=True)
        assert await auto._login_form_still_prominent(page_pw_only) is False
        page_both = _Page(both=True)
        assert await auto._login_form_still_prominent(page_both) is True

    asyncio.run(_run())
