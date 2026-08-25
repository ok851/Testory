# -*- coding: utf-8 -*-
"""跨端 Agent 手机侧工具：本机 enqueue/await，不经 adb 逐步遥控。

Agent（大脑）调用本模块；手机 APK（双手）拉取 job 并本机执行后上报。
桌面侧仍用 windows_*；本模块提供 desktop_* 别名便于统一工具面。

三级 OTP 获取策略：
1. 通知监听（APK 本机 extract_otp）— 主路径
2. scrcpy 视觉（屏幕截图 + OCR）— 快速兜底
3. 手动兜底 — 用户输入
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

# 常见短信验证码：4–8 位数字；优先匹配「验证码」附近
_DEFAULT_OTP_RE = re.compile(
    r"(?:验证码|校验码|动态码|code|Code)[^\d]{0,12}(\d{4,8})"
    r"|(\d{4,8})[^\d]{0,8}(?:为您的验证码|是您的验证码)",
    re.IGNORECASE,
)
_FALLBACK_DIGIT_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")


def parse_otp_from_text(
    text: str,
    *,
    pattern: str = "",
    prefer_near_keywords: bool = True,
) -> Optional[str]:
    """从通知/短信原文解析 OTP。失败返回 None。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if pattern:
        try:
            m = re.search(pattern, raw)
            if m:
                return (m.group(1) if m.lastindex else m.group(0) or "").strip() or None
        except re.error:
            pass
    if prefer_near_keywords:
        m = _DEFAULT_OTP_RE.search(raw)
        if m:
            for g in m.groups():
                if g:
                    return g
    # 含验证码关键词时再宽松取码
    if any(k in raw for k in ("验证码", "校验码", "动态码", "verification", "OTP", "otp")):
        m2 = _FALLBACK_DIGIT_RE.search(raw)
        if m2:
            return m2.group(1)
    return None


def _mock_otp() -> str:
    return (os.environ.get("MOBILE_OTP_MOCK") or "").strip()


def enqueue_mobile_job(
    *,
    steps: Optional[List[Dict[str, Any]]] = None,
    case_id: int = 0,
    user_id: int = 0,
    device_id: str = "",
    job_kind: str = "run_steps",
    job_meta: Optional[Dict[str, Any]] = None,
    source: str = "agent_tool",
) -> str:
    from mobile_sync_store import enqueue_run_job

    # Agent 场景禁止绑定历史 device_id：tokens 里常有多台/旧 ANDROID_ID，
    # 模型或 hands 快照一旦填错，当前轮询手机会永远领不到（status=pending）。
    # 空 device_id = 该用户任意已配对设备可领。
    _ = (device_id or "").strip()
    did = ""

    return enqueue_run_job(
        case_id=int(case_id or 0),
        steps=list(steps or []),
        user_id=int(user_id or 0),
        device_id=did,
        source=source,
        job_kind=job_kind,
        job_meta=dict(job_meta or {}),
    )


def wait_mobile_job(
    job_id: str,
    *,
    timeout_sec: float = 120.0,
    abort_event: Any = None,
    on_tick: Any = None,
    poll_interval_sec: float = 1.0,
) -> Dict[str, Any]:
    from mobile_sync_store import wait_for_run_job

    return wait_for_run_job(
        job_id,
        timeout_sec=timeout_sec,
        abort_event=abort_event,
        on_tick=on_tick,
        poll_interval_sec=poll_interval_sec,
    )


def ensure_mobile_hand_ready(
    user_id: int = 0,
    device_id: str = "",
    *,
    poller_retry: int = 0,
) -> Optional[Dict[str, Any]]:
    """无配对手机或无障碍轮询心跳过期时失败，避免 enqueue 后空等。

    poller_retry: 心跳过期时额外重试次数（每次等 5s），默认 0 = 不重试。
    内部 mobile_extract_otp / mobile_run_steps 调用时自动传入 3 次重试。
    """
    try:
        from mobile_sync_store import device_poller_status_for_user, list_paired_devices_for_user

        devices = list_paired_devices_for_user(int(user_id or 0))
    except Exception:
        devices = []
    if not devices:
        return {
            "success": False,
            "ok": False,
            "error": (
                "未检测到已配对手机。请先在「移动端」完成配对，并保持 APK 已连接；"
                "领取 PC 任务还需开启无障碍（PcRunJobPoller）。"
            ),
            "error_code": "MOBILE_HAND_OFFLINE",
        }
    want = (device_id or "").strip()
    if want and not any((d.get("device_id") or "") == want for d in devices):
        return {
            "success": False,
            "ok": False,
            "error": f"指定 device_id={want} 未在已配对列表中",
            "error_code": "MOBILE_DEVICE_MISMATCH",
            "paired_devices": [d.get("device_id") for d in devices],
        }

    skip_poller = (os.environ.get("MOBILE_HAND_SKIP_POLLER") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ) or bool(_mock_otp())
    if skip_poller:
        return None

    max_attempts = 1 + max(0, int(poller_retry))
    last_status: Dict[str, Any] = {}
    for attempt in range(max_attempts):
        try:
            status = device_poller_status_for_user(int(user_id or 0), want)
        except Exception:
            status = {"alive_count": 0, "stale_sec": 45}
        last_status = status
        if int(status.get("alive_count") or 0) > 0:
            return None
        if attempt < max_attempts - 1:
            # 原 8.0s 等待过长，导致短信验证码时效性丢失；降至 2.5s（3次重试=等待约5s，仍有充分时间让 APK 回发心跳）
            time.sleep(2.5)

    stale = last_status.get("stale_sec") or 45
    return {
        "success": False,
        "ok": False,
        "error": (
            f"[MOBILE_POLLER_STALE] 手机已配对，但近 {int(stale)}s 内无任务轮询心跳"
            f"（已等待重试 {max_attempts} 次）。"
            "mobile_run_steps / mobile_run_case 需要 APK 无障碍服务内的 PcRunJobPoller。"
            "请打开手机无障碍，确认 APK 界面显示已连接后再试；"
            "若只需 PC 遥控点击，请改用 mobile_tap / mobile_swipe（不依赖 poller）。"
        ),
        "error_code": "MOBILE_POLLER_STALE",
        "poller_status": last_status,
        "paired_devices": [d.get("device_id") for d in devices],
        "hint": "use_mobile_tap_for_pc_remote_or_enable_apk_poller",
    }


