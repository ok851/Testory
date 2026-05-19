# -*- coding: utf-8 -*-
"""Windows 桌面控件定位（pywinauto UIA / Win32）。"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from desktop_discovery import format_resolve_error, resolve_executable, resolve_executable_with_meta
except ImportError:
    def resolve_executable(query: str) -> str:
        return (query or "").strip()

    def resolve_executable_with_meta(query: str):
        return None

    def format_resolve_error(meta) -> str:
        return "找不到可执行程序"

_DESKTOP_AVAILABLE = sys.platform == "win32"
if _DESKTOP_AVAILABLE:
    try:
        from pywinauto import Application  # type: ignore
        from pywinauto.findwindows import ElementNotFoundError  # type: ignore
    except ImportError:
        _DESKTOP_AVAILABLE = False
        Application = None  # type: ignore
        ElementNotFoundError = Exception  # type: ignore


def desktop_runtime_available() -> bool:
    return _DESKTOP_AVAILABLE


def parse_desktop_spec(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {}
    return {}


def _split_coordinate(value: str) -> Tuple[int, int]:
    parts = re.split(r"[,;\s]+", (value or "").strip())
    if len(parts) < 2:
        raise ValueError(f"坐标格式无效，应为 x,y：{value}")
    return int(float(parts[0])), int(float(parts[1]))


def resolve_control(
    window: Any,
    selector_type: str,
    selector_value: str,
    desktop_spec: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    在已附着窗口内解析控件。
    selector_type: automation_id | name | control_type | uia_path | coordinate
    """
    if window is None:
        raise RuntimeError("未附着桌面窗口，请先执行 attach_window 或 launch_app")

    st = (selector_type or "automation_id").strip().lower()
    sv = (selector_value or "").strip()
    spec = desktop_spec or {}

    if st == "coordinate":
        x, y = _split_coordinate(sv or spec.get("coordinate", ""))
        return window.click_input(coords=(x, y))

    if st == "uia_path":
        path = sv
        if path.startswith("["):
            nodes = json.loads(path)
        else:
            nodes = json.loads(path) if path else []
        ctrl = window
        for node in nodes:
            if not isinstance(node, dict):
                continue
            kwargs = {}
            if node.get("automation_id"):
                kwargs["auto_id"] = node["automation_id"]
            if node.get("name"):
                kwargs["title"] = node["name"]
            if node.get("control_type"):
                kwargs["control_type"] = node["control_type"]
            if not kwargs:
                continue
            ctrl = ctrl.child_window(**kwargs)
        return ctrl.wrapper_object() if hasattr(ctrl, "wrapper_object") else ctrl

    kwargs: Dict[str, Any] = {}
    if st in ("automation_id", "auto_id"):
        kwargs["auto_id"] = sv
    elif st == "name":
        kwargs["title"] = sv
    elif st == "control_type":
        kwargs["control_type"] = sv
    elif st == "class_name":
        kwargs["class_name"] = sv
    else:
        kwargs["title_re"] = sv

    best_match = spec.get("best_match", False)
    ctrl = window.child_window(best_match=bool(best_match), **kwargs)
    return ctrl.wrapper_object() if hasattr(ctrl, "wrapper_object") else ctrl


def _resolve_main_window(
    app: Any,
    *,
    timeout: int = 30,
    title_re: str = "",
) -> Any:
    """启动/连接后等待主窗口出现（避免进程已起但窗口未创建）。"""
    deadline = time.time() + max(1, int(timeout))
    last_err: Optional[Exception] = None
    tre = (title_re or "").strip()

    while time.time() < deadline:
        try:
            if tre:
                win = app.window(title_re=tre)
                win.wait("exists", timeout=2)
                return win.wrapper_object() if hasattr(win, "wrapper_object") else win
            win = app.top_window()
            try:
                win.wait("ready", timeout=2)
            except Exception:
                pass
            return win
        except RuntimeError as e:
            last_err = e
            if "No windows for that process" not in str(e):
                raise
        except Exception as e:
            last_err = e
        time.sleep(0.25)

    if last_err:
        raise RuntimeError(
            "应用进程已启动，但在限定时间内未找到可用窗口。"
            "若刚点击了「停止执行」，请重新运行用例；否则请检查程序名是否正确、"
            "或改用「附着窗口」+「选择当前窗口」。"
        ) from last_err
    raise RuntimeError("未能获取应用主窗口")


def attach_application(
    desktop_spec: Dict[str, Any],
    backend: Optional[str] = None,
) -> Tuple[Any, Any]:
    """
    附着或启动应用，返回 (Application, 主窗口 wrapper)。
    desktop_spec 支持: path, process, window_title, window_title_re, cmd_line, backend
    """
    if not _DESKTOP_AVAILABLE:
        raise RuntimeError(
            "桌面自动化不可用：请在 Windows 上安装 pywinauto（pip install pywinauto）"
        )

    be = (backend or desktop_spec.get("backend") or "uia").strip().lower()
    if be not in ("uia", "win32"):
        be = "uia"

    path = (desktop_spec.get("path") or desktop_spec.get("exe") or "").strip()
    process = (desktop_spec.get("process") or "").strip()
    title = (desktop_spec.get("window_title") or "").strip()
    title_re = (desktop_spec.get("window_title_re") or title or "").strip()
    cmd_line = (desktop_spec.get("cmd_line") or "").strip()
    timeout = int(desktop_spec.get("timeout", 25) or 25)
    window_wait = min(timeout, 25)

    app: Any = None
    if path or cmd_line:
        cmd = (cmd_line or path).strip()
        if not cmd_line and path:
            resolved = resolve_executable(path)
            if resolved:
                cmd = resolved
            elif os.path.isfile(path):
                cmd = path
            else:
                meta = resolve_executable_with_meta(path)
                raise FileNotFoundError(
                    format_resolve_error(meta)
                    if meta
                    else f"找不到可执行程序「{path}」"
                )
        app = Application(backend=be).start(cmd, timeout=min(timeout, 20))
    elif process:
        app = Application(backend=be).connect(path=process, timeout=timeout)
    elif title_re:
        app = Application(backend=be).connect(title_re=title_re, timeout=timeout)
    elif title:
        app = Application(backend=be).connect(title=title, timeout=timeout)
    else:
        raise ValueError("desktop_spec 需包含 path、process 或 window_title/window_title_re")

    win = _resolve_main_window(
        app, timeout=window_wait, title_re=title_re if title_re else ""
    )
    return app, win
