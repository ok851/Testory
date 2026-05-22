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
    screen_click,
    screen_double_click,
    screen_right_click,
    should_verify_desktop_effect,
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

    def reset_session(self) -> None:
        """释放当前附着（停止执行或浏览器强制重置时调用）。"""
        self._app = None
        self._window = None
        self._screenshot_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static", "desktop_screenshots"
        )
        os.makedirs(self._screenshot_dir, exist_ok=True)

    @property
    def has_window(self) -> bool:
        return self._window is not None

    def _ensure_attached(self, spec: Dict[str, Any], action: str, selector_type: str) -> None:
        """步骤含 desktop_spec 时按需附着窗口，无需单独 attach_window 步骤。"""
        if self._window is not None:
            return
        st = (selector_type or "").strip().lower()
        if action in ("click", "double_click", "right_click") and st == "coordinate":
            return
        if not spec:
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
        if is_desktop_shell_spec(spec):
            self._app, self._window = attach_desktop_shell(spec)
        else:
            self._app, self._window = attach_application(spec)

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
            if uia_json:
                attempts.append(("uia_path", uia_json))
            if center:
                attempts.append(("__desktop_hit__", center))
            if st == "name" and sv:
                attempts.append(("name", sv))
            elif st == "coordinate" and sv:
                attempts.append(("coordinate", sv))
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
        self._assert_screen_coords(x, y)
        px, py = int(x), int(y)
        if is_desktop_shell_spec(spec or {}):
            hwnd = int((spec or {}).get("hwnd") or 0)
            focus_hwnd(hwnd)
        uat_logger.info("桌面屏幕点击: %s @ (%s, %s)", action, px, py)
        if action == "click":
            screen_click(px, py)
        elif action == "double_click":
            screen_double_click(px, py)
        elif action == "right_click":
            screen_right_click(px, py)
        else:
            raise ValueError(f"不支持的指针动作：{action}")

    def _verify_pointer_effect(
        self,
        action: str,
        spec: Dict[str, Any],
        description: str = "",
        *,
        fg_before: int = 0,
    ) -> None:
        """双击/打开类操作：未出现预期窗口则判失败，避免“代码跑完但界面无变化”仍报成功。"""
        if action != "double_click":
            return
        keyword = infer_effect_keyword(spec, description)
        if not keyword:
            return
        timeout = 8.0
        from desktop_input import wait_for_desktop_effect

        shell = is_desktop_shell_spec(spec)
        if not wait_for_desktop_effect(
            keyword,
            fg_before=fg_before,
            timeout=timeout,
            desktop_shell=shell,
            require_verify=should_verify_desktop_effect(spec),
        ):
            hint = (
                "资源管理器窗口（如「回收站」文件夹）"
                if shell
                else "相关应用窗口"
            )
            raise RuntimeError(
                f"已执行双击，但 {timeout:.0f}s 内未检测到与「{keyword}」相关的{hint}。"
                "请重新捕获该桌面图标，并确认手动双击可正常打开。"
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
                    self._verify_resolved_name(
                        ctrl, (spec.get("target_name") or ""), "name"
                    )
                    return ctrl
                if try_st == "coordinate":
                    x, y = _split_coordinate(try_sv)
                    self._assert_screen_coords(x, y)

                    class _CoordTarget:
                        def __init__(self, px: int, py: int):
                            self._x, self._y = px, py

                        def rectangle(self):
                            from types import SimpleNamespace

                            return SimpleNamespace(
                                left=self._x,
                                top=self._y,
                                right=self._x + 1,
                                bottom=self._y + 1,
                            )

                        def click_input(self, **kwargs):
                            self._screen_pointer_action("click", self._x, self._y)

                        def double_click_input(self, **kwargs):
                            self._screen_pointer_action(
                                "double_click", self._x, self._y
                            )

                        def right_click_input(self, **kwargs):
                            self._screen_pointer_action(
                                "right_click", self._x, self._y
                            )

                    return _CoordTarget(x, y)

                ctrl = resolve_control(
                    self._window, try_st, try_sv, spec, app=self._app
                )
                self._verify_resolved_name(ctrl, selector_value, try_st)
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
        try:
            ctrl.set_focus()
        except Exception:
            pass
        time.sleep(0.08)
        if action == "double_click" and self._is_uia_listitem(ctrl):
            icon_name = self._desktop_icon_name(ctrl, spec or {})
            # 桌面图标：中心物理双击（多数 RPA 默认）→ invoke → shell: 回退
            try:
                cx, cy = self._control_screen_center(ctrl)
                screen_double_click(cx, cy)
                time.sleep(0.2)
                return
            except Exception:
                pass
            try:
                ctrl.invoke()
                time.sleep(0.15)
                return
            except Exception:
                pass
            try:
                ctrl.double_click_input()
                return
            except Exception:
                pass
            if icon_name and shell_open_folder(icon_name):
                time.sleep(0.25)
                return
        if action == "click":
            ctrl.click_input()
        elif action == "double_click":
            ctrl.double_click_input()
        else:
            ctrl.right_click_input()

    def _perform_pointer_step(
        self,
        action: str,
        selector_type: str,
        selector_value: str,
        spec: Dict[str, Any],
        *,
        description: str = "",
    ) -> Dict[str, Any]:
        """点击类步骤：优先 UIA 精准路径命中 ListItem，再回退屏幕坐标。"""
        from desktop_input import get_foreground_hwnd

        resolved_via = ""
        click_x = click_y = 0
        fg_before = get_foreground_hwnd()
        st = (selector_type or "").strip().lower()
        if (
            st == "coordinate"
            and selector_value
            and not is_desktop_shell_spec(spec)
        ):
            click_x, click_y = _split_coordinate(selector_value)
            self._screen_pointer_action(action, click_x, click_y, spec)
            resolved_via = "coordinate"
        else:
            ctrl = self._resolve_step_control(
                selector_type, selector_value, spec
            )
            if self._is_uia_listitem(ctrl):
                self._invoke_control_pointer(ctrl, action, spec=spec)
                click_x, click_y = self._control_screen_center(ctrl)
                resolved_via = "uia_listitem"
            elif is_desktop_shell_spec(spec) or not hasattr(ctrl, "element_info"):
                click_x, click_y = self._control_screen_center(ctrl)
                self._screen_pointer_action(action, click_x, click_y, spec)
                resolved_via = "screen_coords"
            else:
                self._invoke_control_pointer(ctrl, action, spec=spec)
                try:
                    click_x, click_y = self._control_screen_center(ctrl)
                except Exception:
                    pass
                resolved_via = (selector_type or "control").strip().lower()
        self._verify_pointer_effect(
            action, spec, description, fg_before=fg_before
        )
        uat_logger.info(
            "桌面指针操作完成: %s via %s @ (%s,%s)",
            action,
            resolved_via or "unknown",
            click_x,
            click_y,
        )
        return {
            "status": "success",
            "action": action,
            "resolved_via": resolved_via,
            "coords": f"{click_x},{click_y}" if click_x or click_y else "",
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
            self._app, self._window = attach_application(spec)
            return {"status": "success", "action": action}

        if action == "attach_window":
            self._app, self._window = attach_application(spec)
            return {"status": "success", "action": action}

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
        self.task_queue.put((task_id, func, args, kwargs))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                rid, result, err = self.result_queue.get(timeout=0.5)
                if rid == task_id:
                    if err:
                        raise err
                    return result
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
