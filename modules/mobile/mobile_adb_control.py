# -*- coding: utf-8 -*-
"""

与 UiAutomator2 / Airtest 思路类似：底层仍走 adb + input 事件。
"""

from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, Optional, Tuple, List

from modules.mobile.mobile_device_manager import adb_path

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


def adb_is_package_installed(udid: str, package: str) -> bool:
    pkg = (package or "").strip()
    if not pkg:
        return False
    code, out, _ = _adb_shell(udid, "pm", "path", pkg, timeout=10)
    return code == 0 and "package:" in (out or "")


def adb_get_foreground_package(udid: str) -> str:
    """解析 dumpsys activity activities 中的前台包名（Android 5–14 兼容）。"""
    code, out, _ = _adb_shell(
        udid,
        "dumpsys",
        "activity",
        "activities",
        timeout=12,
    )
    if code != 0 or not out:
        return ""
    for line in out.splitlines():
        text = line.strip()
        if "mResumedActivity" in text or "mFocusedActivity" in text or "topResumedActivity" in text:
            m = re.search(r"([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.]+", text)
            if m:
                return m.group(1)
    m = re.search(r"mCurrentFocus=Window\{[^ ]+ [^ ]+ ([a-zA-Z0-9_.]+)/", out)
    if m:
        return m.group(1)
    return ""


def adb_wait_foreground_package(
    udid: str,
    package: str,
    *,
    timeout_sec: float = 10.0,
    poll_sec: float = 0.4,
) -> bool:
    import time

    pkg = (package or "").strip()
    if not pkg:
        return False
    deadline = time.time() + max(0.5, timeout_sec)
    while time.time() < deadline:
        if adb_get_foreground_package(udid) == pkg:
            return True
        time.sleep(poll_sec)
    return adb_get_foreground_package(udid) == pkg


def _friendly_app_label(udid: str, package: str) -> str:
    pkg = (package or "").strip()
    code, out, _ = _adb_shell(
        udid,
        "dumpsys",
        "package",
        pkg,
        timeout=10,
    )
    if code == 0 and out:
        m = re.search(r"application-label(?:-en)?:'([^']+)'", out)
        if m:
            return m.group(1)
    return pkg or "未知应用"


def adb_launch_app(
    udid: str,
    package: str,
    activity: Optional[str] = None,
    *,
    retries: int = 3,
    wait_foreground: bool = True,
    timeout_sec: float = 10.0,
) -> Dict[str, Any]:
    """
    健壮的应用启动（PC 端回放 / open_app 步骤）。
    原缺陷：仅 resolve-activity + am start，失败即抛笼统错误。
    """
    pkg = (package or "").strip()
    if not pkg:
        raise ValueError("缺少应用包名")
    if not adb_is_package_installed(udid, pkg):
        label = _friendly_app_label(udid, pkg)
        raise RuntimeError(f"应用「{label}」未安装，请先安装后再重试。")

    act = (activity or "").strip()
    label = _friendly_app_label(udid, pkg)
    last_err = ""

    def _try_launch(cmd: Tuple[str, ...]) -> bool:
        nonlocal last_err
        code, out, err = _adb_shell(udid, *cmd, timeout=15)
        if code != 0:
            last_err = err or out or "启动命令失败"
            return False
        if wait_foreground and not adb_wait_foreground_package(udid, pkg, timeout_sec=timeout_sec):
            last_err = f"已发送启动命令，但 {timeout_sec:.0f}s 内未检测到应用进入前台"
            return False
        return True

    for attempt in range(max(1, retries)):
        if act and _try_launch(("am", "start", "-n", f"{pkg}/{act}")):
            return {"package": pkg, "app_label": label, "via": "am_start_n", "attempt": attempt + 1}

        if not act:
            _, out, _ = _adb_shell(
                udid, "cmd", "package", "resolve-activity", "--brief", pkg, timeout=10,
            )
            line = (out or "").splitlines()[0] if out else ""
            component = line.strip() if "/" in line else ""
            if component and _try_launch(("am", "start", "-n", component)):
                return {"package": pkg, "app_label": label, "via": "resolve_activity", "attempt": attempt + 1}

        if _try_launch((
            "am", "start", "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.LAUNCHER", "-p", pkg,
        )):
            return {"package": pkg, "app_label": label, "via": "am_main_launcher", "attempt": attempt + 1}

        if _try_launch(("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")):
            return {"package": pkg, "app_label": label, "via": "monkey", "attempt": attempt + 1}

    raise RuntimeError(
        last_err or f"无法打开应用「{label}」。请确认已安装且可从桌面图标正常启动。"
    )


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


def adb_get_screen_size(udid: str) -> Tuple[int, int]:
    """读取设备屏幕分辨率，供录制归一化百分比坐标。"""
    code, out, _ = _adb_shell(udid, "wm", "size", timeout=8)
    if code == 0 and out:
        return parse_wm_size(out)
    return 1080, 1920