def mobile_extract_otp(
    *,
    timeout_sec: float = 120.0,
    sender_hint: str = "",
    pattern: str = "",
    user_id: int = 0,
    device_id: str = "",
    mock_allowed: bool = True,
    abort_event: Any = None,
    on_tick: Any = None,
    allow_scrcpy_vision: bool = True,
) -> Dict[str, Any]:
    """高层工具：三级 OTP 获取策略（scrcpy 视觉优先）

    1. scrcpy 视觉（屏幕截图 + OCR）— 主路径，快速（2-5秒）
    2. 通知监听（APK 本机 extract_otp）— 兜底路径（7-15秒）
    3. 手动兜底 — 用户输入
    """
    mock = _mock_otp() if mock_allowed else ""
    if mock:
        return {
            "success": True,
            "ok": True,
            "sms_otp": mock,
            "variables": {"sms_otp": mock},
            "source": "mock_env",
            "evidence": [{"type": "otp_mock", "hint": "MOBILE_OTP_MOCK"}],
        }

    device_id = ""
    evidence: List[Dict[str, Any]] = []

    # ── 一级：scrcpy 视觉（快速主路径）──
    # 直接 ADB screencap + OCR，无需 APK enqueue/await 轮询，2-5 秒出结果
    if allow_scrcpy_vision:
        scrcpy_result = _try_scrcpy_vision_otp(
            user_id=user_id,
            sender_hint=sender_hint,
            pattern=pattern,
        )
        if scrcpy_result.get("success"):
            scrcpy_result["evidence"] = evidence + scrcpy_result.get("evidence", [])
            return scrcpy_result
        # scrcpy 视觉未成功，记录 evidence 继续走 APK 兜底
        evidence.append({
            "type": "scrcpy_vision",
            "ok": False,
            "error": scrcpy_result.get("error", ""),
            "error_code": scrcpy_result.get("error_code", ""),
        })

    # ── 二级：通知监听（APK 本机兜底）──
    hand_err = ensure_mobile_hand_ready(user_id, device_id, poller_retry=2)
    if hand_err:
        evidence.append({"type": "hand_check", "ok": False, "error": hand_err.get("error", "")})
        return hand_err

    job_meta = {
        "skill": "extract_otp",
        "sender_hint": (sender_hint or "").strip(),
        "pattern": (pattern or "").strip(),
        "timeout_sec": float(timeout_sec),
    }
    steps = [
        {
            "action": "extract_otp",
            "description": "从通知/短信提取验证码",
            "selector_value": sender_hint or "",
            "input_value": pattern or "",
            "store_as": "sms_otp",
            "automation_layer": "android",
        }
    ]
    job_id = enqueue_mobile_job(
        steps=steps,
        user_id=user_id,
        device_id=device_id,
        job_kind="extract_otp",
        job_meta=job_meta,
        source="mobile_extract_otp",
    )
    job = wait_mobile_job(
        job_id,
        timeout_sec=timeout_sec,
        abort_event=abort_event,
        on_tick=on_tick,
        poll_interval_sec=0.5,
    )
    payload = job.get("result_payload") if isinstance(job.get("result_payload"), dict) else {}
    vars_out = {}
    if isinstance(payload.get("variables"), dict):
        vars_out.update(payload["variables"])
    otp = (
        vars_out.get("sms_otp")
        or payload.get("sms_otp")
        or ""
    )
    if not otp and isinstance(payload.get("extracted"), dict):
        otp = payload["extracted"].get("sms_otp") or ""
        vars_out.update(payload["extracted"])
    if not otp:
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        for r in results:
            if not isinstance(r, dict):
                continue
            blob = (
                r.get("extractedText")
                or r.get("extracted_text")
                or r.get("notificationText")
                or r.get("errorMessage")
                or ""
            )
            otp = parse_otp_from_text(str(blob), pattern=pattern) or ""
            if otp:
                break
    st = str(job.get("status") or "").strip().lower()
    ok = bool(otp) and st in ("success", "ok")
    if otp and "sms_otp" not in vars_out:
        vars_out["sms_otp"] = otp
    if ok:
        return {
            "success": True,
            "ok": True,
            "sms_otp": otp,
            "variables": vars_out,
            "job_id": job_id,
            "source": "device_await",
            "evidence": evidence + [{"type": "mobile_extract_otp", "job_id": job_id}],
        }

    # ── 三级：手动兜底 ──
    return {
        "success": False,
        "ok": False,
        "sms_otp": otp or "",
        "variables": vars_out,
        "job_id": job_id,
        "error": job.get("error")
        or payload.get("error")
        or ("未解析到验证码" if not otp else "手机本机取码未成功"),
        "error_code": job.get("error_code") or "MOBILE_OTP_EXTRACT_FAILED",
        "source": "device_await",
        "evidence": evidence,
        "manual_fallback_needed": True,
        "hint": "请查看手机短信/通知并手动输入验证码",
    }


