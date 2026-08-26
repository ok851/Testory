"""桌面跨步骤执行上下文贯通测试。"""
import json

from modules.desktop.desktop_env_config import prepare_desktop_step
from modules.desktop.desktop_run_context import (
    DesktopRunContext,
    enrich_desktop_step_with_run_context,
    reset_desktop_run_context,
    update_context_from_step_result,
    window_hints_for_launch,
)


def test_window_hints_for_control():
    hints = window_hints_for_launch("control")
    assert "控制面板" in hints


def test_enrich_attach_from_launch_context():
    ctx = DesktopRunContext()
    ctx.remember_launch("control", hwnd=12345, title_hint="控制面板")
    raw = {
        "action": "attach_window",
        "automation_layer": "desktop",
        "desktop_spec": {"backend": "uia"},
        "description": "附着到控制面板窗口",
    }
    enriched = enrich_desktop_step_with_run_context(raw, ctx)
    spec = enriched["desktop_spec"]
    assert spec.get("title_contains") == "控制面板" or spec.get("hwnd") == 12345


def test_prepare_attach_from_description_only():
    raw = {
        "action": "attach_window",
        "automation_layer": "desktop",
        "description": "附着控制面板",
        "desktop_spec": "{}",
    }
    step = prepare_desktop_step(raw)
    spec = step.get("desktop_spec") or {}
    if isinstance(spec, str):
        spec = json.loads(spec)
    assert spec.get("title_contains") == "控制面板"
    assert "window_title_re" in spec


def test_context_updates_after_launch():
    reset_desktop_run_context()
    from modules.desktop.desktop_run_context import get_desktop_run_context

    step = {"action": "launch_app", "input_value": "control"}
    result = {"hwnd": 999, "window_title": "控制面板"}
    update_context_from_step_result(step, result)
    ctx = get_desktop_run_context()
    assert ctx.attached_hwnd == 999
    assert ctx.last_window_title_hint == "控制面板"


def test_enrich_injects_hwnd_even_when_title_spec_present():
    """错误标题不得挡住 launch 已记住的 hwnd（主路径关键）。"""
    ctx = DesktopRunContext()
    ctx.remember_launch("notepad.exe", hwnd=4242, title_hint="无标题 - Notepad")
    raw = {
        "action": "attach_window",
        "automation_layer": "desktop",
        "desktop_spec": {"window_title_re": ".*记事本.*"},
        "description": "附着",
    }
    enriched = enrich_desktop_step_with_run_context(raw, ctx)
    assert enriched["desktop_spec"].get("hwnd") == 4242


def test_notepad_hints_include_english_title():
    hints = window_hints_for_launch("notepad.exe")
    assert "Notepad" in hints
