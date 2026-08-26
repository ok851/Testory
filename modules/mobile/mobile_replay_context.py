# -*- coding: utf-8 -*-
"""移动端回放上下文推断与步骤清洗（与设备端 ReplayContextHelper 对齐）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_SKIP_PACKAGE_FRAGMENTS = (
    "com.testory.assistant",
    "com.android.systemui",
    "com.android.launcher",
    "com.android.launcher3",
    "com.google.android.apps.nexuslauncher",
    "com.miui.home",
    "com.huawei.android.launcher",
    "com.oppo.launcher",
    "com.bbk.launcher2",
    "com.sec.android.app.launcher",
    "com.oneplus.launcher",
)


def _mobile_spec(step: Dict[str, Any]) -> Dict[str, Any]:
    spec = step.get("mobile_spec")
    return spec if isinstance(spec, dict) else {}


def is_skippable_package(pkg: str) -> bool:
    p = (pkg or "").strip()
    if not p:
        return True
    lower = p.lower()
    for s in _SKIP_PACKAGE_FRAGMENTS:
        if lower == s:
            return True
    return "launcher" in lower or lower.endswith(".home")


def is_coordinate_step(step: Dict[str, Any]) -> bool:
    action = (step.get("action") or "").strip().lower()
    if action == "swipe":
        return True
    if action not in ("tap", "click"):
        return False
    st = (step.get("selector_type") or "").strip()
    if st == "viewport_coord":
        return True
    spec = _mobile_spec(step)
    return isinstance(spec.get("viewport_coord"), dict)


def extract_context_package(step: Dict[str, Any]) -> str:
    spec = _mobile_spec(step)
    pkg = str(spec.get("context_package") or spec.get("app_package") or spec.get("appPackage") or "").strip()
    return pkg


def open_app_package(step: Dict[str, Any]) -> str:
    pkg = str(step.get("input_value") or "").strip()
    if not pkg:
        spec = _mobile_spec(step)
        pkg = str(spec.get("app_package") or spec.get("appPackage") or "").strip()
    return pkg


def should_skip_open_app_step(step: Dict[str, Any]) -> bool:
    if (step.get("action") or "").strip().lower() != "open_app":
        return False
    pkg = open_app_package(step)
    if not pkg or is_skippable_package(pkg):
        return True
    spec = _mobile_spec(step)
    return bool(spec.get("auto_app_switch"))


def sanitize_replay_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤助手 UI 噪声步骤；跳过启动器类 open_app。"""
    out: List[Dict[str, Any]] = []
    for raw in steps:
        step = dict(raw or {})
        action = (step.get("action") or "").strip().lower()
        if action == "open_app" and should_skip_open_app_step(step):
            continue
        out.append(step)
    return out


def infer_prepare_context(steps: List[Dict[str, Any]]) -> Tuple[Optional[str], bool]:
    """
    推断回放前是否需软恢复上下文。
    返回 (package, required)；required=False 表示失败也不应阻断坐标类流程。
    """
    for step in steps:
        action = (step.get("action") or "").strip().lower()
        if action == "open_app":
            pkg = open_app_package(step)
            if pkg and not is_skippable_package(pkg):
                return pkg, True
            continue
        if action in ("press_home", "home", "press_back", "back"):
            return None, False
        pkg = extract_context_package(step)
        if pkg and not is_skippable_package(pkg):
            return pkg, not is_coordinate_step(step)
        break
    return None, False
