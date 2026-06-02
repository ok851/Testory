# -*- coding: utf-8 -*-
"""Windows 窗口 / 任务栏品牌图标（pywebview 默认显示 pythonw 图标）。"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Optional

APP_USER_MODEL_ID = "Testory.Desktop.1"
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
LR_LOADFROMFILE = 0x0010
GCL_HICON = -14
GCL_HICONSM = -34


def resolve_icon_path(root: Path) -> Optional[Path]:
    for rel in ("Testory.ico", "static/brand/app.ico", "packaging/inno/testory.ico"):
        p = root / rel
        if p.is_file():
            return p
    return None


def set_process_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _find_windows_by_title(title: str) -> list[int]:
    import ctypes

    user32 = ctypes.windll.user32
    handles: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if title in buf.value:
            handles.append(int(hwnd))
        return True

    user32.EnumWindows(_enum, 0)
    return handles


def apply_icon_to_hwnd(hwnd: int, ico_path: Path) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    path = str(ico_path.resolve())
    for size in (32, 16, 48, 256):
        hicon = user32.LoadImageW(None, path, 1, size, size, LR_LOADFROMFILE)
        if not hicon:
            continue
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
        try:
            user32.SetClassLongPtrW(hwnd, GCL_HICON, hicon)
            user32.SetClassLongPtrW(hwnd, GCL_HICONSM, hicon)
        except Exception:
            pass


def apply_window_icon_async(root: Path, title: str = "Testory", timeout_sec: float = 180.0) -> None:
    """后台线程：窗口出现后持续刷新标题栏/任务栏图标（避免二次启动回退为 Python 图标）。"""
    ico = resolve_icon_path(root)
    if not ico or sys.platform != "win32":
        return

    def _worker() -> None:
        set_process_app_user_model_id()
        deadline = time.time() + timeout_sec
        seen: set[int] = set()
        while time.time() < deadline:
            for hwnd in _find_windows_by_title(title):
                if hwnd not in seen or (time.time() % 4) < 0.5:
                    apply_icon_to_hwnd(hwnd, ico)
                    seen.add(hwnd)
            time.sleep(1.0)

    threading.Thread(target=_worker, daemon=True).start()
