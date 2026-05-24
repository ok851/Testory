# -*- coding: utf-8 -*-
"""
Windows 桌面 UI 自动化引擎（pywinauto）。
与 Playwright 并行：通过 step_executor 按 automation_layer 分发。
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from desktop_input import (
    focus_hwnd,
    infer_effect_keyword,
    physical_mouse_enabled,
    pointer_action_at_screen,
    screen_click,
    screen_double_click,
    screen_right_click,
    should_verify_desktop_effect,
    steal_focus_enabled,
    wait_for_desktop_effect,
)
from desktop_locator import (
    attach_application,
    attach_desktop_shell,
    desktop_listitem_at_screen_point,
    desktop_runtime_available,
    is_desktop_shell_spec,
    parse_desktop_spec,
    resolve_control,
    resolve_desktop_icon_at_point,
    shell_open_folder,
    _split_coordinate,
)
from desktop_env_config import (
    desktop_execution_mode,
    desktop_operation_timeout,
    prepare_desktop_step,
    remote_desktop_enabled,
    validate_launch_app_ready,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

_DESKTOP_ACTIONS = frozenset({
    "launch_app",
    "attach_window",
    "click",
    "double_click",
    "right_click",
    "input",
    "hotkey",
    "wait",
    "screenshot",
    "assert",
    "verify",
})

_WEB_ONLY_ACTIONS = frozenset({
    "navigate",
    "enter_iframe",
    "exit_iframe",
    "scroll",
    "swipe",
    "batch_input",
    "extract_text",
    "text_compare",
    "extract_json",
    "select",
    "date",
    "submit",
    "keypress",
    "wait_for_selector",
    "wait_for_element_visible",
    "api_request",
})


def normalize_automation_layer(step: Dict[str, Any]) -> str:
    layer = (step.get("automation_layer") or "web").strip().lower()
    return layer if layer in ("web", "desktop") else "web"


def validate_step_for_layer(action: str, layer: str) -> Optional[str]:
    act = (action or "").strip()
    if not act:
        return "步骤 action 不能为空"
    if layer == "desktop":
        if act in _WEB_ONLY_ACTIONS:
            return f"桌面步骤不允许 Web 专用动作：{act}"
        if act not in _DESKTOP_ACTIONS and act not in ("fill",):
            return f"不支持的桌面动作：{act}"
    elif layer == "web" and act in ("launch_app", "attach_window"):
        return f"Web 步骤不允许桌面专用动作：{act}，请将自动化层切换为「桌面」"
    return None


class DesktopAutomation:
    """单会话桌面自动化状态。"""

    def __init__(self):
        self._app: Any = None
        self._window: Any = None
        self._attach_fp: Optional[Tuple[Any, ...]] = None
        self._screenshot_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static", "desktop_screenshots"
        )
        os.makedirs(self._screenshot_dir, exist_ok=True)

    def reset_session(self) -> None:
        """释放当前附着（停止执行或浏览器强制重置时调用）。"""
        self._app = None
        self._window = None
        self._attach_fp = None

    @property
    def has_window(self) -> bool:
        return self._window is not None

    @staticmethod
    def _attach_fingerprint(spec: Dict[str, Any]) -> Tuple[Any, ...]:
        s = spec or {}
        return (
            int(s.get("hwnd") or 0),
            int(s.get("pid") or 0),
            (s.get("process") or "").strip().lower(),
            (s.get("window_title_re") or s.get("window_title") or "").strip(),
            (s.get("surface") or "").strip().lower(),
        )

    def _activate_attached_window(self, spec: Dict[str, Any]) -> None:
        """默认不抢焦点；仅 DESKTOP_STEAL_FOCUS=1 时置前目标窗口。"""
        if not steal_focus_enabled():
            return
        hwnd = int((spec or {}).get("hwnd") or 0)
        if not hwnd and self._window is not None:
            try:
                hwnd = int(getattr(self._window, "handle", 0) or 0)
            except Exception:
                hwnd = 0
        if hwnd:
            focus_hwnd(hwnd)
            time.sleep(0.12)
            return
        if self._window is not None:
            try:
                self._window.set_focus()
                time.sleep(0.08)
            except Exception:
                pass

    @staticmethod
    def _control_root_hwnd(ctrl: Any) -> int:
        import ctypes

        raw = int(
            getattr(getattr(ctrl, "element_info", None), "handle", 0)
            or getattr(ctrl, "handle", 0)
            or 0
        )
        if not raw:
            return 0
        return int(ctypes.windll.user32.GetAncestor(raw, 2) or raw)

    def _assert_control_on_target(self, ctrl: Any, spec: Dict[str, Any]) -> None:
        """确保解析到的控件属于步骤指定的目标窗口，避免点到当前屏幕其它区域。"""
        if is_desktop_shell_spec(spec):
            return
        spec_hwnd = int(spec.get("hwnd") or 0)
        if not spec_hwnd:
            return
        root = self._control_root_hwnd(ctrl)
        if root and root != spec_hwnd:
            raise RuntimeError(
                f"控件不在目标窗口内（期望 hwnd={spec_hwnd}，实际顶层 hwnd={root}）。"
                "请确认目标应用未关闭，且步骤 desktop_spec 与捕获时一致。"
            )

    @staticmethod
    def _assert_control_actionable(ctrl: Any) -> None:
        if type(ctrl).__name__ == "_CoordTarget":
            return
        try:
            rect = ctrl.rectangle()
        except Exception as exc:
            raise RuntimeError(f"无法读取控件位置: {exc}") from exc
        w = int(rect.right) - int(rect.left)
        h = int(rect.bottom) - int(rect.top)
        if w < 2 or h < 2:
            raise RuntimeError("目标控件尺寸无效或不可见")
        if hasattr(ctrl, "is_enabled") and not ctrl.is_enabled():
            raise RuntimeError("目标控件当前不可用（disabled）")

    def _ensure_attached(self, spec: Dict[str, Any], action: str, selector_type: str) -> None:
        """步骤含 desktop_spec 时按需附着/切换窗口（同进程内 hwnd 变化会重新附着）。"""
        st = (selector_type or "").strip().lower()
        if action in ("click", "double_click", "right_click") and st == "coordinate":
            return
        if not spec:
            if self._window is not None:
                return
            raise RuntimeError(
                "未附着桌面窗口：请在步骤中保存 desktop_spec（hwnd/窗口标题），"
                "或使用 launch_app / attach_window"
            )
        if not (
            spec.get("hwnd")
            or spec.get("pid")
            or spec.get("process")
            or spec.get("path")
            or spec.get("exe")
            or spec.get("window_title")
            or spec.get("window_title_re")
            or spec.get("cmd_line")
        ):
            raise RuntimeError(
                "未附着桌面窗口：desktop_spec 缺少 hwnd/process/窗口标题等字段，"
                "请重新捕获元素或填写 launch_app"
            )
        fp = self._attach_fingerprint(spec)
        if self._window is not None and self._attach_fp == fp:
            self._activate_attached_window(spec)
            return
        if is_desktop_shell_spec(spec):
            self._app, self._window = attach_desktop_shell(spec)
        else:
            self._app, self._window = attach_application(spec)
        self._attach_fp = fp
        self._activate_attached_window(spec)

    def _uia_path_json(self, spec: Dict[str, Any]) -> str:
        uia_nodes = spec.get("uia_path")
        if isinstance(uia_nodes, list) and uia_nodes:
            return json.dumps(uia_nodes, ensure_ascii=False)
        if isinstance(uia_nodes, str) and uia_nodes.strip():
            return uia_nodes.strip()
        return ""

    def _build_resolve_attempts(
        self,
        selector_type: str,
        selector_value: str,
        spec: Dict[str, Any],
    ) -> List[Tuple[str, str]]:
        """桌面图标层优先 UIA 精准路径（竞品方案），坐标仅作最后回退。"""
        st = (selector_type or "").strip().lower()
        sv = (selector_value or "").strip()
        center = (spec.get("pick_center") or "").strip()
        uia_json = self._uia_path_json(spec)
        attempts: List[Tuple[str, str]] = []

        if is_desktop_shell_spec(spec):
            if st == "coordinate" and sv:
                return [("coordinate", sv)]
            if uia_json:
                attempts.append(("uia_path", uia_json))
            if center:
                attempts.append(("__desktop_hit__", center))
            if st == "name" and sv:
                attempts.append(("name", sv))
            elif st not in ("uia_path", "__desktop_hit__") and sv:
                attempts.append((st, sv))
            if center and ("coordinate", center) not in attempts:
                attempts.append(("coordinate", center))
            return attempts

        if st == "coordinate" and sv:
            attempts.append((st, sv))
        elif st and sv:
            attempts.append((st, sv))
        if uia_json and ("uia_path", uia_json) not in attempts:
            attempts.append(("uia_path", uia_json))
        if center and st != "coordinate" and ("coordinate", center) not in attempts:
            attempts.append(("coordinate", center))
        return attempts

    @staticmethod
    def _control_screen_center(ctrl: Any) -> Tuple[int, int]:
        rect = ctrl.rectangle()
        return (
            int((rect.left + rect.right) / 2),
            int((rect.top + rect.bottom) / 2),
        )

    @staticmethod
    def _assert_screen_coords(x: int, y: int) -> None:
        if sys.platform != "win32":
            return
        import ctypes

        sw = int(ctypes.windll.user32.GetSystemMetrics(0))
        sh = int(ctypes.windll.user32.GetSystemMetrics(1))
        if not (0 <= x < sw and 0 <= y < sh):
            raise RuntimeError(
                f"点击坐标 ({x},{y}) 超出屏幕范围 {sw}x{sh}，请重新捕获元素"
            )

    @staticmethod
    def _verify_resolved_name(ctrl: Any, expected: str, selector_type: str) -> None:
        st = (selector_type or "").strip().lower()
        exp = (expected or "").strip()
        if st != "name" or not exp:
            return
        try:
            actual = (getattr(ctrl.element_info, "name", None) or "").strip()
        except Exception:
            actual = ""
        if not actual:
            raise RuntimeError(f"无法确认控件名称是否为「{exp}」")
        if exp not in actual and actual not in exp:
            raise RuntimeError(
                f"解析到的控件为「{actual}」，与期望「{exp}」不一致，将尝试其他定位方式"
            )

    def _screen_pointer_action(
        self,
        action: str,
        x: int,
        y: int,
        spec: Optional[Dict[str, Any]] = None,
    ) -> None:
        """非 UIA 控件时的屏幕消息点击（不移动用户鼠标）。"""
        self._assert_screen_coords(x, y)
        px, py = int(x), int(y)
        if physical_mouse_enabled():
            uat_logger.info("桌面指针: %s @ (%s, %s) [physical=1]", action, px, py)
            pointer_action_at_screen(px, py, action, force_physical=True)
            return
        uat_logger.info(
            "桌面指针: %s @ (%s, %s) [mode=message]", action, px, py
        )
        pointer_action_at_screen(px, py, action, force_physical=False)

    def _verify_pointer_effect(
        self,
        action: str,
        spec: Dict[str, Any],
        description: str = "",
        *,
        fg_before: int = 0,
        selector_type: str = "",
    ) -> None:
        """非桌面层步骤：可选校验窗口变化（桌面/坐标步骤不在此校验）。"""
        if action != "double_click":
            return
        from desktop_input import get_foreground_hwnd

        st = (selector_type or "").strip().lower()
        if st == "coordinate" or is_desktop_shell_spec(spec):
            return
        keyword = infer_effect_keyword(spec, description)
        shell = is_desktop_shell_spec(spec)
        require = should_verify_desktop_effect(spec)
        timeout = 8.0 if shell else 5.0
        if shell:
            if not wait_for_desktop_effect(
                keyword,
                fg_before=fg_before,
                timeout=timeout,
                desktop_shell=True,
                require_verify=True,
            ):
                hint = "资源管理器窗口（如「回收站」文件夹）"
                raise RuntimeError(
                    f"已执行双击，但 {timeout:.0f}s 内未检测到与「{keyword or '目标'}」相关的{hint}。"
                    "请重新捕获该桌面图标，并确认手动双击可正常打开。"
                )
            return
        if keyword and require:
            if not wait_for_desktop_effect(
                keyword,
                fg_before=fg_before,
                timeout=timeout,
                desktop_shell=False,
                require_verify=True,
            ):
                raise RuntimeError(
                    f"已执行双击，但 {timeout:.0f}s 内未检测到与「{keyword}」相关的窗口变化。"
                    "请确认目标应用已打开且元素可双击。"
                )
            return
        fg_after = get_foreground_hwnd()
        if fg_after and fg_after != fg_before:
            return
        raise RuntimeError(
            "已执行双击，但前台窗口未变化，目标可能未获得焦点或未响应。"
            "请重新捕获元素，并确认 desktop_spec 指向目标应用窗口（勿使用 explorer/WorkerW）。"
        )

    def _verify_pointer_step(
        self,
        action: str,
        spec: Dict[str, Any],
        description: str,
        *,
        ctrl: Any,
        fg_before: int,
        selector_type: str = "",
        click_x: int = 0,
        click_y: int = 0,
    ) -> None:
        """校验元素可执行：UIA 控件已解析；纯坐标回退则校验消息送达。"""
        self._assert_control_actionable(ctrl)
        act = (action or "click").strip().lower()
        if type(ctrl).__name__ == "_CoordTarget":
            from desktop_input import hwnd_at_screen_point

            if hwnd_at_screen_point(click_x, click_y):
                return
            raise RuntimeError(
                f"坐标 ({click_x},{click_y}) 下无有效窗口，无法发送点击消息。"
                "请重新捕获坐标并确认该位置未被完全遮挡。"
            )
        try:
            ctrl.rectangle()
        except Exception as exc:
            raise RuntimeError(f"无法确认目标控件仍有效: {exc}") from exc
        if act == "double_click":
            self._verify_pointer_effect(
                action,
                spec,
                description,
                fg_before=fg_before,
                selector_type=selector_type,
            )
            return
        if act in ("click", "right_click"):
            if is_desktop_shell_spec(spec):
                return
            spec_hwnd = int(spec.get("hwnd") or 0)
            root = self._control_root_hwnd(ctrl)
            if spec_hwnd and root and root != spec_hwnd:
                raise RuntimeError(
                    f"执行后控件窗口校验失败（期望 hwnd={spec_hwnd}，实际 {root}）"
                )
            if steal_focus_enabled() and spec_hwnd:
                from desktop_input import get_foreground_hwnd

                fg = get_foreground_hwnd()
                if fg and fg != spec_hwnd and fg == fg_before:
                    uat_logger.warning(
                        "点击后前台未切换到目标窗口（未开启抢焦点时属正常）"
                    )

    def _resolve_step_control(
        self,
        selector_type: str,
        selector_value: str,
        spec: Dict[str, Any],
    ) -> Any:
        """解析控件；按场景排序多种定位策略。"""
        last_err: Optional[Exception] = None
        for try_st, try_sv in self._build_resolve_attempts(
            selector_type, selector_value, spec
        ):
            if not try_sv and try_st != "coordinate":
                continue
            try:
                if try_st == "__desktop_hit__":
                    x, y = _split_coordinate(try_sv)
                    self._assert_screen_coords(x, y)
                    ctrl = resolve_desktop_icon_at_point(
                        x, y, self._window, spec, self._app
                    )
                    self._last_resolved_via = "desktop_hit"
                    self._verify_resolved_name(
                        ctrl, (spec.get("target_name") or ""), "name"
                    )
                    return ctrl
                if try_st == "coordinate":
                    x, y = _split_coordinate(try_sv)
                    self._assert_screen_coords(x, y)

                    class _CoordTarget:
                        def __init__(self, px: int, py: int, owner: "DesktopAutomation"):
                            self._x, self._y = px, py
                            self._owner = owner

                        def rectangle(self):
                            from types import SimpleNamespace

                            return SimpleNamespace(
                                left=self._x,
                                top=self._y,
                                right=self._x + 1,
                                bottom=self._y + 1,
                            )

                        def _do(self, act: str) -> None:
                            self._owner._last_resolved_via = "coordinate"
                            self._owner._screen_pointer_action(
                                act, self._x, self._y
                            )

                        def click_input(self, **kwargs):
                            self._do("click")

                        def double_click_input(self, **kwargs):
                            self._do("double_click")

                        def right_click_input(self, **kwargs):
                            self._do("right_click")

                        def click(self, **kwargs):
                            self._do("click")

                        def double_click(self, **kwargs):
                            self._do("double_click")

                        def right_click(self, **kwargs):
                            self._do("right_click")

                    self._last_resolved_via = "coordinate"
                    return _CoordTarget(x, y, self)

                ctrl = resolve_control(
                    self._window, try_st, try_sv, spec, app=self._app
                )
                self._last_resolved_via = try_st
                self._verify_resolved_name(ctrl, selector_value, try_st)
                if try_st != "coordinate":
                    self._assert_control_actionable(ctrl)
                return ctrl
            except Exception as exc:
                last_err = exc
        if last_err:
            raise last_err
        raise RuntimeError("无法解析桌面控件：缺少有效的定位信息")

    @staticmethod
    def _is_uia_listitem(ctrl: Any) -> bool:
        try:
            ct = str(getattr(ctrl.element_info, "control_type", "") or "").lower()
            return "listitem" in ct
        except Exception:
            return False

    def _desktop_icon_name(self, ctrl: Any, spec: Dict[str, Any]) -> str:
        try:
            n = (getattr(ctrl.element_info, "name", None) or "").strip()
            if n:
                return n
        except Exception:
            pass
        return infer_effect_keyword(spec, "")

    def _invoke_control_pointer(
        self, ctrl: Any, action: str, *, spec: Optional[Dict[str, Any]] = None
    ) -> None:
        """默认 UIA 消息点击（不移动用户鼠标）；物理鼠标仅 DESKTOP_PHYSICAL_MOUSE=1。"""
        spec = spec or {}
        act = (action or "click").strip().lower()
        if steal_focus_enabled():
            try:
                ctrl.set_focus()
            except Exception:
                pass
            time.sleep(0.05)

        if act == "double_click" and self._is_uia_listitem(ctrl) and is_desktop_shell_spec(spec):
            icon_name = self._desktop_icon_name(ctrl, spec)
            skip_shell = icon_name in ("FolderView", "桌面", "Desktop", "桌面 1", "")
            if icon_name and not skip_shell and shell_open_folder(icon_name):
                time.sleep(0.25)
                return
            for method_name in ("invoke", "double_click", "double_click_input"):
                if method_name.endswith("_input") and not physical_mouse_enabled():
                    continue
                try:
                    getattr(ctrl, method_name)()
                    time.sleep(0.15)
                    return
                except Exception:
                    continue
            cx, cy = self._control_screen_center(ctrl)
            dbl = act == "double_click"
            right = act == "right_click"
            from desktop_input import message_click_at_screen

            message_click_at_screen(
                cx, cy, double=dbl, right=right
            )
            return

        if physical_mouse_enabled():
            if act == "click":
                ctrl.click_input()
            elif act == "double_click":
                ctrl.double_click_input()
            else:
                ctrl.right_click_input()
            return

        if act == "click":
            ctrl.click()
        elif act == "double_click":
            ctrl.double_click()
        elif act == "right_click":
            ctrl.right_click()
        else:
            raise ValueError(f"不支持的指针动作：{act}")

    def _perform_pointer_step(
        self,
        action: str,
        selector_type: str,
        selector_value: str,
        spec: Dict[str, Any],
        *,
        description: str = "",
    ) -> Dict[str, Any]:
        """点击类步骤：必须在目标窗口内实时解析控件，禁止盲用旧屏幕坐标。"""
        from desktop_input import get_foreground_hwnd

        fg_before = get_foreground_hwnd()
        shell = is_desktop_shell_spec(spec)
        self._last_resolved_via = (selector_type or "uia").strip().lower()
        ctrl = self._resolve_step_control(selector_type, selector_value, spec)
        self._assert_control_on_target(ctrl, spec)
        self._assert_control_actionable(ctrl)
        click_x, click_y = self._control_screen_center(ctrl)
        self._invoke_control_pointer(ctrl, action, spec=spec)
        resolved_via = (
            getattr(self, "_last_resolved_via", None)
            or (selector_type or "uia").strip().lower()
        )
        verified = False

        try:
            self._verify_pointer_step(
                action,
                spec,
                description,
                ctrl=ctrl,
                fg_before=fg_before,
                selector_type=selector_type,
                click_x=click_x,
                click_y=click_y,
            )
            verified = True
        except RuntimeError:
            if (
                (action or "").strip().lower() == "double_click"
                and shell
                and (selector_type or "").strip().lower() != "coordinate"
            ):
                keyword = infer_effect_keyword(spec, description)
                if keyword and shell_open_folder(keyword):
                    time.sleep(0.5)
                    if wait_for_desktop_effect(
                        keyword,
                        fg_before=fg_before,
                        timeout=6.0,
                        desktop_shell=True,
                        require_verify=True,
                    ):
                        uat_logger.info("桌面双击 Shell 回退打开: %s", keyword)
                        return {
                            "status": "success",
                            "action": action,
                            "resolved_via": "shell_open_folder",
                            "coords": "",
                            "verified": True,
                        }
            raise
        uat_logger.info(
            "桌面指针操作完成: %s via %s @ (%s,%s) verified=%s",
            action,
            resolved_via,
            click_x,
            click_y,
            verified,
        )
        return {
            "status": "success",
            "action": action,
            "resolved_via": resolved_via,
            "coords": f"{click_x},{click_y}",
            "verified": verified,
            "pointer_executed": verified,
        }

    def execute_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        if not desktop_runtime_available():
            raise RuntimeError(
                "桌面自动化仅支持 Windows，且需安装 pywinauto、pyperclip（见 requirements-windows.txt）"
            )

        step = prepare_desktop_step(step)
        action = (step.get("action") or "").strip()
        spec = parse_desktop_spec(step.get("desktop_spec"))
        selector_type = step.get("selector_type") or "automation_id"
        selector_value = step.get("selector_value") or ""
        input_value = step.get("input_value") or ""
        compare_type = (step.get("compare_type") or "equals").strip().lower()

        if action == "launch_app":
            err = validate_launch_app_ready(step)
            if err:
                raise ValueError(err)
            path = (input_value or spec.get("path") or spec.get("exe") or "").strip()
            spec = {**spec, "path": path}
            self._attach_fp = None
            self._app, self._window = attach_application(spec)
            self._attach_fp = self._attach_fingerprint(spec)
            self._activate_attached_window(spec)
            return {"status": "success", "action": action, "verified": True}

        if action == "attach_window":
            self._attach_fp = None
            self._app, self._window = attach_application(spec)
            self._attach_fp = self._attach_fingerprint(spec)
            self._activate_attached_window(spec)
            return {"status": "success", "action": action, "verified": True}

        if action in ("click", "double_click", "right_click"):
            self._ensure_attached(spec, action, selector_type)
            return self._perform_pointer_step(
                action,
                selector_type,
                selector_value,
                spec,
                description=(step.get("description") or ""),
            )

        if action in ("input", "fill"):
            if not str(input_value):
                raise ValueError("桌面输入步骤缺少 input_value")
            self._ensure_attached(spec, action, selector_type)
            if selector_value:
                ctrl = self._resolve_step_control(
                    selector_type, selector_value, spec
                )
                ctrl.set_focus()
                try:
                    ctrl.set_edit_text(str(input_value))
                except Exception:
                    ctrl.type_keys(str(input_value), with_spaces=True, set_foreground=True)
            else:
                self._window.set_focus()
                self._window.type_keys(
                    str(input_value), with_spaces=True, set_foreground=True
                )
            return {"status": "success", "action": action}

        if action == "hotkey":
            keys = (input_value or "").strip()
            if not keys:
                raise ValueError("hotkey 需要 input_value，如 ^c 或 %{F4}")
            self._ensure_attached(spec, action, selector_type)
            self._window.set_focus()
            self._window.type_keys(keys, set_foreground=True)
            return {"status": "success", "action": action}

        if action == "wait":
            mode = (compare_type or "fixed").strip().lower()
            if mode == "control" and selector_value:
                self._ensure_attached(spec, action, selector_type)
            if mode == "window" or spec.get("wait_for") == "window":
                title_re = spec.get("window_title_re") or input_value or ".*"
                timeout = float(spec.get("timeout", 30))
                from pywinauto import Application  # type: ignore

                be = spec.get("backend") or "uia"
                Application(backend=be).connect(
                    title_re=title_re, timeout=int(timeout)
                )
            elif mode == "control" and selector_value:
                timeout = float(spec.get("timeout", 30))
                deadline = time.time() + timeout
                last_err = None
                while time.time() < deadline:
                    try:
                        self._resolve_step_control(
                            selector_type, selector_value, spec
                        )
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        time.sleep(0.3)
                if last_err:
                    raise last_err
            else:
                sec = 1.0
                try:
                    sec = float(input_value or "1")
                except ValueError:
                    pass
                if sec <= 120:
                    time.sleep(sec)
                else:
                    time.sleep(sec / 1000.0)
            return {"status": "success", "action": action}

        if action == "screenshot":
            import mss  # type: ignore
            import mss.tools  # type: ignore

            fname = f"desktop_{int(time.time() * 1000)}.png"
            out_path = os.path.join(self._screenshot_dir, fname)
            region = spec.get("region")
            with mss.mss() as sct:
                if region and isinstance(region, (list, tuple)) and len(region) == 4:
                    mon = {
                        "left": int(region[0]),
                        "top": int(region[1]),
                        "width": int(region[2]),
                        "height": int(region[3]),
                    }
                    shot = sct.grab(mon)
                else:
                    shot = sct.grab(sct.monitors[1])
                mss.tools.to_png(shot.rgb, shot.size, output=out_path)
            rel = f"/static/desktop_screenshots/{fname}"
            return {"status": "success", "action": action, "screenshot": rel}

        if action == "verify":
            self._ensure_attached(spec, action, selector_type)
            vt = (
                (input_value or "").strip()
                or (compare_type or "").strip()
                or "auto"
            ).lower()
            if vt in ("auto", "slider", "image", "visible", "exist", "clickable"):
                from desktop_captcha import run_desktop_verify

                return run_desktop_verify(
                    self._window,
                    selector_type,
                    selector_value,
                    spec,
                    vt,
                    app=self._app,
                )
            expected = str(input_value or "")
            if selector_value:
                ctrl = self._resolve_step_control(
                    selector_type, selector_value, spec
                )
                try:
                    actual = ctrl.window_text()
                except Exception:
                    actual = str(getattr(ctrl, "texts", lambda: [""])())
            else:
                actual = self._window.window_text()
            actual = actual or ""
            ct = compare_type or "contains"
            ok = False
            if ct in ("equals", "text_equals"):
                ok = actual == expected
            elif ct in ("contains", "text_contains"):
                ok = expected in actual
            else:
                ok = expected in actual
            if not ok:
                raise AssertionError(
                    f"桌面断言失败：期望「{expected}」({ct})，实际「{actual[:200]}」"
                )
            return {"status": "success", "action": action, "actual": actual}

        if action == "assert":
            self._ensure_attached(spec, action, selector_type)
            expected = str(input_value or "")
            if selector_value:
                ctrl = self._resolve_step_control(
                    selector_type, selector_value, spec
                )
                try:
                    actual = ctrl.window_text()
                except Exception:
                    actual = str(getattr(ctrl, "texts", lambda: [""])())
            else:
                actual = self._window.window_text()
            actual = actual or ""
            ct = compare_type or "contains"
            ok = False
            if ct in ("equals", "text_equals"):
                ok = actual == expected
            elif ct in ("contains", "text_contains"):
                ok = expected in actual
            else:
                ok = expected in actual
            if not ok:
                raise AssertionError(
                    f"桌面断言失败：期望「{expected}」({ct})，实际「{actual[:200]}」"
                )
            return {"status": "success", "action": action, "actual": actual}

        raise ValueError(f"未实现的桌面动作：{action}")

    def inspect_uia_tree(self, max_depth: int = 4, max_nodes: int = 120) -> List[Dict[str, Any]]:
        """返回当前窗口 UIA 树片段（供设计期探测）。"""
        if self._window is None:
            raise RuntimeError("请先 attach_window 或 launch_app")

        nodes: List[Dict[str, Any]] = []

        def walk(ctrl, depth: int) -> None:
            if len(nodes) >= max_nodes or depth > max_depth:
                return
            try:
                info = ctrl.element_info
                nodes.append(
                    {
                        "name": getattr(info, "name", "") or "",
                        "automation_id": getattr(info, "automation_id", "") or "",
                        "control_type": str(getattr(info, "control_type", "")),
                        "class_name": getattr(info, "class_name", "") or "",
                        "depth": depth,
                    }
                )
                for ch in ctrl.children():
                    walk(ch, depth + 1)
                    if len(nodes) >= max_nodes:
                        break
            except Exception:
                pass

        walk(self._window, 0)
        return nodes


class DesktopWorker:
    """专用线程执行桌面操作，避免与 Playwright 事件循环冲突。"""

    def __init__(self):
        self.task_queue: queue.Queue = queue.Queue()
        self.result_queue: queue.Queue = queue.Queue()
        self._orphan_results: Dict[str, Tuple[Any, Optional[Exception]]] = {}
        self._orphan_lock = threading.Lock()
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.automation = DesktopAutomation()
        self.thread.start()

    def _worker_loop(self) -> None:
        while True:
            task = self.task_queue.get()
            if task is None:
                break
            task_id, func, args, kwargs = task
            try:
                result = func(*args, **kwargs)
                self.result_queue.put((task_id, result, None))
            except Exception as e:
                self.result_queue.put((task_id, None, e))

    def execute(self, func, *args, timeout: float = 90, **kwargs):
        import uuid

        task_id = str(uuid.uuid4())
        with self._orphan_lock:
            orphan = self._orphan_results.pop(task_id, None)
        if orphan is not None:
            result, err = orphan
            if err:
                raise err
            return result
        self.task_queue.put((task_id, func, args, kwargs))
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._orphan_lock:
                orphan = self._orphan_results.pop(task_id, None)
            if orphan is not None:
                result, err = orphan
                if err:
                    raise err
                return result
            try:
                rid, result, err = self.result_queue.get(timeout=0.3)
                if rid == task_id:
                    if err:
                        raise err
                    return result
                with self._orphan_lock:
                    self._orphan_results[rid] = (result, err)
            except queue.Empty:
                continue
        raise TimeoutError("桌面自动化操作超时")


_worker: Optional[DesktopWorker] = None


def _get_worker() -> DesktopWorker:
    global _worker
    if _worker is None:
        _worker = DesktopWorker()
    return _worker


def _sync_desktop_execute_inprocess(step: Dict[str, Any]) -> Dict[str, Any]:
    w = _get_worker()
    action = (step.get("action") or "").strip()
    timeout = desktop_operation_timeout(action)
    return w.execute(w.automation.execute_step, step, timeout=timeout)


def _sync_desktop_execute_via_gateway(step: Dict[str, Any]) -> Dict[str, Any]:
    from desktop_agent_client import desktop_agent_enabled, remote_execute_step

    if not desktop_agent_enabled():
        raise RuntimeError(
            "DESKTOP_EXECUTION_MODE=gateway 但未配置 DESKTOP_AGENT_GATEWAY_URL / "
            "DESKTOP_AGENT_GATEWAY_SECRET，或网关未启动"
        )
    spec = parse_desktop_spec(step.get("desktop_spec"))
    sid = spec.get("agent_session_id") or spec.get("session_id")
    return remote_execute_step(step, session_id=sid)


def _sync_desktop_execute_remote(step: Dict[str, Any]) -> Dict[str, Any]:
    from desktop_agent_client import desktop_agent_enabled, remote_execute_step

    if not remote_desktop_enabled():
        raise RuntimeError(
            "远程桌面执行已禁用。本地版请使用 DESKTOP_EXECUTION_MODE=inprocess；"
            "企业多机场景请设置 DEPLOYMENT_PROFILE=enterprise"
        )
    if not desktop_agent_enabled():
        raise RuntimeError("DESKTOP_EXECUTION_MODE=remote 但未配置远程 Agent URL/密钥")
    spec = parse_desktop_spec(step.get("desktop_spec"))
    sid = spec.get("agent_session_id") or spec.get("session_id")
    return remote_execute_step(step, session_id=sid)


def sync_desktop_execute_step(step: Dict[str, Any]) -> Dict[str, Any]:
    step = prepare_desktop_step(step)
    if os.environ.get("DESKTOP_GATEWAY_INPROCESS", "").strip() in ("1", "true", "yes"):
        return _sync_desktop_execute_inprocess(step)

    mode = desktop_execution_mode()
    if mode == "inprocess":
        return _sync_desktop_execute_inprocess(step)
    if mode == "gateway":
        return _sync_desktop_execute_via_gateway(step)
    if mode == "remote":
        return _sync_desktop_execute_remote(step)
    return _sync_desktop_execute_inprocess(step)


def sync_desktop_inspect(max_depth: int = 4, max_nodes: int = 120) -> List[Dict[str, Any]]:
    w = _get_worker()
    return w.execute(
        w.automation.inspect_uia_tree, max_depth, max_nodes=max_nodes
    )


def sync_desktop_attach_from_spec(desktop_spec: Dict[str, Any]) -> None:
    w = _get_worker()

    def _attach():
        w.automation._app, w.automation._window = attach_application(desktop_spec)

    w.execute(_attach)


def sync_reset_desktop_automation() -> None:
    """停止用例或强制重置时清理桌面会话，避免对已退出进程调用 top_window。"""
    try:
        w = _get_worker()
        w.execute(w.automation.reset_session, timeout=5)
    except Exception:
        pass