def _try_scrcpy_vision_otp(
    *,
    user_id: int = 0,
    sender_hint: str = "",
    pattern: str = "",
) -> Dict[str, Any]:
    """尝试 scrcpy 视觉路径提取验证码。

    当 APK 通知监听失败或手机未就绪时，直接通过 ADB 截图 + OCR 获取验证码。
    速度优势：无需 APK enqueue/await 轮询，直接获取屏幕内容进行识别。
    """
    try:
        from mobile_scrcpy_vision import (
            get_device_serial_for_user,
            extract_otp_from_device,
        )
    except ImportError:
        return {"success": False, "source": "scrcpy_vision", "error": "scrcpy_vision not available"}

    serial = get_device_serial_for_user(user_id)
    if not serial:
        return {
            "success": False,
            "source": "scrcpy_vision",
            "error": "无可用设备 serial",
        }

    result = extract_otp_from_device(
        serial,
        sender_hint=sender_hint,
        pattern=pattern,
        user_id=user_id,
        navigate_to_messages=True,
    )
    result["source"] = "scrcpy_vision"
    return result



def mobile_run_steps(
    steps: List[Dict[str, Any]],
    *,
    timeout_sec: float = 180.0,
    user_id: int = 0,
    device_id: str = "",
    case_id: int = 0,
    abort_event: Any = None,
    on_tick: Any = None,
    skip_hand_check: bool = False,
) -> Dict[str, Any]:
    """高层工具：把步骤下发给已配对手机本机回放并等待结果。"""
    if not steps:
        return {"success": False, "ok": False, "error": "steps 为空"}
    # 不信任调用方/模型传入的 device_id，避免绑到历史设备
    device_id = ""
    if not skip_hand_check:
        hand_err = ensure_mobile_hand_ready(user_id, device_id, poller_retry=5)
        if hand_err:
            return hand_err
    job_id = enqueue_mobile_job(
        steps=list(steps),
        case_id=case_id,
        user_id=user_id,
        device_id=device_id,
        job_kind="run_steps",
        source="mobile_run_steps",
    )
    job = wait_mobile_job(
        job_id,
        timeout_sec=timeout_sec,
        abort_event=abort_event,
        on_tick=on_tick,
    )
    payload = job.get("result_payload") if isinstance(job.get("result_payload"), dict) else {}
    st = str(job.get("status") or "").strip().lower()
    ok = st in ("success", "ok") or payload.get("success") is True
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    step_lines: List[str] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        ok_i = r.get("success") is True
        mark = "OK" if ok_i else "FAIL"
        desc = str(r.get("stepDescription") or r.get("step_description") or "")[:40]
        strat = str(r.get("action") or r.get("actualStrategy") or r.get("actual_strategy") or "")
        err = str(r.get("errorMessage") or r.get("error_message") or "")[:80]
        line = f"[{mark}] {desc} ({strat})"
        if err and not ok_i:
            line += f" err={err}"
        step_lines.append(line)
    agent_note = (
        "success 仅表示手机无障碍手势层回报；不得据此编造「业务已办完」。"
        "请逐条对照 steps_digest；若有 FAIL 或未勾选/未推进类错误，必须如实告知用户并停止夸大。"
    )
    return {
        "success": ok,
        "ok": ok,
        "job_id": job_id,
        "status": st,
        "result_payload": payload,
        "steps_digest": step_lines,
        "agent_note": agent_note,
        "variables": payload.get("variables") if isinstance(payload.get("variables"), dict) else {},
        "error": None if ok else (
            job.get("error")
            or payload.get("error")
            or "手机本机执行失败"
        ),
        "error_code": None if ok else (job.get("error_code") or payload.get("error_code")),
        "source": "device_await",
    }


def mobile_run_case(
    case_id: int,
    *,
    timeout_sec: float = 180.0,
    user_id: int = 0,
    device_id: str = "",
    abort_event: Any = None,
    on_tick: Any = None,
) -> Dict[str, Any]:
    """按 PC 用例库 case_id 拉步骤后本机执行（需 user_id 有权限）。"""
    from database import Database
    from mobile_sync_store import case_bundle

    cid = int(case_id or 0)
    if cid <= 0:
        return {"success": False, "ok": False, "error": "缺少 case_id"}
    db = Database()
    bundle, err = case_bundle(db, cid, int(user_id or 0))
    if err or not bundle:
        return {"success": False, "ok": False, "error": err or "用例不可用"}
    steps = bundle.get("steps") or []
    return mobile_run_steps(
        steps if isinstance(steps, list) else [],
        timeout_sec=timeout_sec,
        user_id=user_id,
        device_id=device_id,
        case_id=cid,
        abort_event=abort_event,
        on_tick=on_tick,
    )


def desktop_launch(app_name: str) -> Dict[str, Any]:
    from windows_desktop_tools import windows_launch_app

    return windows_launch_app(app_name)


def desktop_click(description: str) -> Dict[str, Any]:
    from windows_desktop_tools import windows_click_element

    return windows_click_element(description)


def desktop_input(text: str, **kwargs: Any) -> Dict[str, Any]:
    from windows_desktop_tools import windows_type_text

    return windows_type_text(text, **kwargs)


def desktop_focus(app_name: str) -> Dict[str, Any]:
    from windows_desktop_tools import windows_focus_app

    return windows_focus_app(app_name)


# ════════════════════════════════════════════════════════════
# 移动端通用操作手（agent 可直接操控手机）
# 执行位置自动切换：活跃 scrcpy 会话注入 → ADB 遥控 → 手机 APK job
# ════════════════════════════════════════════════════════════

def _resolve_device_serial(user_id: int = 0, serial: str = "") -> str:
    serial = (serial or "").strip()
    if serial:
        return serial
    try:
        from mobile_scrcpy_vision import get_device_serial_for_user

        return get_device_serial_for_user(int(user_id or 0))
    except Exception:
        return ""


def _scrcpy_session_ready(serial: str) -> bool:
    """是否有活跃 scrcpy 会话（不预热，快速探测）。"""
    if not serial:
        return False
    try:
        from mobile_scrcpy_bridge import _get_persistent_device

        sess = _get_persistent_device(serial)
        return bool(sess is not None and getattr(sess, "running", False))
    except Exception:
        return False


