# -*- coding: utf-8 -*-
"""
Windows 桌面视觉自动化引擎（单路径：ORB 匹配 + SendInput）。
与 Playwright 并行：通过 step_executor 按 automation_layer 分发。
"""

from __future__ import annotations

import os
import queue
import re
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

_NATIVE_WINDOW_SELECTORS = frozenset({"window", "title", "hwnd", "process"})

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
    action = (step.get("action") or "").strip().lower()
    if action in _DESKTOP_ONLY_ACTIONS:
        return "desktop"
    st = (step.get("selector_type") or "").strip().lower()
    if st == "visual":
        return "desktop"
    layer = (step.get("automation_layer") or "").strip().lower()
    if layer in ("web", "desktop", "android"):
        return layer
    try:
        from mobile_automation import _MOBILE_ONLY_ACTIONS, normalize_mobile_action

        if normalize_mobile_action(action) in _MOBILE_ONLY_ACTIONS:
            return "android"
        if layer == "android":
            return "android"
    except ImportError:
        pass
    if action in _DESKTOP_ACTIONS or action in ("fill",):
        return "desktop"
    return "web"


def validate_step_for_layer(action: str, layer: str) -> Optional[str]:
    act = (action or "").strip()
    if not act:
        return "步骤 action 不能为空"
    if layer == "android":
        try:
            from mobile_automation import validate_step_for_mobile

            return validate_step_for_mobile(act)
        except ImportError:
            return "移动端模块未安装"
    if layer == "desktop":
        if act in _WEB_ONLY_ACTIONS:
            return f"桌面步骤不允许 Web 专用动作：{act}"
        if act not in _DESKTOP_ACTIONS and act not in ("fill",):
            return f"不支持的桌面动作：{act}"
        if act in _POINTER_ACTIONS.union({"input", "fill", "verify", "assert"}):
            return None
    elif layer == "web" and act in ("launch_app", "attach_window"):
        return f"Web 步骤不允许桌面专用动作：{act}，请将自动化层切换为「桌面」"
    elif layer == "web" and act in ("open_app", "close_app", "tap", "input_text"):
        return f"Web 步骤不允许 Android 专用动作：{act}，请将自动化层切换为「Android」"
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
        self._ensure_dwm_stable()
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
        verify_effect = action in _POINTER_ACTIONS and should_verify_desktop_effect(
            spec, action=action
        )
        titles_before = {t for _h, t, _c in _enum_visible_windows() if t} if verify_effect else set()
        hwnds_before = {h for h, _t, _c in _enum_visible_windows()} if verify_effect else set()

        pointer_executed = False
        if result.resolved_via == "shell_com":
            from desktop_shell_application import execute_shell_application_action

            com_target = execute_shell_application_action(
                step, action, target=result.shell_com_target
            )
            pointer_executed = True
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
            pointer_executed = True
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
            try:
                sendinput_pointer_at_screen(x, y, action, step=step)
                pointer_executed = True
            except Exception as exc:
                uat_logger.warning("桌面点击执行失败: %s", exc)
                pointer_executed = False

        min_score_for_verified = 0.5
        verified = score >= min_score_for_verified

        if not verified and result.resolved_via not in ("shell_com", "shell_listview"):
            uat_logger.warning(
                "桌面步骤低置信度: score=%.3f via=%s, 可能未命中目标",
                score,
                result.resolved_via,
            )

        # 定位命中且指针已发出 → 成功；低分未命中 → 失败（不能标成功）
        if not pointer_executed:
            raise RuntimeError(
                f"点击操作未成功执行，定位方式={result.resolved_via} score={score:.3f} @ ({x},{y})"
            )
        if not verified and result.resolved_via not in ("shell_com", "shell_listview"):
            raise RuntimeError(
                f"未准确定位到目标元素（score={score:.3f}，定位方式={result.resolved_via}）。"
                "请重新捕获目标元素确保定位准确"
            )

        out: Dict[str, Any] = {
            "status": "success",
            "action": action,
            "verified": verified,
            "pointer_executed": pointer_executed,
            "match_score": score,
            "coords": f"{x},{y}",
            "selector_type": "visual",
            "resolved_via": result.resolved_via,
        }

        if verify_effect:
            keyword = _effect_keyword_from_step(step) or infer_effect_keyword(
                spec, (step.get("description") or "")
            )
            # 仅显式 desktop_shell / 双击桌面图标类才走资源管理器启发式
            desktop_shell = bool(spec.get("desktop_shell")) or action in (
                "double_click",
                "doubleclick",
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
                diag = f"操作后未检测到预期界面变化"
                if keyword:
                    diag += f"（期望关键词={keyword}）"
                else:
                    diag += "（无关键词可匹配）"
                diag += (
                    f"，点击 ({x},{y})，定位方式={result.resolved_via}"
                    f" score={score:.3f}"
                )
                if result.resolved_via != "uia":
                    diag += "。请重新捕获目标元素确保定位准确"
                else:
                    diag += "。请检查目标应用是否正常响应点击"
                raise RuntimeError(diag)
            out["effect_verified"] = True
            out["effect_keyword"] = keyword
        self._recover_desktop_if_needed()
        return out

    def _launch_application(self, step: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
        err = validate_launch_app_ready(step)
        if err:
            raise ValueError(err)
        path = (
            (spec.get("path") or spec.get("exe") or "").strip()
            or (step.get("input_value") or "").strip()
        )
        if not path:
            raise ValueError("launch_app 缺少 input_value 或 desktop_spec.path")
        try:
            from desktop_env_config import smart_resolve_launch_path

            # 带 args 时 path 常为 python.exe，勿被 alias 改写
            if not (spec.get("args") or step.get("args")):
                path = smart_resolve_launch_path(path)
        except ImportError:
            pass

        raw_args = spec.get("args") if spec.get("args") is not None else step.get("args")
        args: List[str] = []
        if isinstance(raw_args, str) and raw_args.strip():
            args = [raw_args.strip()]
        elif isinstance(raw_args, (list, tuple)):
            args = [str(a) for a in raw_args if a is not None and str(a).strip() != ""]

        title_hint = ""
        for a in args:
            s = str(a).strip()
            if s.upper().startswith("ORD-") or s.upper().startswith("TESTORYERP"):
                title_hint = s
                break

        if args:
            try:
                from desktop_embed_launch import popen_with_embed_hooks

                _, prep = popen_with_embed_hooks(path, args)
                embed_meta = prep
            except Exception:
                subprocess.Popen([path] + args, shell=False)
                embed_meta = {}
        elif sys.platform == "win32":
            # 不用 os.startfile：无法注入 WebView2/Chromium 无障碍参数
            try:
                from desktop_embed_launch import popen_with_embed_hooks

                _, prep = popen_with_embed_hooks(path, [])
                embed_meta = prep
            except Exception:
                os.startfile(path)  # type: ignore[attr-defined]
                embed_meta = {}
        else:
            subprocess.Popen([path], shell=False)
            embed_meta = {}

        hwnd, win_title = self._find_hwnd_after_launch(title_hint or path)
        if hwnd:
            from desktop_input import focus_hwnd

            focus_hwnd(hwnd)
        out: Dict[str, Any] = {
            "status": "success",
            "action": "launch_app",
            "verified": True,
        }
        if hwnd:
            out["hwnd"] = int(hwnd)
        if win_title:
            out["window_title"] = win_title
        elif title_hint:
            out["window_title"] = title_hint
        if embed_meta.get("cdp_port"):
            out["embed_cdp_port"] = int(embed_meta["cdp_port"])
            out["embed_hooks"] = True
        return out

    def _find_hwnd_after_launch(self, launch_value: str, timeout: float = 10.0) -> tuple:
        from desktop_input import get_foreground_hwnd, resolve_hwnd_from_spec, _hwnd_title_class
        from desktop_run_context import window_hints_for_launch

        hints = window_hints_for_launch(launch_value)
        if not hints and launch_value:
            base = launch_value.replace("\\", "/").split("/")[-1]
            if base and "." in base:
                hints = [base.rsplit(".", 1)[0]]
            else:
                hints = [launch_value.strip()]
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            for hint in hints:
                hwnd = resolve_hwnd_from_spec({"window_title_re": f".*{re.escape(hint)}.*"})
                if hwnd:
                    title, _ = _hwnd_title_class(hwnd)
                    return int(hwnd), title or hint
            fg = get_foreground_hwnd()
            if fg:
                title, _ = _hwnd_title_class(fg)
                for hint in hints:
                    if hint.lower() in title.lower():
                        return int(fg), title
            time.sleep(0.35)
        return 0, ""

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
            return self._attach_window(step, spec)

        if action in _POINTER_ACTIONS:
            return self._run_visual_pointer(step)

        if action in ("input", "fill"):
            if not str(input_value):
                raise ValueError("桌面输入步骤缺少 input_value")
            # 热键/附着后的键盘输入：无 visual 模板时不要强行视觉点击（否则微信搜索必失败）
            st = (step.get("selector_type") or "").strip().lower()
            keyboard_only = bool(spec.get("keyboard_only")) or (
                (compare_type or "").strip().lower() in ("keyboard", "type", "paste")
            )
            has_visual = st == "visual" or bool(spec.get("template_path") or spec.get("template"))
            if has_visual and not keyboard_only:
                self._run_visual_pointer({**step, "action": "click"})
                time.sleep(0.15)
            sendinput_type_text(str(input_value))
            return {
                "status": "success",
                "action": action,
                "verified": True,
                "pointer_executed": bool(has_visual and not keyboard_only),
                "keyboard_only": bool(keyboard_only or not has_visual),
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
                    window_rect = self._try_get_window_rect_from_step(step)
                    if window_rect:
                        mon = {
                            "left": int(window_rect[0]),
                            "top": int(window_rect[1]),
                            "width": int(window_rect[2]),
                            "height": int(window_rect[3]),
                        }
                        shot = sct.grab(mon)
                    else:
                        shot = sct.grab(sct.monitors[1])
                mss.tools.to_png(shot.rgb, shot.size, output=out_path)
            rel = f"/static/desktop_screenshots/{fname}"
            return {"status": "success", "action": action, "screenshot": rel}

        if action == "verify":
            st = (step.get("selector_type") or "").strip().lower()
            if st in _NATIVE_WINDOW_SELECTORS or st == "":
                return self._verify_window_exists(step, spec, action="verify")
            if st != "visual":
                raise RuntimeError(
                    "verify 步骤需使用 selector_type=window（窗口存在校验）或 visual（框选录制）"
                )
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
            st = (step.get("selector_type") or "").strip().lower()
            ct = (compare_type or "").strip().lower()
            if st in _NATIVE_WINDOW_SELECTORS or st == "" or ct in (
                "element_exists",
                "element_visible",
                "text_contains",
                "text_equals",
                "",
            ):
                return self._verify_window_exists(step, spec, action="assert")
            raise RuntimeError(
                "桌面 assert 仅支持窗口级校验（selector_type=window）或 visual 步骤；"
                "控件文本断言请使用框选录制。"
            )

        raise ValueError(f"未实现的桌面动作：{action}")

    @staticmethod
    def _window_title_from_step(step: Dict[str, Any], spec: Dict[str, Any]) -> str:
        sv = (step.get("selector_value") or "").strip()
        iv = (step.get("input_value") or "").strip()
        _skip = frozenset({"exist", "visible", "clickable", "auto", "ok", "success"})
        if sv and sv.lower() not in _skip:
            return sv
        for key in ("title_contains", "window_title", "title"):
            v = (spec.get(key) or "").strip()
            if v:
                return v
        if iv and iv.lower() not in _skip:
            return iv
        return ""

    def _attach_window(self, step: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
        from desktop_input import get_foreground_hwnd, resolve_hwnd_from_spec, _hwnd_title_class
        from desktop_run_context import get_desktop_run_context, spec_has_window_target

        self._ensure_dwm_stable()

        ctx = get_desktop_run_context()
        hwnd = resolve_hwnd_from_spec(spec)
        if not hwnd and ctx.attached_hwnd:
            hwnd = int(ctx.attached_hwnd)
        if not hwnd and ctx.last_window_title_hint:
            hwnd = resolve_hwnd_from_spec(
                {"window_title_re": f".*{re.escape(ctx.last_window_title_hint)}.*"}
            )
        # 多语言 launch hints（如 Notepad / 记事本）逐一尝试
        if not hwnd and ctx.last_launch_value:
            from desktop_run_context import window_hints_for_launch

            for hint in window_hints_for_launch(ctx.last_launch_value):
                hwnd = resolve_hwnd_from_spec(
                    {"window_title_re": f"(?i).*{re.escape(hint)}.*"}
                )
                if hwnd:
                    break
        if not hwnd and not spec_has_window_target(spec):
            fg = get_foreground_hwnd()
            hint = self._window_title_from_step(step, spec) or ctx.last_window_title_hint
            if fg and hint:
                title, _ = _hwnd_title_class(fg)
                if hint.lower() in title.lower():
                    hwnd = fg
        if not hwnd:
            hint = self._window_title_from_step(step, spec) or ctx.last_window_title_hint
            if hint:
                msg = f"未找到目标窗口：{hint}"
            elif ctx.last_launch_value:
                msg = (
                    f"未找到目标窗口（已启动 {ctx.last_launch_value}，"
                    "请在 attach_window 填写窗口标题）"
                )
            else:
                msg = "未找到目标窗口（请先执行 launch_app 或在步骤中填写窗口标题）"
            raise RuntimeError(msg)
        import ctypes

        user32 = ctypes.windll.user32
        no_focus_steal = os.environ.get("DESKTOP_NO_FOCUS_STEAL", "").strip().lower() in (
            "1", "true", "yes", "on"
        )
        if not no_focus_steal:
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.1)
        else:
            user32.ShowWindow(hwnd, 9)
            time.sleep(0.1)
        title, _ = _hwnd_title_class(hwnd)
        self._ensure_dwm_stable()
        out = {
            "status": "success",
            "action": "attach_window",
            "hwnd": int(hwnd),
            "verified": True,
            "window_title": title,
        }
        store_as = str(step.get("store_as") or step.get("var_name") or "").strip()
        if store_as and title:
            out["extracted_text"] = title
            out["store_as"] = store_as
        return out

    def _verify_window_exists(
        self,
        step: Dict[str, Any],
        spec: Dict[str, Any],
        *,
        action: str = "verify",
    ) -> Dict[str, Any]:
        from desktop_input import resolve_hwnd_from_spec

        title = self._window_title_from_step(step, spec)
        if not title and not spec:
            raise ValueError(f"{action} 步骤缺少窗口标题（selector_value 或 desktop_spec.title_contains）")
        if spec and (spec.get("window_title_re") or spec.get("window_title") or spec.get("hwnd")):
            hwnd = resolve_hwnd_from_spec(spec)
            if hwnd:
                return {
                    "status": "success",
                    "action": action,
                    "verified": True,
                    "hwnd": int(hwnd),
                    "message": f"窗口已找到：{title or spec}",
                }
        if title and self._find_window_by_title(title):
            return {
                "status": "success",
                "action": action,
                "verified": True,
                "message": f"窗口已找到：{title}",
            }
        raise RuntimeError(f"窗口未找到：{title or '请填写窗口标题'}")

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

    @staticmethod
    def _ensure_dwm_stable() -> None:
        import ctypes

        try:
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            pass
        time.sleep(0.05)

    @staticmethod
    def _recover_desktop_if_needed() -> None:
        import ctypes

        try:
            hwnd = ctypes.windll.user32.FindWindowW("Progman", None)
            if not hwnd:
                hwnd = ctypes.windll.user32.FindWindowW("WorkerW", None)
            if hwnd:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0111, 0x7402, 0)
                time.sleep(0.15)
        except Exception:
            pass

    @staticmethod
    def _try_get_window_rect_from_step(step: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
        try:
            payload = assert_visual_desktop_step(step)
            snap = payload.element_snapshot or {}
            sel = snap.get("selector") or {}
            window_bounds = sel.get("window_bounds")
            if window_bounds and len(window_bounds) == 4:
                l, t, r, b = window_bounds
                if r > l and b > t:
                    return (int(l), int(t), int(r) - int(l), int(b) - int(t))
        except Exception:
            pass
        try:
            selector_value = (step.get("selector_value") or "").strip()
            if selector_value:
                import json
                data = json.loads(selector_value)
                snap = data.get("element_snapshot") or {}
                sel = snap.get("selector") or {}
                window_bounds = sel.get("window_bounds")
                if window_bounds and len(window_bounds) == 4:
                    l, t, r, b = window_bounds
                    if r > l and b > t:
                        return (int(l), int(t), int(r) - int(l), int(b) - int(t))
        except Exception:
            pass
        try:
            from desktop_input import hwnd_at_screen_point
            from desktop_win32_snapshot import get_window_rect, get_top_level_window

            payload = assert_visual_desktop_step(step)
            use_x = payload.search_anchor_x or 0
            use_y = payload.search_anchor_y or 0
            if use_x or use_y:
                hwnd = hwnd_at_screen_point(int(use_x), int(use_y))
                if hwnd:
                    top = get_top_level_window(hwnd)
                    rect = get_window_rect(top)
                    if rect:
                        l, t, r, b = rect
                        if r > l and b > t:
                            return (int(l), int(t), int(r) - int(l), int(b) - int(t))
        except ImportError:
            pass
        return None

    def inspect_uia_tree(self, max_depth: int = 4, max_nodes: int = 120) -> List[Dict[str, Any]]:
        try:
            from desktop_uia_core import dump_foreground_uia_tree

            return dump_foreground_uia_tree(
                max_depth=max(1, int(max_depth or 4)),
                max_nodes=max(10, int(max_nodes or 120)),
            )
        except Exception as exc:
            raise RuntimeError(f"UIA 探测失败: {exc}") from exc


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
    from desktop_run_context import (
        enrich_desktop_step_with_run_context,
        update_context_from_step_result,
    )

    step = prepare_desktop_step(step)
    step = enrich_desktop_step_with_run_context(step)
    if os.environ.get("DESKTOP_GATEWAY_INPROCESS", "").strip() in ("1", "true", "yes"):
        result = _sync_desktop_execute_inprocess(step)
        update_context_from_step_result(step, result)
        return result

    mode = desktop_execution_mode()
    if mode == "inprocess":
        result = _sync_desktop_execute_inprocess(step)
        update_context_from_step_result(step, result)
        return result
    if mode == "gateway":
        result = _sync_desktop_execute_via_gateway(step)
        update_context_from_step_result(step, result)
        return result
    if mode == "remote":
        result = _sync_desktop_execute_remote(step)
        update_context_from_step_result(step, result)
        return result
    result = _sync_desktop_execute_inprocess(step)
    update_context_from_step_result(step, result)
    return result


def sync_desktop_inspect(max_depth: int = 4, max_nodes: int = 120) -> List[Dict[str, Any]]:
    """导出前台窗口 UIA 树（调试/控件探测）。"""
    try:
        from desktop_uia_core import dump_foreground_uia_tree

        return dump_foreground_uia_tree(
            max_depth=max(1, int(max_depth or 4)),
            max_nodes=max(10, int(max_nodes or 120)),
        )
    except Exception:
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
        from desktop_run_context import reset_desktop_run_context

        reset_desktop_run_context()
    except Exception:
        pass
    try:
        w = _get_worker()
        w.execute(w.automation.reset_session, timeout=5)
    except Exception:
        pass
