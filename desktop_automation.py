# -*- coding: utf-8 -*-
"""
Windows 桌面视觉自动化引擎（单路径：ORB 匹配 + SendInput）。
与 Playwright 并行：通过 step_executor 按 automation_layer 分发。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from desktop_env_config import (
    desktop_execution_mode,
    desktop_operation_timeout,
    prepare_desktop_step,
    remote_desktop_enabled,
    validate_launch_app_ready,
)
from desktop_input import (
    get_foreground_hwnd,
    infer_effect_keyword,
    sendinput_pointer_at_screen,
    sendinput_type_text,
    should_verify_desktop_effect,
    wait_for_desktop_effect,
)
from desktop_runtime import (
    desktop_runtime_available,
    desktop_runtime_unavailable_reason,
    parse_desktop_spec,
)
from desktop_visual_engine import (
    VisualMatchFailed,
    assert_visual_desktop_step,
    build_visual_failure_artifact_png,
    capture_virtual_desktop_png,
    is_legacy_desktop_step,
    resolve_visual_click_point,
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

_POINTER_ACTIONS = frozenset({"click", "double_click", "right_click"})

# 仅桌面存在的动作：即使 automation_layer 误存为 web 也必须走桌面执行器
_DESKTOP_ONLY_ACTIONS = frozenset({
    "launch_app",
    "attach_window",
    "hotkey",
    "screenshot",
})


def normalize_automation_layer(step: Dict[str, Any]) -> str:
    action = (step.get("action") or "").strip()
    if action in _DESKTOP_ONLY_ACTIONS:
        return "desktop"
    st = (step.get("selector_type") or "").strip().lower()
    if st == "visual":
        return "desktop"
    layer = (step.get("automation_layer") or "").strip().lower()
    if layer in ("web", "desktop"):
        return layer
    if action in _DESKTOP_ACTIONS or action in ("fill",):
        return "desktop"
    return "web"


def validate_step_for_layer(action: str, layer: str) -> Optional[str]:
    act = (action or "").strip()
    if not act:
        return "步骤 action 不能为空"
    if layer == "desktop":
        if act in _WEB_ONLY_ACTIONS:
            return f"桌面步骤不允许 Web 专用动作：{act}"
        if act not in _DESKTOP_ACTIONS and act not in ("fill",):
            return f"不支持的桌面动作：{act}"
        if act in _POINTER_ACTIONS.union({"input", "fill", "verify", "assert"}):
            return None
    elif layer == "web" and act in ("launch_app", "attach_window"):
        return f"Web 步骤不允许桌面专用动作：{act}，请将自动化层切换为「桌面」"
    return None


class DesktopAutomation:
    """单会话桌面视觉自动化。"""

    def __init__(self):
        self._screenshot_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static", "desktop_screenshots"
        )
        os.makedirs(self._screenshot_dir, exist_ok=True)
        self._last_match_score: float = 0.0
        self._last_coords: Tuple[int, int] = (0, 0)

    def reset_session(self) -> None:
        self._last_match_score = 0.0
        self._last_coords = (0, 0)

    @property
    def has_window(self) -> bool:
        return False

    def _capture_failure_screenshot(self, step: Dict[str, Any]) -> Optional[str]:
        if os.environ.get("DESKTOP_FAILURE_SCREENSHOT", "1").strip().lower() in (
            "0",
            "false",
            "no",
            "off",
        ):
            return None
        try:
            payload = assert_visual_desktop_step(step)
            png = build_visual_failure_artifact_png(payload)
            fname = f"desktop_fail_{int(time.time() * 1000)}.png"
            out_path = os.path.join(self._screenshot_dir, fname)
            with open(out_path, "wb") as f:
                f.write(png)
            return f"/static/desktop_screenshots/{fname}"
        except Exception:
            return None

    def _run_visual_pointer(self, step: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self._perform_visual_step(step)
        except VisualMatchFailed as exc:
            shot = getattr(exc, "failure_screenshot", None) or self._capture_failure_screenshot(
                step
            )
            raise VisualMatchFailed(
                f"{exc}；请使用「自学习」在当前屏幕点击正确位置更新模板",
                failure_screenshot=shot,
                selector_value=(step.get("selector_value") or "").strip() or None,
                need_relearn=getattr(exc, "need_relearn", False),
                best_score=getattr(exc, "best_score", 0.0),
            ) from exc

    def _perform_visual_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        from desktop_hybrid_locator import (
            _effect_keyword_from_step,
            resolve_desktop_click_point,
        )

        action = (step.get("action") or "click").strip().lower()
        result = resolve_desktop_click_point(step)
        x, y, score = result.x, result.y, result.score
        self._last_match_score = score
        self._last_coords = (x, y)
        uat_logger.info(
            "桌面指针步骤: action=%s via=%s score=%.3f @ (%s,%s)",
            action,
            result.resolved_via,
            float(score),
            x,
            y,
        )
        fg_before = get_foreground_hwnd()
        from desktop_input import _enum_visible_windows

        spec = parse_desktop_spec(step.get("desktop_spec"))
        verify_effect = action in _POINTER_ACTIONS and should_verify_desktop_effect(spec)
        titles_before = {t for _h, t, _c in _enum_visible_windows() if t} if verify_effect else set()
        hwnds_before = {h for h, _t, _c in _enum_visible_windows()} if verify_effect else set()

        if result.resolved_via == "shell_com":
            from desktop_shell_application import execute_shell_application_action

            com_target = execute_shell_application_action(
                step, action, target=result.shell_com_target
            )
            uat_logger.info(
                "桌面 Shell COM: action=%s icon=%s matched=%s",
                action,
                com_target.icon_name,
                com_target.matched_name,
            )
        elif result.resolved_via == "shell_listview":
            from desktop_shell_listview import execute_shell_listview_action

            target = execute_shell_listview_action(
                step, action, target=result.shell_target
            )
            x, y = target.screen_x, target.screen_y
            uat_logger.info(
                "桌面 ListView 后台消息: action=%s icon=%s index=%s @ client(%s,%s) screen(%s,%s)",
                action,
                target.icon_name,
                target.index,
                target.client_x,
                target.client_y,
                x,
                y,
            )
        else:
            sendinput_pointer_at_screen(x, y, action, step=step)
        out: Dict[str, Any] = {
            "status": "success",
            "action": action,
            "verified": True,
            "pointer_executed": True,
            "match_score": score,
            "coords": f"{x},{y}",
            "selector_type": "visual",
            "resolved_via": result.resolved_via,
        }
        if verify_effect:
            keyword = _effect_keyword_from_step(step) or infer_effect_keyword(
                spec, (step.get("description") or "")
            )
            desktop_shell = bool(
                spec.get("desktop_shell")
                or keyword
                or "listitem" in (step.get("description") or "").lower()
            )
            timeout = float(spec.get("effect_timeout") or 8.0)
            if not wait_for_desktop_effect(
                keyword,
                fg_before=fg_before,
                timeout=timeout,
                desktop_shell=desktop_shell,
                titles_before=titles_before,
                hwnds_before=hwnds_before,
            ):
                raise RuntimeError(
                    f"桌面操作后未检测到预期界面变化（关键词={keyword or '无'}，"
                    f"点击 ({x},{y})，定位={result.resolved_via} score={score:.3f}）。"
                    "可能点错位置或目标应用未响应，请重新捕获或开启 DESKTOP_PHYSICAL_MOUSE=1 试物理点击"
                )
            out["effect_verified"] = True
            out["effect_keyword"] = keyword
        return out

    def _launch_application(self, step: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
        err = validate_launch_app_ready(step)
        if err:
            raise ValueError(err)
        path = (
            (step.get("input_value") or "")
            or spec.get("path")
            or spec.get("exe")
            or ""
        ).strip()
        if not path:
            raise ValueError("launch_app 缺少 input_value 或 desktop_spec.path")
        try:
            from desktop_env_config import smart_resolve_launch_path

            path = smart_resolve_launch_path(path)
        except ImportError:
            pass
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen([path], shell=False)
        time.sleep(0.8)
        return {"status": "success", "action": "launch_app", "verified": True}

    def _send_hotkey(self, keys: str) -> None:
        """简易 pywinauto 风格热键：^c !{F4} %{TAB}。"""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        VK = {
            "ctrl": 0x11,
            "shift": 0x10,
            "alt": 0x12,
            "enter": 0x0D,
            "tab": 0x09,
            "esc": 0x1B,
        }

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]

        def _key(vk: int, down: bool) -> None:
            inp = INPUT()
            inp.type = 1
            inp.ki = KEYBDINPUT(vk, 0, 0 if down else 0x0002, 0, None)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        mods: List[int] = []
        seq = (keys or "").strip()
        i = 0
        while i < len(seq):
            ch = seq[i]
            if ch == "^":
                mods.append(VK["ctrl"])
                i += 1
                continue
            if ch == "+":
                mods.append(VK["shift"])
                i += 1
                continue
            if ch == "%":
                mods.append(VK["alt"])
                i += 1
                continue
            if ch == "{":
                j = seq.find("}", i)
                name = seq[i + 1 : j].strip().upper() if j > i else ""
                vk = VK.get(name.lower(), 0)
                if not vk and len(name) == 1:
                    vk = ord(name.upper())
                for m in mods:
                    _key(m, True)
                if vk:
                    _key(vk, True)
                    _key(vk, False)
                for m in reversed(mods):
                    _key(m, False)
                mods = []
                i = j + 1 if j > i else i + 1
                continue
            vk = ord(ch.upper())
            for m in mods:
                _key(m, True)
            _key(vk, True)
            _key(vk, False)
            for m in reversed(mods):
                _key(m, False)
            mods = []
            i += 1

    def execute_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        if not desktop_runtime_available():
            raise RuntimeError(
                desktop_runtime_unavailable_reason()
                or "桌面自动化不可用（需 Windows + opencv-python + mss）"
            )

        step = prepare_desktop_step(step)
        action = (step.get("action") or "").strip()
        spec = parse_desktop_spec(step.get("desktop_spec"))
        input_value = step.get("input_value") or ""
        compare_type = (step.get("compare_type") or "equals").strip().lower()

        if is_legacy_desktop_step(step) and action in _POINTER_ACTIONS.union(
            {"input", "fill", "verify", "assert"}
        ):
            raise RuntimeError(
                "该步骤使用已废弃的 UIA/坐标定位，请用「框选录制」重新捕获为 visual 步骤"
            )

        if action == "launch_app":
            return self._launch_application(step, spec)

        if action == "attach_window":
            return {"status": "success", "action": action, "verified": True}

        if action in _POINTER_ACTIONS:
            return self._run_visual_pointer(step)

        if action in ("input", "fill"):
            if not str(input_value):
                raise ValueError("桌面输入步骤缺少 input_value")
            self._run_visual_pointer({**step, "action": "click"})
            time.sleep(0.15)
            sendinput_type_text(str(input_value))
            return {
                "status": "success",
                "action": action,
                "verified": True,
                "pointer_executed": True,
            }

        if action == "hotkey":
            keys = (input_value or "").strip()
            if not keys:
                raise ValueError("hotkey 需要 input_value，如 ^c 或 %{F4}")
            self._send_hotkey(keys)
            return {"status": "success", "action": action, "verified": True}

        if action == "wait":
            mode = (compare_type or "fixed").strip().lower()
            if mode == "window" or spec.get("wait_for") == "window":
                title = spec.get("window_title") or input_value or ""
                timeout = float(spec.get("timeout", 30))
                deadline = time.time() + timeout
                found = False
                while time.time() < deadline:
                    if self._find_window_by_title(title):
                        found = True
                        break
                    time.sleep(0.3)
                if not found:
                    raise TimeoutError(f"等待窗口超时：{title}")
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
            return {"status": "success", "action": action, "verified": True}

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
            st = (step.get("selector_type") or "").strip().lower()
            if st != "visual":
                raise RuntimeError("verify 步骤需使用 visual 框选录制")
            self._run_visual_pointer({**step, "action": "click"})
            vt = (
                (input_value or "").strip()
                or (compare_type or "").strip()
                or "auto"
            ).lower()
            if vt not in ("auto", "slider", "image", "visible", "exist", "clickable"):
                return {
                    "status": "success",
                    "action": action,
                    "verified": True,
                    "message": f"视觉点击已完成（{vt}）",
                }
            x, y = self._last_coords
            from desktop_captcha import run_desktop_verify_at_point

            return run_desktop_verify_at_point(x, y, vt)

        if action == "assert":
            raise RuntimeError(
                "桌面 assert 已废弃 UIA 文本比对，请改用 Web 断言或 visual verify"
            )

        raise ValueError(f"未实现的桌面动作：{action}")

    @staticmethod
    def _find_window_by_title(title_sub: str) -> bool:
        if not title_sub:
            return False
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = False

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            nonlocal found
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            if title_sub in (buff.value or ""):
                found = True
                return False
            return True

        user32.EnumWindows(_cb, 0)
        return found

    def inspect_uia_tree(self, max_depth: int = 4, max_nodes: int = 120) -> List[Dict[str, Any]]:
        raise RuntimeError("UIA 探测已移除，请使用 visual 框选录制")


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
    return []


def sync_desktop_verify_element(
    selector_type: str,
    selector_value: str,
    desktop_spec: Any = None,
    locator_candidates: Any = None,
) -> Dict[str, Any]:
    if not desktop_runtime_available():
        return {
            "success": False,
            "error": desktop_runtime_unavailable_reason() or "桌面视觉自动化不可用",
        }
    st = (selector_type or "").strip().lower()
    if st != "visual":
        return {
            "success": False,
            "error": "仅支持 visual 步骤校验，请重新框选录制",
        }
    try:
        from desktop_hybrid_locator import (
            element_snapshot_for_step,
            resolve_desktop_click_point,
        )
        from desktop_uia_snapshot import resolve_uia_click_point

        step = {
            "selector_type": "visual",
            "selector_value": selector_value,
            "automation_layer": "desktop",
            "action": "click",
        }
        if locator_candidates:
            step["locator_candidates"] = locator_candidates
        if desktop_spec:
            step["desktop_spec"] = desktop_spec

        uia_hint = ""
        snap = element_snapshot_for_step(step)
        if snap:
            uia = resolve_uia_click_point(snap, timeout_sec=3.0)
            if uia.ok:
                return {
                    "success": True,
                    "resolved_via": "uia",
                    "match_score": float(uia.score),
                    "coords": f"{uia.x},{uia.y}",
                    "message": (
                        f"结构定位成功 (uia) score={float(uia.score):.3f} "
                        f"@ {uia.x},{uia.y}"
                    ),
                }
            code = (uia.error_code or "").strip()
            msg = (uia.message or "").strip()
            uia_hint = msg or code or "未命中"
            if code:
                uia_hint = f"{code}" + (f" ({msg})" if msg and msg != code else "")

        try:
            from desktop_shell_application import (
                resolve_shell_application_icon,
                shell_com_enabled,
            )
            from desktop_shell_listview import (
                icon_name_from_step,
                is_desktop_listitem_step,
                resolve_shell_listview_icon,
                shell_message_enabled,
            )

            if is_desktop_listitem_step(step):
                name = icon_name_from_step(step)
                if name and shell_com_enabled():
                    com_preview = resolve_shell_application_icon(name)
                    if com_preview:
                        return {
                            "success": True,
                            "resolved_via": "shell_com",
                            "match_score": 1.0,
                            "coords": "0,0",
                            "message": (
                                f"桌面 Shell COM 可命中 icon={com_preview.icon_name} "
                                f"matched={com_preview.matched_name}"
                                + (f"；结构未命中（{uia_hint}）" if uia_hint else "")
                            ),
                        }
                if shell_message_enabled() and name:
                    preview = resolve_shell_listview_icon(name)
                    if preview:
                        return {
                            "success": True,
                            "resolved_via": "shell_listview",
                            "match_score": 1.0,
                            "coords": f"{preview.screen_x},{preview.screen_y}",
                            "message": (
                                f"桌面 ListView 可命中 (shell_listview) icon={preview.icon_name} "
                                f"index={preview.index} @ {preview.screen_x},{preview.screen_y}"
                                + (f"；结构未命中（{uia_hint}）" if uia_hint else "")
                            ),
                        }
        except ImportError:
            pass

        result = resolve_desktop_click_point(step)
        via = result.resolved_via or "visual"
        msg = (
            f"定位成功 ({via}) score={result.score:.3f} "
            f"@ {result.x},{result.y}"
        )
        if uia_hint and via != "uia":
            msg = f"结构定位未命中（{uia_hint}）；视觉兜底成功：{msg}"
        return {
            "success": True,
            "resolved_via": via,
            "match_score": result.score,
            "coords": f"{result.x},{result.y}",
            "message": msg,
            "uia_available": bool(snap),
            "uia_ok": False,
        }
    except Exception as exc:
        err = str(exc)
        if "视觉匹配失败" in err:
            err += (
                "。提示：请先显示桌面（Win+D）并关闭遮挡窗口后再校验；"
                "桌面图标建议保持 hybrid_capture 且校验显示 uia 成功"
            )
        out: Dict[str, Any] = {"success": False, "error": err}
        if getattr(exc, "need_relearn", False):
            out["need_relearn"] = True
            out["best_score"] = getattr(exc, "best_score", 0.0)
        return out


def sync_desktop_attach_from_spec(desktop_spec: Dict[str, Any]) -> None:
    return None


def sync_reset_desktop_automation() -> None:
    try:
        w = _get_worker()
        w.execute(w.automation.reset_session, timeout=5)
    except Exception:
        pass