def _warm_scrcpy_once(serial: str, *, timeout: float = 8.0) -> Tuple[bool, str]:
    """短超时预热 scrcpy 会话；失败不抛，返回 (ok, err)。"""
    if not serial:
        return False, "serial 为空"
    if _scrcpy_session_ready(serial):
        return True, "already_ready"
    try:
        from mobile_scrcpy_bridge import ensure_bridge_started, warm_scrcpy_session

        ensure_bridge_started()
        return warm_scrcpy_session(serial, timeout=timeout)
    except TypeError:
        try:
            from mobile_scrcpy_bridge import warm_scrcpy_session

            return warm_scrcpy_session(serial)
        except Exception as exc:
            return False, str(exc)[:160]
    except Exception as exc:
        return False, str(exc)[:160]

def _pc_remote_tap(serial: str, x: int, y: int) -> Dict[str, Any]:
    """PC 遥控 tap：优先活跃 scrcpy 控制通道注入，否则 ADB smart_tap。"""
    if not _scrcpy_session_ready(serial):
        _warm_scrcpy_once(serial, timeout=6.0)
    if _scrcpy_session_ready(serial):
        try:
            from mobile_scrcpy_bridge import ensure_scrcpy_device_session
            from mobile_ui_probe import get_screen_size

            session, _ = ensure_scrcpy_device_session(serial)
            if session is not None:
                w, h = get_screen_size(serial)
                if session.inject_tap(int(x), int(y), screen_width=w, screen_height=h):
                    return {"ok": True, "via": "scrcpy_inject", "x": int(x), "y": int(y)}
        except Exception:
            pass
    try:
        from mobile_adb_control import smart_tap

        r = smart_tap(serial, int(x), int(y))
        return {"ok": True, "via": r.get("via", "adb"), "x": int(x), "y": int(y)}
    except Exception as exc:
        return {"ok": False, "error": f"PC 遥控 tap 失败: {exc}"}


def _pc_remote_swipe(
    serial: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int = 300,
) -> Dict[str, Any]:
    """PC 遥控 swipe：优先 scrcpy 注入，否则 ADB。"""
    if not _scrcpy_session_ready(serial):
        _warm_scrcpy_once(serial, timeout=6.0)
    if _scrcpy_session_ready(serial):
        try:
            from mobile_scrcpy_bridge import ensure_scrcpy_device_session
            from mobile_ui_probe import get_screen_size

            session, _ = ensure_scrcpy_device_session(serial)
            if session is not None:
                w, h = get_screen_size(serial)
                if session.inject_swipe(
                    int(x1), int(y1), int(x2), int(y2),
                    screen_width=w, screen_height=h,
                ):
                    return {
                        "ok": True,
                        "via": "scrcpy_inject",
                        "start": (int(x1), int(y1)),
                        "end": (int(x2), int(y2)),
                    }
        except Exception:
            pass
    try:
        from mobile_adb_control import adb_swipe

        r = adb_swipe(serial, int(x1), int(y1), int(x2), int(y2), int(duration_ms))
        return {"ok": True, "via": "adb", "start": r.get("start"), "end": r.get("end")}
    except Exception as exc:
        return {"ok": False, "error": f"PC 遥控 swipe 失败: {exc}"}


def _pc_key(serial: str, keycode: int) -> Dict[str, Any]:
    """PC 遥控按键（back/home/menu）。"""
    names = {3: "home", 4: "back", 82: "menu", 24: "volume_up", 25: "volume_down"}
    try:
        from mobile_adb_control import adb_keyevent

        adb_keyevent(serial, int(keycode))
        return {"ok": True, "via": "adb_keyevent", "key": names.get(int(keycode), str(keycode))}
    except Exception as exc:
        return {"ok": False, "error": f"按键注入失败: {exc}"}


def _apk_job_execute(
    user_id: int,
    steps: List[Dict[str, Any]],
    *,
    timeout_sec: float = 120.0,
    source: str = "mobile_action",
) -> Dict[str, Any]:
    """手机 APK job 队列执行（本机执行路径，PC 不逐步遥控）。"""
    hand_err = ensure_mobile_hand_ready(int(user_id or 0), "", poller_retry=2)
    if hand_err:
        return hand_err
    job_id = enqueue_mobile_job(
        steps=steps,
        user_id=int(user_id or 0),
        job_kind="run_steps",
        source=source,
    )
    job = wait_mobile_job(
        job_id,
        timeout_sec=timeout_sec,
        poll_interval_sec=0.8,
    )
    st = str(job.get("status") or "").strip().lower()
    payload = job.get("result_payload") if isinstance(job.get("result_payload"), dict) else {}
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    ok = st in ("success", "ok")
    return {
        "ok": ok,
        "success": ok,
        "job_id": job_id,
        "status": st,
        "via": "apk_job",
        "error": job.get("error") or payload.get("error") or ("" if ok else "手机本机执行失败"),
        "results": results,
    }


def _apk_text_input(
    user_id: int,
    serial: str,
    text: str,
    *,
    input_label: str = "",
    timeout_sec: float = 90.0,
) -> Dict[str, Any]:
    """中文等非 ASCII 文本输入：必走 APK 本机剪贴板/无障碍输入。"""
    step: Dict[str, Any] = {
        "action": "input",
        "input_value": text,
        "selector_type": "text",
        "selector_value": input_label or "",
        "description": f"输入文本 {text!r}" + (f"（目标：{input_label}）" if input_label else "（当前焦点输入框）"),
        "automation_layer": "android",
    }
    r = _apk_job_execute(user_id, [step], timeout_sec=timeout_sec, source="mobile_input")
    if r.get("ok"):
        r["input_text"] = text
    return r


