# -*- coding: utf-8 -*-
"""
通过 ADB 直接操控设备（无需 Appium 会话即可投屏/点击/滑动）。
与 UiAutomator2 / Airtest 思路类似：底层仍走 adb + input 事件。
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, Optional, Tuple

from mobile_device_manager import adb_path

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


def _adb_shell(udid: str, *args: str, timeout: int = 15) -> Tuple[int, str, str]:
    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    cmd.extend(["shell", *args])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as exc:
        return 1, "", str(exc)


def adb_tap(udid: str, x: int, y: int) -> Dict[str, Any]:
    code, out, err = _adb_shell(udid, "input", "tap", str(int(x)), str(int(y)))
    if code != 0:
        raise RuntimeError(err or out or "adb input tap 失败")
    return {"x": int(x), "y": int(y), "via": "adb"}


def adb_swipe(
    udid: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int = 300,
) -> Dict[str, Any]:
    code, out, err = _adb_shell(
        udid,
        "input",
        "swipe",
        str(int(x1)),
        str(int(y1)),
        str(int(x2)),
        str(int(y2)),
        str(max(50, int(duration_ms))),
    )
    if code != 0:
        raise RuntimeError(err or out or "adb input swipe 失败")
    return {
        "start": (int(x1), int(y1)),
        "end": (int(x2), int(y2)),
        "duration_ms": duration_ms,
        "via": "adb",
    }


def adb_keyevent(udid: str, keycode: int) -> None:
    code, out, err = _adb_shell(udid, "input", "keyevent", str(int(keycode)))
    if code != 0:
        raise RuntimeError(err or out or f"keyevent {keycode} 失败")


def adb_press_home(udid: str) -> None:
    adb_keyevent(udid, 3)


def adb_press_back(udid: str) -> None:
    adb_keyevent(udid, 4)


def adb_input_text(udid: str, text: str) -> None:
    """输入 ASCII 友好文本；中文等建议走 Appium 或剪贴板扩展。"""
    safe = (text or "").replace(" ", "%s")
    if not safe:
        return
    code, out, err = _adb_shell(udid, "input", "text", safe)
    if code != 0:
        raise RuntimeError(err or out or "adb input text 失败")


def try_uiautomator2_tap(udid: str, x: int, y: int) -> Optional[Dict[str, Any]]:
    """若已安装 uiautomator2，可选用其点击（自动处理部分机型）。"""
    try:
        import uiautomator2 as u2  # type: ignore

        serial = udid or None
        d = u2.connect(serial)
        d.click(int(x), int(y))
        return {"x": int(x), "y": int(y), "via": "uiautomator2"}
    except ImportError:
        return None
    except Exception as exc:
        uat_logger.debug("uiautomator2 tap 失败，回退 adb: %s", exc)
        return None


def smart_tap(udid: str, x: int, y: int, *, prefer_u2: bool = True) -> Dict[str, Any]:
    if prefer_u2:
        u2r = try_uiautomator2_tap(udid, x, y)
        if u2r:
            return u2r
    return adb_tap(udid, x, y)


def parse_wm_size(output: str) -> Tuple[int, int]:
    """解析 `wm size` 输出 Physical size: 1080x2400。"""
    text = output or ""
    m = re.search(r"Physical size:\s*(\d+)x(\d+)", text, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"Override size:\s*(\d+)x(\d+)", text, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{3,5})x(\d{3,5})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1080, 1920
