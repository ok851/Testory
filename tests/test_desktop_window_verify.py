"""桌面窗口级 verify/attach 不应触发 visual 废弃逻辑。"""
import pytest

from desktop_automation import DesktopAutomation
from desktop_env_config import prepare_desktop_step
from desktop_visual_engine import is_legacy_desktop_step


def test_window_verify_not_legacy():
    step = {
        "action": "verify",
        "automation_layer": "desktop",
        "selector_type": "window",
        "selector_value": "控制面板",
        "input_value": "exist",
        "desktop_spec": '{"title_contains": "控制面板"}',
    }
    assert is_legacy_desktop_step(step) is False


def test_attach_window_not_legacy():
    step = {
        "action": "attach_window",
        "automation_layer": "desktop",
        "desktop_spec": '{"title_contains": "控制面板"}',
        "selector_type": "",
        "selector_value": "",
    }
    assert is_legacy_desktop_step(step) is False


def test_prepare_desktop_spec_title_contains_to_regex():
    raw = {
        "action": "attach_window",
        "automation_layer": "desktop",
        "desktop_spec": '{"title_contains": "控制面板"}',
    }
    step = prepare_desktop_step(raw)
    spec = step.get("desktop_spec") or {}
    if isinstance(spec, str):
        import json
        spec = json.loads(spec)
    assert "window_title_re" in spec


@pytest.mark.skipif(
    __import__("sys").platform != "win32",
    reason="Windows only",
)
def test_verify_window_exists_smoke():
    from desktop_runtime import desktop_runtime_available

    if not desktop_runtime_available():
        pytest.skip("desktop runtime not available")
    auto = DesktopAutomation()
    step = prepare_desktop_step({
        "action": "verify",
        "automation_layer": "desktop",
        "selector_type": "window",
        "selector_value": "Program Manager",
        "input_value": "exist",
    })
    spec = step.get("desktop_spec") if isinstance(step.get("desktop_spec"), dict) else {}
    if not spec:
        from desktop_runtime import parse_desktop_spec
        spec = parse_desktop_spec(step.get("desktop_spec"))
    result = auto._verify_window_exists(step, spec, action="verify")
    assert result.get("verified") is True