def _observe_after_action(
    serial: str,
    user_id: int = 0,
    observe: Any = True,
) -> Dict[str, Any]:
    """操作后视觉反馈：observe=true 默认 OCR（<1s）；observe=deep 追加 VLM 描述。

    不做持续流式喂 VLM（token 不可控）；默认只 OCR，深度理解按需开启。
    """
    mode = str(observe or "").strip().lower()
    if mode in ("0", "false", "no", "off", "none"):
        return {}
    try:
        from mobile_scrcpy_vision import capture_device_frame, ocr_device_frame

        png = capture_device_frame(serial)
        if not png:
            return {"observe_error": "操作后截图失败"}
        ocr = ocr_device_frame(png)
        obs: Dict[str, Any] = {
            "texts": ocr.get("texts", []),
            "text_joined": ocr.get("text_joined", ""),
        }
        if mode in ("deep", "vlm", "1", "true", "yes"):
            try:
                from ai_vision_local import vision_describe

                desc = vision_describe(
                    png,
                    "这是手机当前屏幕截图，请用一句中文概括当前界面内容和关键状态（不超过60字）。",
                )
                if desc:
                    obs["vision"] = str(desc)[:280]
            except Exception:
                pass
        return {"observation": obs}
    except Exception as exc:
        return {"observe_error": str(exc)}


def _mobile_execute_action(
    action: str,
    args: Dict[str, Any],
    *,
    user_id: int = 0,
) -> Dict[str, Any]:
    """移动端动作适配器：解析坐标/文本 → 按可用性自动切换执行位置。

    决策链：
    - tap/swipe：活跃 scrcpy 会话 → PC 注入；否则 ADB 遥控；APK job 兜底
    - input：ASCII → ADB input text；含中文 → APK 剪贴板（ADB 中文必败）
    - back/home：ADB keyevent → APK job 兜底
    """
    a = dict(args or {})
    serial = _resolve_device_serial(int(user_id or 0), str(a.get("serial") or ""))
    if not serial:
        return {"success": False, "ok": False, "error": "无可用设备 serial，请先配对手机或通过 ADB 连接设备"}

    # 设备级互斥锁：同一手机同一时间仅一个执行通道（PC 遥控 / APK job）
    from mobile_device_lock import MobileDeviceLockError, mobile_device_guard

    act = (action or "").strip().lower().lstrip("mobile_")

    # 首次 tap/swipe 前短超时预热 scrcpy（失败则后续走 ADB，结果带 via）
    if act in ("tap", "click", "swipe"):
        _warm_scrcpy_once(serial, timeout=6.0)

    # ── tap ──
    if act in ("tap", "click"):
        x = a.get("x")
        y = a.get("y")
        if x is None or y is None:
            # 通过 UI 树按文本/resource-id 定位
            desc = str(a.get("description") or a.get("text") or a.get("locate") or "")
            rid = str(a.get("resource_id") or "")
            xml = ""
            try:
                from mobile_ui_probe import get_mobile_ui_tree

                tree = get_mobile_ui_tree(serial, user_id=user_id)
                if tree.get("success"):
                    xml = tree.get("xml") or ""
            except Exception:
                pass
            node = None
            if rid and xml:
                from mobile_ui_probe import find_node_by_resource_id

                node = find_node_by_resource_id(xml, rid)
            if node is None and desc and xml:
                from mobile_ui_probe import find_node_by_text

                node = find_node_by_text(xml, desc)
            if node is not None:
                from mobile_ui_probe import locate_center

                center = locate_center(node)
                if center:
                    x, y = center
                    a["locator"] = (node.get("resource_id") or node.get("text") or "")[:80]
            if x is None or y is None:
                return {
                    "success": False,
                    "ok": False,
                    "error": f"无法从 UI 树定位目标（desc={desc or '-'} rid={rid or '-'}），"
                    "请先调用 mobile_get_ui_tree 查看可用节点，或直接提供 x/y 坐标",
                }
        # 物理注入：设备锁保护（防 PC 遥控与 APK job 同设备双写）
        try:
            with mobile_device_guard(
                serial,
                owner=f"agent_tap:{user_id}:{serial}",
                timeout_sec=150.0,
            ):
                result = _pc_remote_tap(serial, int(x), int(y))
        except MobileDeviceLockError as le:
            return {"success": False, "ok": False, "error": str(le), "action": "tap", "serial": serial}
        result["action"] = "tap"
        result.update(_observe_after_action(serial, user_id, a.get("observe", True)))
        result["serial"] = serial
        return result

    # ── swipe ──
    if act == "swipe":
        x1 = a.get("x1") if a.get("x1") is not None else a.get("from_x")
        y1 = a.get("y1") if a.get("y1") is not None else a.get("from_y")
        x2 = a.get("x2") if a.get("x2") is not None else a.get("to_x")
        y2 = a.get("y2") if a.get("y2") is not None else a.get("to_y")
        if x1 is None or y1 is None or x2 is None or y2 is None:
            return {"success": False, "ok": False, "error": "swipe 需要 x1/y1/x2/y2 坐标"}
        try:
            with mobile_device_guard(
                serial,
                owner=f"agent_swipe:{user_id}:{serial}",
                timeout_sec=150.0,
            ):
                result = _pc_remote_swipe(
                    serial, int(x1), int(y1), int(x2), int(y2),
                    int(a.get("duration_ms") or a.get("duration") or 300),
                )
        except MobileDeviceLockError as le:
            return {"success": False, "ok": False, "error": str(le), "action": "swipe", "serial": serial}
        result["action"] = "swipe"
        result.update(_observe_after_action(serial, user_id, a.get("observe", True)))
        result["serial"] = serial
        return result

    # ── input ──
    if act in ("input", "type_text", "fill", "set_text"):
        text = str(a.get("text") or a.get("input_value") or "")
        if not text:
            return {"success": False, "ok": False, "error": "input 需要 text 参数"}
        has_cjk = any(ord(ch) > 127 for ch in text)
        # 输入执行（ADB / APK 剪贴板）全程持设备锁
        try:
            with mobile_device_guard(
                serial,
                owner=f"agent_input:{user_id}:{serial}",
                timeout_sec=180.0,
            ):
                if not has_cjk:
                    try:
                        from mobile_adb_control import adb_input_text

                        adb_input_text(serial, text)
                        result = {"ok": True, "via": "adb_input_text", "input_text": text}
                    except Exception:
                        result = _apk_text_input(user_id, serial, text, input_label=str(a.get("input_label") or ""))
                else:
                    result = _apk_text_input(user_id, serial, text, input_label=str(a.get("input_label") or ""))
                    if not result.get("ok"):
                        result["hint"] = "中文输入必须由手机本机执行（剪贴板/无障碍），PC 侧 ADB 不支持中文"
        except MobileDeviceLockError as le:
            return {"success": False, "ok": False, "error": str(le), "action": "input", "serial": serial}
        result["action"] = "input"
        result.update(_observe_after_action(serial, user_id, a.get("observe", True)))
        result["serial"] = serial
        return result

    # ── back / home ──
    if act in ("back", "home", "menu"):
        keycode = {"back": 4, "home": 3, "menu": 82}.get(act, 4)
        try:
            with mobile_device_guard(
                serial,
                owner=f"agent_key:{user_id}:{serial}",
                timeout_sec=150.0,
            ):
                result = _pc_key(serial, keycode)
                if not result.get("ok"):
                    apk = _apk_job_execute(
                        user_id,
                        [{"action": act, "description": f"按下 {act}", "automation_layer": "android"}],
                        timeout_sec=60.0,
                    )
                    if apk.get("ok"):
                        return {**apk, "action": act, "serial": serial}
        except MobileDeviceLockError as le:
            return {"success": False, "ok": False, "error": str(le), "action": act, "serial": serial}
        result["action"] = act
        result.update(_observe_after_action(serial, user_id, a.get("observe", True)))
        result["serial"] = serial
        return result

    return {"success": False, "ok": False, "error": f"不支持的移动端动作 {action}"}


