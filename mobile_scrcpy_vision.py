# -*- coding: utf-8 -*-
"""scrcpy 视觉集成：帧捕获 + OCR + 控制注入，为多端联动提供快速视觉能力。

三级 OTP 获取策略：
1. 通知监听（现有 APK 机制）
2. scrcpy 视觉（屏幕截图 + OCR，快速路径）
3. 手动兜底（用户输入）

核心能力：
- capture_device_frame: 快速截取设备屏幕（ADB screencap，亚秒级）
- ocr_device_frame: 对截图进行 OCR 文字识别
- extract_otp_from_frame: 从 OCR 结果提取验证码
- scrcpy_tap / scrcpy_swipe: 通过 scrcpy 控制通道注入操作
- open_messages_app: 导航到信息/短信应用
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_OTP_RE = re.compile(
    r"(?:验证码|校验码|动态码|code|Code|OTP|otp)[^\d]{0,12}(\d{4,8})"
    r"|(\d{4,8})[^\d]{0,8}(?:为您的验证码|是您的验证码|验证码|code)",
    re.IGNORECASE,
)
_FALLBACK_DIGIT_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")


def capture_device_frame(serial: str = "", *, timeout: float = 10.0) -> Optional[bytes]:
    """快速截取设备屏幕（ADB screencap -p，直接返回 PNG 字节）。

    相比 scrcpy H.264 帧需要解码，此路径直接获取 PNG，适合 OCR。
    耗时通常 < 1 秒（USB 连接）。
    """
    serial = (serial or "").strip()
    try:
        from mobile_device_manager import adb_path
    except ImportError:
        return None

    cmd = [adb_path()]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["exec-out", "screencap", "-p"])
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=float(timeout), check=False)
        if proc.returncode != 0 or not proc.stdout or len(proc.stdout) < 100:
            return None
        return proc.stdout
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def ocr_device_frame(png_bytes: bytes, *, lang: str = "chi_sim+eng") -> Dict[str, Any]:
    """对设备截图进行 OCR 文字识别。"""
    if not png_bytes or len(png_bytes) < 100:
        return {"texts": [], "blocks": [], "error": "empty_frame"}
    try:
        from desktop_ocr import extract_text_blocks
        blocks = extract_text_blocks(png_bytes, lang=lang)
        texts = [b.get("text", "") for b in blocks if isinstance(b, dict)]
        return {
            "texts": texts,
            "blocks": blocks,
            "text_joined": "\n".join(t for t in texts if t),
        }
    except ImportError:
        return {"texts": [], "blocks": [], "error": "ocr_not_available"}
    except Exception as exc:
        return {"texts": [], "blocks": [], "error": str(exc)}


def extract_otp_from_ocr(
    ocr_result: Dict[str, Any],
    *,
    sender_hint: str = "",
    pattern: str = "",
) -> Optional[str]:
    """从 OCR 结果提取验证码。"""
    if not isinstance(ocr_result, dict):
        return None
    texts = ocr_result.get("texts") or ocr_result.get("text_joined") or ""
    if isinstance(texts, list):
        text_blob = " ".join(str(t) for t in texts if t)
    else:
        text_blob = str(texts)

    if not text_blob:
        return None

    # 1. 自定义 pattern
    if pattern:
        try:
            m = re.search(pattern, text_blob)
            if m:
                return (m.group(1) if m.lastindex else m.group(0) or "").strip() or None
        except re.error:
            pass

    # 2. 标准 OTP regex
    m = _DEFAULT_OTP_RE.search(text_blob)
    if m:
        for g in m.groups():
            if g:
                return g

    # 3. 宽松匹配：含验证码关键词时取 4-8 位数字
    if any(k in text_blob for k in ("验证码", "校验码", "动态码", "verification", "OTP", "otp", "code", "Code")):
        m2 = _FALLBACK_DIGIT_RE.search(text_blob)
        if m2:
            return m2.group(1)

    return None


def extract_otp_from_device(
    serial: str = "",
    *,
    sender_hint: str = "",
    pattern: str = "",
    user_id: int = 0,
    device_id: str = "",
    navigate_to_messages: bool = False,
) -> Dict[str, Any]:
    """scrcpy 视觉路径：截取设备屏幕 → OCR → 提取验证码。

    速度优势：无需 APK enqueue/await 轮询，直接获取屏幕内容进行识别。
    适合验证码时效要求高的场景。

    Args:
        serial: 设备序列号
        sender_hint: 发送者提示（可选）
        pattern: 自定义验证码正则
        user_id: 用户 ID（用于日志）
        device_id: 设备 ID
        navigate_to_messages: 是否先导航到短信/信息应用

    Returns:
        标准 OTP 结果字典
    """
    evidence: List[Dict[str, Any]] = []

    if navigate_to_messages:
        nav_ok = _navigate_to_messages_app(serial)
        evidence.append({"type": "navigate_to_messages", "ok": nav_ok})
        if not nav_ok:
            return {
                "success": False,
                "ok": False,
                "sms_otp": "",
                "error": "无法导航到信息/短信应用",
                "error_code": "SCRCPY_NAV_FAILED",
                "evidence": evidence,
                "source": "scrcpy_vision",
            }
        # 等待页面加载
        time.sleep(0.8)

    # 截图
    png = capture_device_frame(serial)
    if not png:
        return {
            "success": False,
            "ok": False,
            "sms_otp": "",
            "error": "设备截图失败",
            "error_code": "SCRCPY_SCREENSHOT_FAILED",
            "evidence": evidence,
            "source": "scrcpy_vision",
        }

    evidence.append({"type": "screenshot", "size": len(png)})

    # OCR
    ocr_result = ocr_device_frame(png)
    if ocr_result.get("error"):
        return {
            "success": False,
            "ok": False,
            "sms_otp": "",
            "error": f"OCR 识别失败: {ocr_result['error']}",
            "error_code": "SCRCPY_OCR_FAILED",
            "evidence": evidence,
            "source": "scrcpy_vision",
        }

    evidence.append({"type": "ocr", "texts_count": len(ocr_result.get("texts", []))})

    # 提取 OTP
    otp = extract_otp_from_ocr(ocr_result, sender_hint=sender_hint, pattern=pattern)

    if otp:
        return {
            "success": True,
            "ok": True,
            "sms_otp": otp,
            "variables": {"sms_otp": otp},
            "evidence": evidence,
            "source": "scrcpy_vision",
        }

    # 未找到验证码
    texts_preview = " | ".join(ocr_result.get("texts", [])[:5])
    return {
        "success": False,
        "ok": False,
        "sms_otp": "",
        "error": "屏幕中未识别到验证码",
        "error_code": "SCRCPY_OTP_NOT_FOUND",
        "evidence": evidence + [{"type": "texts_preview", "texts": texts_preview[:200]}],
        "source": "scrcpy_vision",
    }


def _navigate_to_messages_app(serial: str) -> bool:
    """导航到信息/短信应用。

    策略：
    1. 尝试通过 scrcpy 控制注入 Home + 打开应用
    2. 回退到 ADB am start
    """
    serial = (serial or "").strip()
    if not serial:
        return False

    # 策略 1：ADB 直接启动短信应用
    _MESSAGES_PACKAGES = [
        "com.android.mms",
        "com.google.android.apps.messaging",
        "com.samsung.android.messaging",
        "com.sec.android.app.mediaplayer",
        "com.android.settings",  # 兜底
    ]
    for pkg in _MESSAGES_PACKAGES:
        try:
            cmd = ["adb"]
            if serial:
                cmd.extend(["-s", serial])
            cmd.extend(["shell", "am", "start", "-a", "android.intent.action.MAIN",
                        "-c", "android.intent.category.LAUNCHER", "-n", f"{pkg}/.MainActivity"])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            if proc.returncode == 0 and "Error" not in (proc.stdout or ""):
                return True
        except Exception:
            continue

    # 策略 2：按 Home 键后尝试打开信息
    try:
        cmd = ["adb"]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(["shell", "input", "keyevent", "3"])  # KEYCODE_HOME
        subprocess.run(cmd, capture_output=True, timeout=5, check=False)
        time.sleep(0.3)
        cmd2 = ["adb"]
        if serial:
            cmd2.extend(["-s", serial])
        cmd2.extend(["shell", "am", "start", "-a", "android.intent.action.VIEW",
                      "-d", "sms://"])
        proc = subprocess.run(cmd2, capture_output=True, text=True, timeout=5, check=False)
        if proc.returncode == 0:
            return True
    except Exception:
        pass

    return False


def scrcpy_tap(
    serial: str,
    x: int,
    y: int,
    *,
    screen_width: int = 1080,
    screen_height: int = 1920,
) -> Dict[str, Any]:
    """通过 scrcpy 控制通道注入 tap 操作。"""
    try:
        from mobile_scrcpy_bridge import _get_persistent_device
    except ImportError:
        return {"success": False, "error": "scrcpy bridge not available"}

    dev = _get_persistent_device(serial)
    if not dev or not dev.running:
        return {"success": False, "error": "scrcpy session not running"}

    ok = dev.inject_tap(int(x), int(y), screen_width=screen_width, screen_height=screen_height)
    return {"success": ok, "x": int(x), "y": int(y), "source": "scrcpy_control"}


def scrcpy_swipe(
    serial: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    screen_width: int = 1080,
    screen_height: int = 1920,
    steps: int = 8,
) -> Dict[str, Any]:
    """通过 scrcpy 控制通道注入 swipe 操作。"""
    try:
        from mobile_scrcpy_bridge import _get_persistent_device
    except ImportError:
        return {"success": False, "error": "scrcpy bridge not available"}

    dev = _get_persistent_device(serial)
    if not dev or not dev.running:
        return {"success": False, "error": "scrcpy session not running"}

    ok = dev.inject_swipe(int(x1), int(y1), int(x2), int(y2),
                          screen_width=screen_width, screen_height=screen_height, steps=steps)
    return {
        "success": ok,
        "start": (int(x1), int(y1)),
        "end": (int(x2), int(y2)),
        "source": "scrcpy_control",
    }


def scrcpy_type_text(
    serial: str,
    text: str,
    *,
    screen_width: int = 1080,
    screen_height: int = 1920,
) -> Dict[str, Any]:
    """通过 ADB input text 注入文字（scrcpy 不直接支持文字输入）。"""
    serial = (serial or "").strip()
    escaped = (text or "").replace(" ", "%s")
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["shell", "input", "text", escaped])
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=10, check=False)
        ok = proc.returncode == 0
        return {"success": ok, "text": text, "source": "adb_input_text"}
    except Exception as exc:
        return {"success": False, "text": text, "error": str(exc)}


def get_device_serial_for_user(user_id: int = 0) -> str:
    """获取用户已配对设备的 serial。"""
    try:
        from mobile_sync_store import list_paired_devices_for_user
        devices = list_paired_devices_for_user(int(user_id or 0))
        if devices:
            return (devices[0].get("device_id") or "").strip()
    except Exception:
        pass
    try:
        from mobile_device_manager import get_connected_udid
        return get_connected_udid() or ""
    except Exception:
        return ""


def scrcpy_ensure_session(serial: str) -> Dict[str, Any]:
    """确保 scrcpy 会话已启动。"""
    serial = (serial or "").strip()
    if not serial:
        return {"success": False, "error": "缺少设备 serial"}

    try:
        from mobile_scrcpy_bridge import ensure_scrcpy_device_session, scrcpy_available
        if not scrcpy_available():
            return {"success": False, "error": "scrcpy 未安装"}
        sess, err = ensure_scrcpy_device_session(serial)
        if sess and sess.running:
            return {"success": True, "running": True, "control": bool(sess._control_socket)}
        return {"success": False, "error": err or "scrcpy 启动失败"}
    except ImportError:
        return {"success": False, "error": "scrcpy bridge not available"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
