# -*- coding: utf-8 -*-
"""
桌面嵌入式 UI（CEF / Electron / WebView2）启动挂钩。

用户通常没有被测应用源码，无法改程序开启 remote debugging。
正确做法：由 Testory 在启动被测进程时注入 Chromium/WebView2 参数，无需源码：

1. --force-renderer-accessibility
   让 Chromium 把内部控件暴露给 Windows UIA（捕获器主路径，不依赖 CDP）
2. --remote-debugging-port=<port>
   额外打开 DevTools 协议，供 DOM 级精确定位（embed_cdp）
3. WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS（环境变量）
   微软官方支持的 WebView2 附加浏览器参数，对大量 WinForms/WPF/ERP 壳有效

可通过 DESKTOP_EMBED_HOOKS=0 关闭；端口可用 DESKTOP_EMBED_CDP_PORT 固定。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

_lock = threading.Lock()
_active_port: int = 0

_CHROMIUM_FLAG_MARKERS = (
    "--force-renderer-accessibility",
    "--remote-debugging-port",
    "--enable-features=AccessibilityObjectModel",
)


def embed_hooks_enabled() -> bool:
    # 默认关闭：打开应用=点击即可，捕获不依赖特殊启动参数
    raw = (os.environ.get("DESKTOP_EMBED_HOOKS") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def get_active_embed_cdp_port() -> int:
    with _lock:
        if _active_port > 0:
            return int(_active_port)
    try:
        return int(os.environ.get("DESKTOP_EMBED_CDP_PORT") or 0)
    except ValueError:
        return 0


def _pick_free_port() -> int:
    fixed = (os.environ.get("DESKTOP_EMBED_CDP_PORT") or "").strip()
    if fixed.isdigit() and int(fixed) > 0:
        return int(fixed)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def allocate_embed_debug_port() -> int:
    global _active_port
    port = _pick_free_port()
    with _lock:
        _active_port = port
    os.environ["DESKTOP_EMBED_CDP_PORT"] = str(port)
    return port


def chromium_embed_flags(port: Optional[int] = None) -> List[str]:
    """注入到 exe 命令行的 Chromium 开关（Electron/CEF/Chrome 族常见可透传）。"""
    p = int(port or allocate_embed_debug_port())
    return [
        "--force-renderer-accessibility",
        f"--remote-debugging-port={p}",
        "--remote-allow-origins=*",
    ]


def webview2_additional_browser_arguments(port: Optional[int] = None) -> str:
    """WebView2 官方环境变量值。"""
    return " ".join(chromium_embed_flags(port))


def _args_already_hooked(args: List[str]) -> bool:
    joined = " ".join(str(a) for a in args).lower()
    return any(m in joined for m in ("--force-renderer-accessibility", "--remote-debugging-port="))


def merge_embed_args(args: Optional[List[str]] = None, *, port: Optional[int] = None) -> Tuple[List[str], int]:
    base = [str(a) for a in (args or [])]
    if not embed_hooks_enabled():
        return base, get_active_embed_cdp_port()
    p = int(port or allocate_embed_debug_port())
    if _args_already_hooked(base):
        return base, p
    return base + chromium_embed_flags(p), p


def merge_embed_env(env: Optional[Dict[str, str]] = None, *, port: Optional[int] = None) -> Tuple[Dict[str, str], int]:
    out = dict(os.environ)
    if env:
        out.update({str(k): str(v) for k, v in env.items()})
    if not embed_hooks_enabled():
        return out, get_active_embed_cdp_port()
    p = int(port or allocate_embed_debug_port())
    flags = webview2_additional_browser_arguments(p)
    existing = (out.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS") or "").strip()
    if "--force-renderer-accessibility" not in existing.lower():
        out["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            f"{existing} {flags}".strip() if existing else flags
        )
    out["DESKTOP_EMBED_CDP_PORT"] = str(p)
    # 部分 Electron 构建会读这些
    out.setdefault("ELECTRON_EXTRA_LAUNCH_ARGS", flags)
    return out, p


def prepare_embed_launch(
    path: str,
    args: Optional[List[str]] = None,
    *,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """准备带嵌入式捕获挂钩的启动参数。"""
    merged_args, port = merge_embed_args(args)
    merged_env, port2 = merge_embed_env(env, port=port or None)
    port = port or port2
    return {
        "path": path,
        "args": merged_args,
        "env": merged_env,
        "cdp_port": int(port),
        "hooks_enabled": embed_hooks_enabled(),
        "flags": chromium_embed_flags(port) if embed_hooks_enabled() else [],
    }


def popen_with_embed_hooks(
    path: str,
    args: Optional[List[str]] = None,
    *,
    cwd: Optional[str] = None,
) -> Tuple[subprocess.Popen, Dict[str, Any]]:
    """
    用挂钩启动进程。注意：不要用 os.startfile，否则无法传参/环境变量。
    """
    prep = prepare_embed_launch(path, args)
    cmd = [prep["path"]] + list(prep["args"])
    proc = subprocess.Popen(
        cmd,
        shell=False,
        cwd=cwd or None,
        env=prep["env"],
    )
    return proc, prep


def resolve_exe_path_from_hwnd(hwnd: int) -> str:
    """从窗口句柄解析进程 exe 完整路径（无需源码）。"""
    if sys.platform != "win32" or not hwnd:
        return ""
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    if not pid.value:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(32768)
        # QueryFullProcessImageNameW
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return (buf.value or "").strip()
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    return ""


def relaunch_path_with_embed_hooks(
    path: str,
    *,
    extra_args: Optional[List[str]] = None,
    terminate_pids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    关闭旧进程（可选）后，用嵌入式挂钩重新启动同一 exe。
    用于：用户已从开始菜单打开应用 → 一键重启以启用内部元素捕获。
    """
    path = (path or "").strip().strip('"')
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": f"找不到可执行文件: {path}"}

    if terminate_pids:
        for pid in terminate_pids:
            try:
                if sys.platform == "win32" and int(pid) > 0:
                    subprocess.run(
                        ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                        capture_output=True,
                        timeout=8,
                        check=False,
                    )
            except Exception:
                pass

    try:
        proc, prep = popen_with_embed_hooks(path, extra_args)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": path}

    return {
        "ok": True,
        "path": path,
        "pid": int(proc.pid or 0),
        "cdp_port": prep.get("cdp_port"),
        "hooks_enabled": prep.get("hooks_enabled"),
        "message": (
            "已用 Testory 挂钩重新启动应用："
            "内部控件将暴露给 UIA，并尝试打开调试端口供精确定位。"
            "无需被测应用源码。"
        ),
    }


def relaunch_foreground_with_embed_hooks(*, kill: bool = True) -> Dict[str, Any]:
    """对当前前台窗口对应进程：解析 exe →（可选结束）→ 带挂钩重启。"""
    if sys.platform != "win32":
        return {"ok": False, "error": "仅支持 Windows"}
    import ctypes
    from ctypes import wintypes

    hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    if not hwnd:
        return {"ok": False, "error": "无前台窗口"}
    path = resolve_exe_path_from_hwnd(hwnd)
    if not path:
        return {"ok": False, "error": "无法解析前台进程路径"}

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    pids = [int(pid.value)] if kill and pid.value else []
    result = relaunch_path_with_embed_hooks(path, terminate_pids=pids)
    result["previous_hwnd"] = hwnd
    result["previous_pid"] = int(pid.value or 0)
    return result


def user_facing_embed_hint(*, hooks_tried: bool = False, cdp_ok: bool = False) -> str:
    if cdp_ok:
        return "已捕获应用内部元素"
    return "已按屏幕点击位置捕获（视觉/OCR）；打开应用后直接点选即可，无需特殊启动"
