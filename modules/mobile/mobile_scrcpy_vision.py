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


# [Testory-patch] 截图失败诊断：capture_device_frame 失败原因按 serial 记录，
# 供调用方（mobile_get_screen_text 等）在错误信息中透出，替代"设备截图失败"盲区。
_LAST_CAPTURE_ERROR: Dict[str, str] = {}
_LAST_CAPTURE_SOURCE: Dict[str, str] = {}


def get_last_capture_error(serial: str = "") -> str:
    """返回最近一次 capture_device_frame 失败的诊断信息（无则空串）。"""
    return _LAST_CAPTURE_ERROR.get((serial or "").strip(), "")


def get_last_capture_source(serial: str = "") -> str:
    """返回最近一次成功截图来源：scrcpy_keyframe | adb_screencap。"""
    return _LAST_CAPTURE_SOURCE.get((serial or "").strip(), "")


def capture_device_frame(serial: str = "", *, timeout: float = 10.0) -> Optional[bytes]:
    """截取设备屏幕 PNG。

    优先 scrcpy relay 关键帧（与人类预览同源）；失败再 adb screencap。
    日志/诊断经 get_last_capture_source 标注来源。
    """
    serial = (serial or "").strip()
    _err_key = serial or ""

    def _note_fail(reason: str) -> None:
        _LAST_CAPTURE_ERROR[_err_key] = reason[:300]

    # P1：scrcpy 关键帧（会话热且可解码时）
    try:
        from modules.mobile.mobile_scrcpy_bridge import get_latest_keyframe_png

        kf_timeout = min(2.0, max(0.3, float(timeout) * 0.25))
        png = get_latest_keyframe_png(serial, timeout=kf_timeout)
        if png and len(png) > 100:
            _LAST_CAPTURE_SOURCE[_err_key] = "scrcpy_keyframe"
            _LAST_CAPTURE_ERROR.pop(_err_key, None)
            return png
    except Exception as exc:
        _note_fail(f"scrcpy_keyframe: {exc}")

    try:
        from modules.mobile.mobile_env_config import adb_path
    except ImportError:
        try:
            from modules.mobile.mobile_device_manager import adb_path
        except ImportError:
            adb_path = lambda: "adb"  # type: ignore

    # 选用能看见该 serial 的 adb（捆绑与 PATH 可能分属不同 daemon）
    try:
        adb_exe = resolve_adb_exe_for_serial(serial) or adb_path()
    except Exception:
        adb_exe = adb_path()
    cmd = [adb_exe]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["exec-out", "screencap", "-p"])

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=float(timeout), check=False)
        if proc.returncode != 0 or not proc.stdout or len(proc.stdout) < 100:
            _stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()[:200]
            _note_fail(
                f"adb screencap rc={proc.returncode} size={len(proc.stdout or b'')}"
                + (f" stderr={_stderr}" if _stderr else "")
            )
            return None
        _LAST_CAPTURE_SOURCE[_err_key] = "adb_screencap"
        _LAST_CAPTURE_ERROR.pop(_err_key, None)
        return proc.stdout
    except subprocess.TimeoutExpired:
        _note_fail(f"adb screencap 超时（>{timeout}s），设备可能离线或 adb server 忙")
        return None
    except Exception as exc:
        _note_fail(f"adb screencap 异常: {exc}")
        return None


def ocr_device_frame(png_bytes: bytes, *, lang: str = "chi_sim+eng") -> Dict[str, Any]:
    """对设备截图进行 OCR 文字识别。"""
    if not png_bytes or len(png_bytes) < 100:
        return {"texts": [], "blocks": [], "error": "empty_frame"}
    try:
        from modules.desktop.desktop_ocr import extract_text_blocks
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
    exclude_numbers: Optional[List[str]] = None,
) -> Optional[str]:
    """从 OCR / dumpsys 文本提取验证码（复用严格解析，禁止乱抓手机号片段）。"""
    if not isinstance(ocr_result, dict):
        return None
    texts = ocr_result.get("texts") or ocr_result.get("text_joined") or ""
    if isinstance(texts, list):
        text_blob = "\n".join(str(t) for t in texts if t)
    else:
        text_blob = str(texts)

    if not text_blob:
        return None

    try:
        from modules.mobile.mobile_cross_end_tools import parse_otp_from_text

        return parse_otp_from_text(
            text_blob,
            pattern=pattern,
            prefer_near_keywords=True,
            exclude_numbers=exclude_numbers,
        )
    except Exception:
        return None


