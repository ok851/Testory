# -*- coding: utf-8 -*-
"""阶段2：树级结果校验 —— 回放时按录制 verification 快照复核 UIA 树节点。

验证：节点消失→默认告警不阻断；strict=True→TREE_VERIFY_FAILED；校验通过→ok；
无 verification/取树失败→原样返回（静默降级）。
"""
from unittest.mock import patch

from modules.execution.step_executor import _apply_tree_verification

_MOBILE_ANCHOR = {
    "layer": "android",
    "candidates": [{"type": "id", "value": "com.example:id/login_btn", "score": 0.95}],
    "node": {"resource_id": "com.example:id/login_btn", "text": "登录"},
    "tree_fingerprint": "abc123",
}

_STEP = {
    "action": "tap",
    "automation_layer": "android",
    "selector_type": "id",
    "selector_value": "com.example:id/login_btn",
    "uia_anchor": _MOBILE_ANCHOR,
    "verification": {"found": True, "matched_via": "id", "node_state": {"text": "登录"}},
}

_OK_RESULT = {"ok": True, "status": "success"}


def test_node_missing_warns_but_not_blocking():
    with patch(
        "modules.mobile.mobile_cross_end_tools._verify_after_action",
        return_value={"found": False, "matched_via": "", "tree_fingerprint": "x"},
    ):
        out = _apply_tree_verification(dict(_STEP), dict(_OK_RESULT))
    assert out.get("tree_verify") == "warn"
    assert "树级校验未通过" in (out.get("warning") or "")
    assert out.get("status") == "success"  # 不阻断


def test_strict_mode_fails():
    step = dict(_STEP)
    step["verification"] = {**_STEP["verification"], "strict": True}
    with patch(
        "modules.mobile.mobile_cross_end_tools._verify_after_action",
        return_value={"found": False, "matched_via": "", "tree_fingerprint": "x"},
    ):
        out = _apply_tree_verification(step, dict(_OK_RESULT))
    assert out.get("error_code") == "TREE_VERIFY_FAILED"
    assert out.get("status") == "error"


def test_verification_ok():
    with patch(
        "modules.mobile.mobile_cross_end_tools._verify_after_action",
        return_value={"found": True, "matched_via": "id", "tree_fingerprint": "y"},
    ):
        out = _apply_tree_verification(dict(_STEP), dict(_OK_RESULT))
    assert out.get("tree_verify") == "ok"
    assert out.get("status") == "success"


def test_no_verification_returns_unchanged():
    step = {"action": "tap", "automation_layer": "android"}
    out = _apply_tree_verification(step, dict(_OK_RESULT))
    assert out == _OK_RESULT


def test_tree_failure_silently_degrades():
    # 取树抛异常/返回空 → 原样返回（静默降级，不阻塞回放）
    with patch(
        "modules.mobile.mobile_cross_end_tools._verify_after_action",
        return_value={},
    ):
        out = _apply_tree_verification(dict(_STEP), dict(_OK_RESULT))
    assert out == _OK_RESULT


def test_desktop_verification_path():
    step = {
        "action": "click",
        "automation_layer": "desktop",
        "verification": {"found": True, "matched_via": "automation_id"},
    }
    with patch(
        "modules.desktop.windows_desktop_tools._build_desktop_uia_anchor",
        return_value={"found": True, "candidates": [{"type": "automation_id"}]},
    ):
        out = _apply_tree_verification(step, {"ok": True, "x": 100, "y": 200})
    assert out.get("tree_verify") == "ok"
