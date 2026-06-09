"""探测注册表：id 应优先于 name 作为 recommended_selector。"""

from ai_page_probe import (
    _recommended_selector,
    _selector_for_registry_row,
    heuristic_repair_plan_selectors_from_registry,
)


def test_recommended_selector_prefers_id_over_name() -> None:
    row = {"tag": "input", "id": "username", "name": "username", "css": ""}
    sel, st = _recommended_selector(row)
    assert sel == "input#username"
    assert st == "css"


def test_selector_for_registry_row_prefers_id() -> None:
    row = {
        "tag": "input",
        "id": "username",
        "name": "username",
        "recommended_selector": "input[name=\"username\"]",
        "recommended_selector_type": "css",
    }
    sel, st = _selector_for_registry_row(row)
    assert sel == "input#username"
    assert st == "css"


def test_heuristic_repair_xpath_login_click() -> None:
    registry = [
        {
            "tag": "button",
            "id": "submit-btn",
            "txt": "登录",
            "typ": "submit",
            "recommended_selector": "button#submit-btn",
            "recommended_selector_type": "css",
        }
    ]
    steps = [
        {
            "action": "click",
            "description": "点击登录",
            "selector_type": "xpath",
            "selector_value": "//button[contains(text(), '登录')]",
        }
    ]
    out, hints = heuristic_repair_plan_selectors_from_registry(steps, registry)
    assert out[0]["selector_value"] == "button#submit-btn"
    assert hints


def test_heuristic_repair_assert_empty_account_field() -> None:
    registry = [
        {
            "tag": "input",
            "id": "username",
            "typ": "text",
            "ph": "请输入账号",
            "recommended_selector": "input#username",
            "recommended_selector_type": "css",
        }
    ]
    steps = [
        {
            "action": "assert",
            "description": "断言账号输入框值为空",
            "selector_type": "css",
            "selector_value": "input[type='text']",
            "compare_type": "text_equals",
            "input_value": "",
        }
    ]
    out, hints = heuristic_repair_plan_selectors_from_registry(steps, registry)
    assert out[0]["selector_value"] == "input#username"
    assert out[0]["input_value"] == ""
    assert hints


def test_heuristic_repair_account_and_password_inputs() -> None:
    registry = [
        {
            "tag": "input",
            "id": "username",
            "name": "username",
            "typ": "text",
            "ph": "请输入账号",
            "recommended_selector": "input#username",
            "recommended_selector_type": "css",
        },
        {
            "tag": "input",
            "id": "password",
            "name": "password",
            "typ": "password",
            "ph": "请输入密码",
            "recommended_selector": "input#password",
            "recommended_selector_type": "css",
        },
    ]
    steps = [
        {
            "action": "input",
            "description": "将密码填入账号框",
            "selector_type": "css",
            "selector_value": "input[name='username']",
            "input_value": "kol@654321",
        },
        {
            "action": "input",
            "description": "将账号填入密码框",
            "selector_type": "css",
            "selector_value": "input[name='password']",
            "input_value": "admin",
        },
    ]
    out, hints = heuristic_repair_plan_selectors_from_registry(steps, registry)
    assert out[0]["selector_value"] == "input#username"
    assert out[1]["selector_value"] == "input#password"
    assert hints