# ── agent 工具函数（供 dispatch_cross_end_tool 调用）──

def mobile_tap(args: Dict[str, Any], *, user_id: int = 0) -> Dict[str, Any]:
    return _mobile_execute_action("tap", args, user_id=user_id)


def mobile_swipe(args: Dict[str, Any], *, user_id: int = 0) -> Dict[str, Any]:
    return _mobile_execute_action("swipe", args, user_id=user_id)


def mobile_input(args: Dict[str, Any], *, user_id: int = 0) -> Dict[str, Any]:
    return _mobile_execute_action("input", args, user_id=user_id)


def mobile_back(args: Dict[str, Any], *, user_id: int = 0) -> Dict[str, Any]:
    return _mobile_execute_action("back", args, user_id=user_id)


def mobile_home(args: Dict[str, Any], *, user_id: int = 0) -> Dict[str, Any]:
    return _mobile_execute_action("home", args, user_id=user_id)


def mobile_get_ui_tree(args: Dict[str, Any], *, user_id: int = 0) -> Dict[str, Any]:
    from mobile_ui_probe import get_mobile_ui_tree

    return get_mobile_ui_tree(
        str(args.get("serial") or ""),
        user_id=int(user_id or 0),
        max_nodes=int(args.get("max_nodes") or 80),
    )


def mobile_get_screen_text(args: Dict[str, Any], *, user_id: int = 0) -> Dict[str, Any]:
    from mobile_scrcpy_vision import capture_device_frame, ocr_device_frame

    serial = _resolve_device_serial(int(user_id or 0), str(args.get("serial") or ""))
    if not serial:
        return {"success": False, "ok": False, "error": "无可用设备 serial"}
    png = capture_device_frame(serial)
    if not png:
        return {"success": False, "ok": False, "error": "设备截图失败"}
    ocr = ocr_device_frame(png)
    return {
        "success": True,
        "ok": True,
        "serial": serial,
        "texts": ocr.get("texts", []),
        "text_joined": ocr.get("text_joined", ""),
        "source": "mobile_screencap_ocr",
    }


MOBILE_TOOL_NAMES = frozenset(
    {
        "mobile_extract_otp",
        "mobile_run_steps",
        "mobile_run_case",
        "mobile_await_notification",  # alias → extract_otp with longer wait
        # scrcpy 视觉工具（此前已挂 schema 但未入此集合 → _is_cross_end_agent_tool
        # 判 False → 落入"未知工具"，是"agent 移动端作用≈0"的直接原因之一）
        "mobile_scrcpy_screenshot",
        "mobile_scrcpy_extract_otp",
        # 通用操作手 + 结构化感知
        "mobile_tap",
        "mobile_swipe",
        "mobile_input",
        "mobile_back",
        "mobile_home",
        "mobile_get_ui_tree",
        "mobile_get_screen_text",
    }
)

DESKTOP_ALIAS_TOOL_NAMES = frozenset(
    {
        "desktop_launch",
        "desktop_click",
        "desktop_input",
        "desktop_focus",
    }
)