def extract_otp_from_device(
    serial: str = "",
    *,
    sender_hint: str = "",
    pattern: str = "",
    user_id: int = 0,
    device_id: str = "",
    navigate_to_messages: bool = False,
    exclude_numbers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """scrcpy/ADB 视觉路径：优先通知栏文本 → 当前屏 OCR →（可选）正确打开短信后再 OCR。

    不再盲目 am start 到错误包名（旧实现含 mediaplayer/settings，会误进其它页面）。
    """
    evidence: List[Dict[str, Any]] = []
    serial = (serial or "").strip()
    excludes = list(exclude_numbers or [])
    if not serial:
        try:
            serial = get_device_serial_for_user(int(user_id or 0))
        except Exception:
            serial = ""
    if not serial:
        return {
            "success": False,
            "ok": False,
            "sms_otp": "",
            "error": "无可用设备 serial",
            "error_code": "SCRCPY_NO_SERIAL",
            "evidence": evidence,
            "source": "scrcpy_vision",
        }

    # 1) 通知栏 dumpsys（按单条 NotificationRecord 解析，禁止整坨乱抠）
    notif_otp, notif_ev = _extract_otp_from_notifications(
        serial, pattern=pattern, sender_hint=sender_hint, exclude_numbers=excludes
    )
    evidence.extend(notif_ev)
    if notif_otp:
        return {
            "success": True,
            "ok": True,
            "sms_otp": notif_otp,
            "variables": {"sms_otp": notif_otp},
            "evidence": evidence,
            "source": "adb_notification",
        }

    # 1b) 短信收件箱（有权限时比通知更准；无权限则静默跳过）
    sms_otp, sms_ev = _extract_otp_from_sms_inbox(
        serial, pattern=pattern, exclude_numbers=excludes
    )
    evidence.extend(sms_ev)
    if sms_otp:
        return {
            "success": True,
            "ok": True,
            "sms_otp": sms_otp,
            "variables": {"sms_otp": sms_otp},
            "evidence": evidence,
            "source": "sms_inbox",
        }

    # 2) 下拉通知栏后 OCR（仍不离开当前 App 太远）
    shade_otp, shade_ev = _extract_otp_via_notification_shade(
        serial, pattern=pattern, sender_hint=sender_hint, exclude_numbers=excludes
    )
    evidence.extend(shade_ev)
    if shade_otp:
        return {
            "success": True,
            "ok": True,
            "sms_otp": shade_otp,
            "variables": {"sms_otp": shade_otp},
            "evidence": evidence,
            "source": "notification_shade_ocr",
        }

    # 3) 当前屏幕 OCR（短信可能已在前台）
    cur_otp, cur_ev = _ocr_screen_for_otp(
        serial, pattern=pattern, sender_hint=sender_hint, exclude_numbers=excludes
    )
    evidence.extend(cur_ev)
    if cur_otp:
        return {
            "success": True,
            "ok": True,
            "sms_otp": cur_otp,
            "variables": {"sms_otp": cur_otp},
            "evidence": evidence,
            "source": "scrcpy_vision",
        }

    # 4) 仅在明确要求时，用系统 APP_MESSAGING / 可信短信包打开后再 OCR
    if navigate_to_messages:
        nav_ok, nav_detail = _navigate_to_messages_app(serial)
        evidence.append({"type": "navigate_to_messages", "ok": nav_ok, "detail": nav_detail})
        if nav_ok:
            time.sleep(1.0)
            msg_otp, msg_ev = _ocr_screen_for_otp(
                serial, pattern=pattern, sender_hint=sender_hint, exclude_numbers=excludes
            )
            evidence.extend(msg_ev)
            if msg_otp:
                return {
                    "success": True,
                    "ok": True,
                    "sms_otp": msg_otp,
                    "variables": {"sms_otp": msg_otp},
                    "evidence": evidence,
                    "source": "scrcpy_vision_messages",
                }

    texts_preview = ""
    for ev in reversed(evidence):
        if isinstance(ev, dict) and ev.get("texts_preview"):
            texts_preview = str(ev.get("texts_preview") or "")
            break
    return {
        "success": False,
        "ok": False,
        "sms_otp": "",
        "error": "未从通知栏或屏幕识别到验证码（请确认短信已到达且手机已解锁）",
        "error_code": "SCRCPY_OTP_NOT_FOUND",
        "evidence": evidence + ([{"type": "texts_preview", "texts": texts_preview[:200]}] if texts_preview else []),
        "source": "scrcpy_vision",
    }


def _adb_base(serial: str) -> List[str]:
    try:
        exe = resolve_adb_exe_for_serial(serial)
    except Exception:
        exe = "adb"
    cmd = [exe or "adb"]
    if serial:
        cmd.extend(["-s", serial])
    return cmd


def _ocr_screen_for_otp(
    serial: str,
    *,
    pattern: str = "",
    sender_hint: str = "",
    exclude_numbers: Optional[List[str]] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    evidence: List[Dict[str, Any]] = []
    png = capture_device_frame(serial)
    if not png:
        evidence.append({"type": "screenshot", "ok": False, "error": get_last_capture_error(serial)})
        return None, evidence
    evidence.append({"type": "screenshot", "ok": True, "size": len(png)})
    ocr_result = ocr_device_frame(png)
    if ocr_result.get("error"):
        evidence.append({"type": "ocr", "ok": False, "error": ocr_result.get("error")})
        return None, evidence
    texts = ocr_result.get("texts") or []
    preview = " | ".join(str(t) for t in texts[:8])
    evidence.append({"type": "ocr", "ok": True, "texts_count": len(texts), "texts_preview": preview[:200]})
    otp = extract_otp_from_ocr(
        ocr_result,
        sender_hint=sender_hint,
        pattern=pattern,
        exclude_numbers=exclude_numbers,
    )
    return otp, evidence


def _extract_otp_from_notifications(
    serial: str,
    *,
    pattern: str = "",
    sender_hint: str = "",
    exclude_numbers: Optional[List[str]] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """从 dumpsys notification 按单条通知解析验证码（禁止整坨乱抠数字）。"""
    evidence: List[Dict[str, Any]] = []
    try:
        from modules.mobile.mobile_cross_end_tools import parse_otp_from_notification_dump

        cmd = _adb_base(serial) + ["shell", "dumpsys", "notification", "--noredact"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12, check=False)
        blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if len(blob) < 40:
            cmd2 = _adb_base(serial) + ["shell", "dumpsys", "notification"]
            proc2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=12, check=False)
            blob = (proc2.stdout or "") + "\n" + (proc2.stderr or "")
        evidence.append({"type": "dumpsys_notification", "ok": bool(blob), "size": len(blob)})
        otp, meta = parse_otp_from_notification_dump(
            blob,
            pattern=pattern,
            sender_hint=sender_hint,
            exclude_numbers=exclude_numbers,
        )
        evidence.append(
            {
                "type": "notification_otp",
                "ok": bool(otp),
                "records_total": meta.get("records_total"),
                "otp_candidates": meta.get("otp_candidates"),
                "picked_pkg": meta.get("picked_pkg") or "",
                "picked_preview": (meta.get("picked_preview") or "")[:120],
            }
        )
        if otp:
            return otp, evidence
    except Exception as exc:
        evidence.append({"type": "dumpsys_notification", "ok": False, "error": str(exc)[:160]})
    return None, evidence


def _extract_otp_from_sms_inbox(
    serial: str,
    *,
    pattern: str = "",
    exclude_numbers: Optional[List[str]] = None,
    lookback_ms: int = 15 * 60 * 1000,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """尝试 content://sms/inbox 读取最近短信（无 READ_SMS 权限时会失败，属正常）。"""
    evidence: List[Dict[str, Any]] = []
    try:
        from modules.mobile.mobile_cross_end_tools import parse_otp_from_text

        since = int(time.time() * 1000) - int(lookback_ms)
        # 部分机型 content query 语法不同；失败即跳过
        cmd = _adb_base(serial) + [
            "shell",
            "content",
            "query",
            "--uri",
            "content://sms/inbox",
            "--projection",
            "body:date:address",
            "--sort",
            "date DESC",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        evidence.append(
            {
                "type": "sms_inbox_query",
                "ok": proc.returncode == 0 and "Row:" in out,
                "size": len(out),
                "rc": proc.returncode,
            }
        )
        if "Row:" not in out:
            return None, evidence

        # Row: 0 body=xxx, date=123, address=yyy
        rows = re.split(r"\n(?=Row:\s*\d+)", out)
        for row in rows[:12]:
            if "body=" not in row:
                continue
            body_m = re.search(r"\bbody=([^,\n]*(?:,(?!\s*(?:date|address|_id)=)[^,\n]*)*)", row)
            date_m = re.search(r"\bdate=(\d+)", row)
            body = (body_m.group(1) if body_m else "").strip()
            if not body:
                continue
            if date_m:
                try:
                    dt = int(date_m.group(1))
                    if dt < 1_000_000_000_000:
                        dt *= 1000
                    if dt < since:
                        continue
                except ValueError:
                    pass
            otp = parse_otp_from_text(
                body, pattern=pattern, prefer_near_keywords=True, exclude_numbers=exclude_numbers
            )
            if otp:
                evidence.append({"type": "sms_inbox_otp", "ok": True, "preview": body[:80]})
                return otp, evidence
        evidence.append({"type": "sms_inbox_otp", "ok": False})
    except Exception as exc:
        evidence.append({"type": "sms_inbox_query", "ok": False, "error": str(exc)[:160]})
    return None, evidence


def _extract_otp_via_notification_shade(
    serial: str,
    *,
    pattern: str = "",
    sender_hint: str = "",
    exclude_numbers: Optional[List[str]] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    evidence: List[Dict[str, Any]] = []
    try:
        expand = _adb_base(serial) + ["shell", "cmd", "statusbar", "expand-notifications"]
        subprocess.run(expand, capture_output=True, timeout=5, check=False)
        time.sleep(0.6)
        evidence.append({"type": "expand_notifications", "ok": True})
    except Exception as exc:
        evidence.append({"type": "expand_notifications", "ok": False, "error": str(exc)[:120]})
        return None, evidence
    otp, ocr_ev = _ocr_screen_for_otp(
        serial, pattern=pattern, sender_hint=sender_hint, exclude_numbers=exclude_numbers
    )
    evidence.extend(ocr_ev)
    try:
        collapse = _adb_base(serial) + ["shell", "cmd", "statusbar", "collapse"]
        subprocess.run(collapse, capture_output=True, timeout=5, check=False)
    except Exception:
        pass
    return otp, evidence


def _navigate_to_messages_app(serial: str) -> Tuple[bool, str]:
    """打开系统短信/信息应用。返回 (ok, detail)。

    禁止用 mediaplayer/settings 等无关包「冒充成功」。
    """
    serial = (serial or "").strip()
    if not serial:
        return False, "serial 为空"

    # 优先系统消息分类 Intent（各 OEM 通常会正确解析）
    intents: List[List[str]] = [
        ["shell", "am", "start", "-a", "android.intent.action.MAIN",
         "-c", "android.intent.category.APP_MESSAGING"],
        ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "sms:"],
        ["shell", "am", "start", "-a", "android.intent.action.MAIN",
         "-c", "android.intent.category.DEFAULT", "-t", "vnd.android-dir/mms-sms"],
    ]
    for extra in intents:
        try:
            cmd = _adb_base(serial) + extra
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
            out = ((proc.stdout or "") + (proc.stderr or "")).lower()
            if proc.returncode == 0 and "error" not in out and "exception" not in out:
                return True, " ".join(extra[-3:])
        except Exception:
            continue

    # 可信短信包（不含播放器/设置）
    packages = [
        "com.google.android.apps.messaging",
        "com.android.mms",
        "com.samsung.android.messaging",
        "com.android.messaging",
        "com.coloros.mms",
        "com.oplus.mms",
        "com.oneplus.mms",
        "com.android.mms.service",
        "com.miui.sms",
    ]
    # 动态探测已安装的 messaging 相关包
    try:
        pm = _adb_base(serial) + ["shell", "pm", "list", "packages"]
        proc = subprocess.run(pm, capture_output=True, text=True, timeout=10, check=False)
        installed = (proc.stdout or "").lower()
        for hint in ("messaging", "mms", "sms"):
            for line in installed.splitlines():
                if hint in line and "package:" in line:
                    pkg = line.split("package:", 1)[-1].strip()
                    if pkg and pkg not in packages and "media" not in pkg and "settings" not in pkg:
                        packages.insert(0, pkg)
    except Exception:
        pass

    for pkg in packages:
        if "mediaplayer" in pkg or pkg == "com.android.settings":
            continue
        try:
            # monkey LAUNCHER 比硬编码 .MainActivity 更稳
            cmd = _adb_base(serial) + [
                "shell", "monkey", "-p", pkg, "-c",
                "android.intent.category.LAUNCHER", "1",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
            out = ((proc.stdout or "") + (proc.stderr or "")).lower()
            if proc.returncode == 0 and "no activities" not in out and "error" not in out:
                return True, f"monkey:{pkg}"
        except Exception:
            continue

    return False, "未找到可用的短信/信息应用"


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
        from modules.mobile.mobile_scrcpy_bridge import _get_persistent_device
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
        from modules.mobile.mobile_scrcpy_bridge import _get_persistent_device
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


def _adb_authorized_serials_via(exe: str) -> List[str]:
    exe = (exe or "").strip()
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        out: List[str] = []
        for line in (proc.stdout or "").strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                udid = parts[0].strip()
                if udid:
                    out.append(udid)
        return out
    except Exception:
        return []


def _candidate_adb_exes() -> List[str]:
    """捆绑 adb 与 PATH adb（可能分属不同 daemon，需都探测）。"""
    seen = set()
    out: List[str] = []
    for getter in (
        lambda: __import__("modules.mobile.mobile_env_config", fromlist=["adb_path"]).adb_path(),
    ):
        try:
            p = str(getter() or "").strip()
        except Exception:
            p = ""
        if p:
            key = os.path.normcase(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
    try:
        import shutil

        p = (shutil.which("adb") or "").strip()
        if p:
            key = os.path.normcase(p)
            if key not in seen:
                seen.add(key)
                out.append(p)
    except Exception:
        pass
    if "adb" not in seen:
        out.append("adb")
    return out


def _adb_authorized_serials() -> List[str]:
    """当前任一可用 adb 中 state=device 的 serial（去重保序）。"""
    seen = set()
    out: List[str] = []
    try:
        from modules.mobile.mobile_device_manager import list_usb_devices

        for d in list_usb_devices() or []:
            udid = str((d or {}).get("udid") or "").strip()
            state = str((d or {}).get("state") or "").strip().lower()
            if udid and state == "device" and udid not in seen:
                seen.add(udid)
                out.append(udid)
    except Exception:
        pass
    for exe in _candidate_adb_exes():
        for udid in _adb_authorized_serials_via(exe):
            if udid not in seen:
                seen.add(udid)
                out.append(udid)
    return out


def resolve_adb_exe_for_serial(serial: str = "") -> str:
    """为指定 serial 选择能看见该设备的 adb 可执行文件。"""
    serial = (serial or "").strip()
    candidates = _candidate_adb_exes()
    if not serial:
        return candidates[0] if candidates else "adb"
    for exe in candidates:
        if serial in _adb_authorized_serials_via(exe):
            return exe
    return candidates[0] if candidates else "adb"


def is_adb_serial_online(serial: str) -> bool:
    """判断 serial 是否为当前在线的 adb 设备（不可用 ANDROID_ID 冒充）。"""
    s = (serial or "").strip()
    if not s:
        return False
    return s in _adb_authorized_serials()


def get_device_serial_for_user(user_id: int = 0) -> str:
    """解析可用于 adb/scrcpy 的设备 serial。

    配对表里的 device_id 通常是 ANDROID_ID，不能直接当 adb -s 参数。
    优先：本机在线 adb 设备；其次：配对 device_id 恰好等于在线 serial 时才采用。
    """
    online = _adb_authorized_serials()
    online_set = set(online)

    try:
        from modules.mobile.mobile_device_manager import (
            get_connected_udid,
            pick_best_authorized_device,
        )

        udid = (get_connected_udid() or "").strip()
        if udid and udid in online_set:
            return udid
        if udid and is_adb_serial_online(udid):
            return udid
        dev = pick_best_authorized_device()
        if dev:
            best = (dev.get("udid") or "").strip()
            if best and (not online_set or best in online_set):
                return best
    except Exception:
        pass

    # 配对 device_id 仅当它确实出现在 adb devices 中才可用
    try:
        from modules.mobile.mobile_sync_store import list_paired_devices_for_user

        for d in list_paired_devices_for_user(int(user_id or 0)) or []:
            did = str((d or {}).get("device_id") or "").strip()
            if did and did in online_set:
                return did
    except Exception:
        pass

    if online:
        return online[0]
    return ""


def scrcpy_ensure_session(serial: str) -> Dict[str, Any]:
    """确保 scrcpy 会话已启动。"""
    serial = (serial or "").strip()
    if not serial:
        return {"success": False, "error": "缺少设备 serial"}

    try:
        from modules.mobile.mobile_scrcpy_bridge import ensure_scrcpy_device_session, scrcpy_available
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
