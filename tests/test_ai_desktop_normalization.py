"""桌面 AI 用例：归一化与 Web 污染修复。"""
from modules.ai.ai_step_normalization import (
    apply_step_normalization_to_plan,
    repair_desktop_ai_steps_inplace,
)


def test_repair_click_control_becomes_launch_app():
    steps = [
        {
            "action": "click",
            "input_value": "control",
            "description": "Launch the Control Panel by executing the 'control' command.",
        },
        {
            "action": "verify",
            "selector_type": "css",
            "selector_value": "控制面板",
            "input_value": "exist",
            "description": "Verify Control Panel window",
        },
    ]
    warns = repair_desktop_ai_steps_inplace(steps)
    assert steps[0]["action"] == "launch_app"
    assert steps[0]["automation_layer"] == "desktop"
    assert steps[0]["input_value"] == "control"
    assert any("launch_app" in w for w in warns)
    assert steps[1].get("selector_type") == "window"


def test_normalize_desktop_skips_navigate_warning():
    plan = {
        "case_name": "打开控制面板",
        "case_url": "",
        "meta": {"platform_type": "desktop"},
        "steps": [
            {
                "action": "launch_app",
                "automation_layer": "desktop",
                "input_value": "control",
                "selector_type": "",
                "selector_value": "",
                "description": "启动控制面板",
            },
            {
                "action": "wait",
                "automation_layer": "desktop",
                "input_value": "3",
                "selector_type": "",
                "selector_value": "",
                "description": "等待",
            },
        ],
    }
    out, warns = apply_step_normalization_to_plan(plan)
    assert out["platform"] == "desktop"
    assert not any("navigate" in w for w in warns)


def test_local_inference_desktop_normalize_output():
    from modules.ai.ai_local_inference import LocalAIService

    svc = LocalAIService()
    raw = {
        "case_name": "Control Panel",
        "case_url": "https://should-not-appear.com",
        "steps": [
            {
                "action": "click",
                "input_value": "control",
                "description": "open control panel",
            }
        ],
    }
    out = svc._normalize_desktop_output(raw, "打开控制面板", "proj", "test-model")
    assert out["case_url"] == ""
    assert out["platform"] == "desktop"
    assert out["steps"][0]["action"] == "launch_app"
    assert out["steps"][0]["automation_layer"] == "desktop"