def dispatch_cross_end_tool(
    name: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    abort_event: Any = None,
    on_tick: Any = None,
) -> Dict[str, Any]:
    """供 ai_chat_tool_loop 调用。"""
    a = dict(args or {})
    n = (name or "").strip()
    # 内部控制参数不进工具 schema
    a.pop("_abort_event", None)
    a.pop("_on_tick", None)
    try:
        if n == "desktop_launch":
            return desktop_launch(str(a.get("app_name") or a.get("name") or ""))
        if n == "desktop_focus":
            return desktop_focus(str(a.get("app_name") or a.get("name") or ""))
        if n == "desktop_click":
            return desktop_click(str(a.get("description") or a.get("locate") or a.get("text") or ""))
        if n == "desktop_input":
            return desktop_input(str(a.get("text") or ""), **{
                k: v for k, v in a.items() if k not in ("text",)
            })
        if n in ("mobile_extract_otp", "mobile_await_notification"):
            return mobile_extract_otp(
                timeout_sec=float(a.get("timeout_sec") or a.get("timeout") or 120),
                sender_hint=str(a.get("sender_hint") or a.get("sender") or ""),
                pattern=str(a.get("pattern") or a.get("regex") or ""),
                user_id=int(a.get("user_id") or 0),
                device_id=str(a.get("device_id") or ""),
                abort_event=abort_event,
                on_tick=on_tick,
            )
        if n == "mobile_scrcpy_screenshot":
            from mobile_scrcpy_vision import get_device_serial_for_user, capture_device_frame, ocr_device_frame
            serial = str(a.get("serial") or "") or get_device_serial_for_user(int(a.get("user_id") or 0))
            if not serial:
                return {"success": False, "error": "无可用设备 serial"}
            png = capture_device_frame(serial)
            if not png:
                return {"success": False, "error": "设备截图失败"}
            ocr = ocr_device_frame(png)
            return {
                "success": True,
                "ok": True,
                "serial": serial,
                "image_size": len(png),
                "texts": ocr.get("texts", []),
                "text_joined": ocr.get("text_joined", ""),
                "source": "scrcpy_vision",
            }
        if n == "mobile_scrcpy_extract_otp":
            return mobile_extract_otp(
                timeout_sec=float(a.get("timeout_sec") or 30),
                sender_hint=str(a.get("sender_hint") or ""),
                pattern=str(a.get("pattern") or ""),
                user_id=int(a.get("user_id") or 0),
                device_id=str(a.get("device_id") or ""),
                abort_event=abort_event,
                on_tick=on_tick,
                mock_allowed=False,
                allow_scrcpy_vision=True,
            )
        if n == "mobile_run_steps":
            steps = a.get("steps") or []
            if not isinstance(steps, list):
                return {"success": False, "error": "steps 须为数组"}
            return mobile_run_steps(
                steps,
                timeout_sec=float(a.get("timeout_sec") or 180),
                user_id=int(a.get("user_id") or 0),
                device_id=str(a.get("device_id") or ""),
                case_id=int(a.get("case_id") or 0),
                abort_event=abort_event,
                on_tick=on_tick,
            )
        if n == "mobile_run_case":
            return mobile_run_case(
                int(a.get("case_id") or 0),
                timeout_sec=float(a.get("timeout_sec") or 180),
                user_id=int(a.get("user_id") or 0),
                device_id=str(a.get("device_id") or ""),
                abort_event=abort_event,
                on_tick=on_tick,
            )
        # ── 通用操作手 + 结构化感知（P0/P1 新增）──
        if n == "mobile_tap":
            return mobile_tap(a, user_id=int(a.get("user_id") or 0))
        if n == "mobile_swipe":
            return mobile_swipe(a, user_id=int(a.get("user_id") or 0))
        if n == "mobile_input":
            return mobile_input(a, user_id=int(a.get("user_id") or 0))
        if n == "mobile_back":
            return mobile_back(a, user_id=int(a.get("user_id") or 0))
        if n == "mobile_home":
            return mobile_home(a, user_id=int(a.get("user_id") or 0))
        if n == "mobile_get_ui_tree":
            return mobile_get_ui_tree(a, user_id=int(a.get("user_id") or 0))
        if n == "mobile_get_screen_text":
            return mobile_get_screen_text(a, user_id=int(a.get("user_id") or 0))
    except Exception as e:
        return {"success": False, "ok": False, "error": str(e)}
    return {"success": False, "ok": False, "error": f"未知跨端工具 {n}"}


