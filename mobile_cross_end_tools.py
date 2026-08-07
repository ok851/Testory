# -*- coding: utf-8 -*-
"""跨端 Agent 手机侧工具：本机 enqueue/await，不经 adb 逐步遥控。

Agent（大脑）调用本模块；手机 APK（双手）拉取 job 并本机执行后上报。
桌面侧仍用 windows_*；本模块提供 desktop_* 别名便于统一工具面。
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

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
) -> Dict[str, Any]:
    from mobile_sync_store import wait_for_run_job

    return wait_for_run_job(
        job_id,
        timeout_sec=timeout_sec,
        abort_event=abort_event,
        on_tick=on_tick,
    )


def ensure_mobile_hand_ready(user_id: int = 0, device_id: str = "") -> Optional[Dict[str, Any]]:
    """无配对手机或无障碍轮询心跳过期时立刻失败，避免 enqueue 后空等。"""
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

    # 跳过心跳：CI / 显式放行；有 OTP mock 时也不强卡 poller
    skip_poller = (os.environ.get("MOBILE_HAND_SKIP_POLLER") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ) or bool(_mock_otp())
    if skip_poller:
        return None

    try:
        status = device_poller_status_for_user(int(user_id or 0), want)
    except Exception:
        status = {"alive_count": 0, "stale_sec": 45}
    if int(status.get("alive_count") or 0) <= 0:
        stale = status.get("stale_sec") or 45
        return {
            "success": False,
            "ok": False,
            "error": (
                f"手机已配对，但近 {int(stale)}s 内无任务轮询心跳。"
                "请打开 APK 无障碍服务（PcRunJobPoller 仅在无障碍内运行），"
                "确认界面显示已连接后再试。"
            ),
            "error_code": "MOBILE_POLLER_STALE",
            "poller_status": status,
            "paired_devices": [d.get("device_id") for d in devices],
        }
    return None


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
) -> Dict[str, Any]:
    """高层工具：等待手机本机提取短信/通知验证码，写入 sms_otp。

    CI / 无真机：设置环境变量 MOBILE_OTP_MOCK=123456 可立即返回（不 enqueue）。
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
    hand_err = ensure_mobile_hand_ready(user_id, device_id)
    if hand_err:
        return hand_err

    job_meta = {
        "skill": "extract_otp",
        "sender_hint": (sender_hint or "").strip(),
        "pattern": (pattern or "").strip(),
        "timeout_sec": float(timeout_sec),
    }
    # 给手机一个可识别步骤（APK 看到 action=extract_otp 即走取码逻辑）
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
    # 手机可能把原文放在 results[0].extractedText
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
            "evidence": [{"type": "mobile_extract_otp", "job_id": job_id}],
        }
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
    }


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
        hand_err = ensure_mobile_hand_ready(user_id, device_id)
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


MOBILE_TOOL_NAMES = frozenset(
    {
        "mobile_extract_otp",
        "mobile_run_steps",
        "mobile_run_case",
        "mobile_await_notification",  # alias → extract_otp with longer wait
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
                    "请已配对手机本机从短信/通知提取验证码（双手：手机 APK）。"
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
