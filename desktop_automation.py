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
from typing import Any, Dict, List, Optional

from desktop_locator import (
    attach_application,
    desktop_runtime_available,
    parse_desktop_spec,
    resolve_control,
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
            if self._window is None:
                raise RuntimeError("未附着桌面窗口，请先执行 launch_app 或 attach_window")
            if selector_type == "coordinate" and selector_value:
                x, y = [int(float(p)) for p in selector_value.replace(";", ",").split(",")[:2]]
                self._window.set_focus()
                if action == "click":
                    self._window.click_input(coords=(x, y))
                elif action == "double_click":
                    import pywinauto.mouse as mouse  # type: ignore

                    mouse.double_click(coords=(x, y))
                else:
                    import pywinauto.mouse as mouse  # type: ignore

                    mouse.right_click(coords=(x, y))
            else:
                ctrl = resolve_control(
                    self._window, selector_type, selector_value, spec
                )
                if action == "click":
                    ctrl.click_input()
                elif action == "double_click":
                    ctrl.double_click_input()
                else:
                    ctrl.right_click_input()
            return {"status": "success", "action": action}

        if action in ("input", "fill"):
            if not str(input_value):
                raise ValueError("桌面输入步骤缺少 input_value")
            if selector_value:
                ctrl = resolve_control(
                    self._window, selector_type, selector_value, spec
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
            self._window.set_focus()
            self._window.type_keys(keys, set_foreground=True)
            return {"status": "success", "action": action}

        if action == "wait":
            mode = (compare_type or "fixed").strip().lower()
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
                        resolve_control(
                            self._window, selector_type, selector_value, spec
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
                )
            expected = str(input_value or "")
            if selector_value:
                ctrl = resolve_control(
                    self._window, selector_type, selector_value, spec
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
            expected = str(input_value or "")
            if selector_value:
                ctrl = resolve_control(
                    self._window, selector_type, selector_value, spec
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