def cross_end_tool_schemas(
    *,
    include_desktop: bool = True,
    include_mobile: bool = True,
) -> List[Dict[str, Any]]:
    """Agent 可见的统一多端工具面（按已连接双手裁剪）。"""
    all_schemas: List[Dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "desktop_launch",
                "description": "启动本机 Windows 桌面应用（双手：桌面 UIA）。等同 windows_launch_app。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app_name": {"type": "string", "description": "应用名，如「记事本」"},
                    },
                    "required": ["app_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "desktop_focus",
                "description": "聚焦已打开的桌面应用窗口。等同 windows_focus_app。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "app_name": {"type": "string"},
                    },
                    "required": ["app_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "desktop_click",
                "description": "在当前桌面目标窗口按短控件名点击。等同 windows_click_element。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "短控件名，如「发送验证码」"},
                    },
                    "required": ["description"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "desktop_input",
                "description": "向当前桌面目标输入文本。等同 windows_type_text。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_extract_otp",
                "description": (
                    "请已配对手机本机从短信/通知提取验证码。"
                    "优先走 scrcpy 视觉（截图+OCR，2-5秒），失败后走 APK 通知监听兜底。"
                    "PC 不等逐步遥控；返回 variables.sms_otp。"
                    "多端注册场景：桌面点发送验证码后调用本工具，再 desktop_input 填回。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "timeout_sec": {"type": "number", "description": "等待秒数，默认 120"},
                        "sender_hint": {"type": "string", "description": "可选：短信发送方提示"},
                        "pattern": {"type": "string", "description": "可选：自定义正则，须含捕获组"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_scrcpy_screenshot",
                "description": (
                    "快速截取已配对手机屏幕并 OCR 识别文字（scrcpy 视觉路径，亚秒级）。"
                    "用于多端联动中快速获取手机屏幕内容，无需 APK 轮询。"
                    "返回 texts（OCR 文本列表）和 text_joined（合并文本）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "serial": {"type": "string", "description": "可选：设备序列号，默认自动获取"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_scrcpy_extract_otp",
                "description": (
                    "通过 scrcpy 视觉快速提取验证码（截图+OCR，2-5秒）。"
                    "先导航到信息/短信应用，截图后 OCR 识别验证码。"
                    "速度远快于 mobile_extract_otp 的 APK 轮询路径。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sender_hint": {"type": "string", "description": "可选：短信发送方提示"},
                        "pattern": {"type": "string", "description": "可选：自定义正则"},
                        "timeout_sec": {"type": "number", "description": "等待秒数，默认 30"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_run_steps",
                "description": (
                    "把 Android 步骤下发给已配对手机本机回放并等待完成。"
                    "正式路径：enqueue + await，禁止用 adb 逐步点手机。"
                    "steps.action 须为手机 IR：open_app（推荐，带 package_name）、"
                    "tap/input/wait/home/back；勿用 launch_app/start_app/shell。"
                    "应用内 tap 必须 selector_type=text + selector_value=可见文案；"
                    "input 必须 input_value + 输入框文案定位。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": (
                                "步骤数组。示例："
                                'open_app={"action":"open_app","package_name":"com.tencent.mobileqq"}；'
                                'tap={"action":"tap","selector_type":"text","selector_value":"登录"}；'
                                'input={"action":"input","selector_type":"text","selector_value":"手机号",'
                                '"input_value":"13800000000"}'
                            ),
                            "items": {"type": "object"},
                        },
                        "timeout_sec": {"type": "number"},
                        "case_id": {"type": "integer"},
                    },
                    "required": ["steps"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_run_case",
                "description": "按 PC 用例库 case_id 在手机本机执行并等待结果。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "case_id": {"type": "integer"},
                        "timeout_sec": {"type": "number"},
                    },
                    "required": ["case_id"],
                },
            },
        },
        # ── 通用操作手 + 结构化感知（P0/P1）──
        {
            "type": "function",
            "function": {
                "name": "mobile_tap",
                "description": (
                    "在已配对手机上执行点击。优先按 UI 树文本定位（description/text 传可见文案如「登录」「发送验证码」），"
                    "也可直接给 x/y 物理像素坐标。自动选择执行通道：活跃 scrcpy 注入 → ADB → 手机本机。"
                    "observe=true（默认）会在点击后自动 OCR 屏幕反馈。多端联动典型用法：先 mobile_get_ui_tree 找目标文案，再本工具点击。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "目标控件可见文案（UI 树 text）"},
                        "text": {"type": "string", "description": "同 description，二选一"},
                        "resource_id": {"type": "string", "description": "可选：resource-id 精确定位，如 com.demo:id/login"},
                        "x": {"type": "integer", "description": "可选：直接给物理像素 x"},
                        "y": {"type": "integer", "description": "可选：直接给物理像素 y"},
                        "serial": {"type": "string", "description": "可选：设备 serial，默认自动获取"},
                        "observe": {"type": "boolean", "description": "点击后是否截图反馈，默认 true"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_swipe",
                "description": (
                    "在已配对手机上滑动（x1,y1 → x2,y2，物理像素坐标）。"
                    "duration_ms 控制时长（默认 300，长滑动可给 800+）。"
                    "observe=true（默认）滑动后自动 OCR 反馈。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "x1": {"type": "integer", "description": "起点 x"},
                        "y1": {"type": "integer", "description": "起点 y"},
                        "x2": {"type": "integer", "description": "终点 x"},
                        "y2": {"type": "integer", "description": "终点 y"},
                        "duration_ms": {"type": "integer", "description": "滑动时长毫秒，默认 300"},
                        "serial": {"type": "string", "description": "可选：设备 serial"},
                        "observe": {"type": "boolean", "description": "滑动后是否截图反馈，默认 true"},
                    },
                    "required": ["x1", "y1", "x2", "y2"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_input",
                "description": (
                    "向手机当前聚焦输入框输入文本。ASCII 文本走 PC 快速通道；中文等非 ASCII 自动走手机本机剪贴板/无障碍输入"
                    "（PC 侧 ADB 不支持中文）。input_label 可选：输入框可见文案，帮助手机端定位目标输入框。"
                    "observe=true（默认）输入后自动 OCR 反馈。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要输入的文本（中文/英文均可）"},
                        "input_value": {"type": "string", "description": "同 text，二选一"},
                        "input_label": {"type": "string", "description": "可选：目标输入框的可见文案（如「手机号」）"},
                        "serial": {"type": "string", "description": "可选：设备 serial"},
                        "observe": {"type": "boolean", "description": "输入后是否截图反馈，默认 true"},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_back",
                "description": "手机返回键。observe=true（默认）按下后自动 OCR 反馈。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "serial": {"type": "string", "description": "可选：设备 serial"},
                        "observe": {"type": "boolean", "description": "按下后是否截图反馈，默认 true"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_home",
                "description": "手机 Home 键，回到桌面。observe=true（默认）按下后自动 OCR 反馈。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "serial": {"type": "string", "description": "可选：设备 serial"},
                        "observe": {"type": "boolean", "description": "按下后是否截图反馈，默认 true"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_get_ui_tree",
                "description": (
                    "获取手机当前界面 UI 层级树（紧凑文本，类似 Web DOM 快照 / 桌面 UIA 树）。"
                    "返回 compact_text 可直接阅读：节点含类名/文本/resource-id/坐标/是否可点击。"
                    "**这是移动端智能操作的第一手感知**：定位元素用 mobile_tap(description=文案) 即可，无需盲猜坐标。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "serial": {"type": "string", "description": "可选：设备 serial"},
                        "max_nodes": {"type": "integer", "description": "可选：返回节点上限，默认 80"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mobile_get_screen_text",
                "description": (
                    "截图手机屏幕并 OCR 全部可见文字（快速，<1s）。"
                    "适合快速了解手机当前界面内容；需要精确定位控件时优先 mobile_get_ui_tree。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "serial": {"type": "string", "description": "可选：设备 serial"},
                    },
                },
            },
        },
    ]
    out: List[Dict[str, Any]] = []
    for s in all_schemas:
        try:
            name = str((s.get("function") or {}).get("name") or "")
        except Exception:
            name = ""
        if name.startswith("desktop_") and not include_desktop:
            continue
        if name.startswith("mobile_") and not include_mobile:
            continue
        out.append(s)
    return out
