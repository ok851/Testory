# -*- coding: utf-8 -*-
"""桌面自然语言执行：本机 DesktopAutomation / DesktopAgent，不经浏览器。"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# (匹配关键词, launch_app 的 input_value, 展示名)
_DESKTOP_APP_MAP = (
    (("控制面板", "control panel", "control.exe"), "control", "控制面板"),
    (("设置", "windows 设置", "系统设置", "ms-settings"), "ms-settings:", "Windows 设置"),
    (("记事本", "notepad"), "notepad", "记事本"),
    (("计算器", "calculator", "calc"), "calc", "计算器"),
    (("资源管理器", "文件资源管理器", "explorer", "此电脑"), "explorer", "资源管理器"),
    (("命令提示符", "cmd", "命令行"), "cmd", "命令提示符"),
    (("powershell", "终端"), "powershell", "PowerShell"),
    (("画图", "mspaint"), "mspaint", "画图"),
    # path 用中文名走 resolve_executable（新版微信实为 Weixin.exe；WeChat 常解析失败）
    (("微信", "wechat", "weixin"), "微信", "微信"),
    (("企业微信", "wecom", "wxwork"), "企业微信", "企业微信"),
    (("qq",), "QQ", "QQ"),
    # 浏览器
    (("edge", "msedge", "microsoft edge", "microsoft-edge", "微软edge"), "msedge", "Microsoft Edge"),
    (("chrome", "google chrome", "google-chrome", "谷歌chrome"), "chrome", "Google Chrome"),
    (("firefox", "mozilla firefox", "火狐"), "firefox", "Firefox"),
    (("browser", "浏览器"), "msedge", "默认浏览器"),
)

_LOCAL_HINTS = (
    "本地电脑",
    "本机",
    "本地的",
    "我本地",
    "电脑上",
    "操作系统",
    "windows",
    "桌面",
    "本地软件",
    "本地应用",
)

_COMPLEX_HINTS = (
    "发送",
    "发消息",
    "发给",
    "输入",
    "点击",
    "搜索",
    "登录",
    "备注",
    "联系人",
    "好友",
)


def is_desktop_nl_task(message: str) -> bool:
    """粗判：用户是否在要求操作本机桌面应用（而非网页）。"""
    t = (message or "").strip()
    if not t:
        return False
    tl = t.lower()
    if re.search(r"https?://", tl):
        return False
    if any(k in t for k in ("网页", "网站", "浏览器里", "打开百度", "打开谷歌")):
        return False
    for keys, _path, _name in _DESKTOP_APP_MAP:
        if any(k in t or k in tl for k in keys):
            return True
    if any(h in t or h in tl for h in _LOCAL_HINTS) and any(
        v in t for v in ("打开", "启动", "运行", "关闭", "发送", "操作")
    ):
        return True
    return False


def is_complex_desktop_task(message: str) -> bool:
    t = message or ""
    return any(h in t for h in _COMPLEX_HINTS)


def resolve_desktop_launch_target(message: str) -> Optional[Tuple[str, str]]:
    """从自然语言解析 launch 目标。返回 (input_value, display_name) 或 None。"""
    t = (message or "").strip()
    if not t:
        return None
    tl = t.lower()
    for keys, path, name in _DESKTOP_APP_MAP:
        if any(k in t or k in tl for k in keys):
            return path, name
    # 模糊：打开XXX
    m = re.search(r"(?:打开|启动|运行)\s*(?:一下)?\s*(?:本地(?:的|电脑)?|本机)?\s*([^\s，,。的]{1,20})", t)
    if m:
        name = m.group(1).strip()
        if name and name not in ("我", "一个", "这个"):
            return name, name
    return None


def _launch_step(path: str, display: str) -> Dict[str, Any]:
    step = {
        "action": "launch_app",
        "input_value": path,
        "target": display,
        "description": f"打开{display}",
        "automation_layer": "desktop",
    }
    gateway_err = ""
    try:
        from modules.desktop.desktop_agent_client import desktop_agent_enabled, remote_execute_step

        if desktop_agent_enabled():
            r0 = remote_execute_step(step)
            return {
                "ok": True,
                "display": display,
                "step": step,
                "result": r0,
                "via": "desktop_gateway",
            }
    except Exception as e:
        gateway_err = str(e)[:160]

    try:
        from modules.desktop.desktop_automation import DesktopAutomation

        eng = DesktopAutomation()
        out = eng.execute_step(step)
        return {
            "ok": True,
            "display": display,
            "step": step,
            "result": out,
            "via": "local_desktop",
            "gateway_warning": gateway_err,
        }
    except Exception as e:
        err = str(e)[:200]
        if gateway_err:
            err = f"{err}；gateway: {gateway_err}"
        return {"ok": False, "error": err, "display": display, "step": step}


def _is_junk_app_path(path: str, name: str = "") -> bool:
    blob = f"{path} {name}".lower()
    return any(
        x in blob
        for x in (
            "uninstall",
            "卸载",
            "update",
            "updater",
            "setup",
            "installer",
            "安装",
        )
    )


def _fuzzy_resolve_app(query: str) -> Optional[Tuple[str, str]]:
    q = (query or "").strip()
    if not q:
        return None
    # 优先走可执行解析（含 Weixin.exe 等常见路径），避免目录名/卸载项误匹配
    try:
        from modules.desktop.desktop_discovery import resolve_executable

        resolved = (resolve_executable(q) or "").strip()
        if resolved and not _is_junk_app_path(resolved, q):
            return resolved, q
    except Exception:
        pass
    try:
        from modules.desktop.desktop_fuzzy_search import find_apps_by_query
        from modules.desktop.desktop_app_catalog import list_catalog_apps

        apps = list_catalog_apps() or []
        matches = find_apps_by_query(q, apps, top_k=8) or []
        for app, score in matches:
            if score < 0.25:
                continue
            path = (app.get("path") or app.get("exe_name") or q).strip()
            name = (app.get("display_name") or q).strip()
            if _is_junk_app_path(path, name):
                continue
            return path, name
        return None
    except Exception:
        return None


def execute_desktop_launch(message: str) -> Dict[str, Any]:
    """仅启动类任务（兼容旧接口）。"""
    resolved = resolve_desktop_launch_target(message)
    if not resolved:
        fuzzy = None
        m = re.search(r"(?:打开|启动|运行)\s*(?:一下)?\s*(?:本地(?:的|电脑)?|本机)?\s*([^\s，,。]{1,20})", message or "")
        if m:
            fuzzy = _fuzzy_resolve_app(m.group(1).strip())
        if not fuzzy:
            return {
                "ok": False,
                "error": "未能识别要打开的桌面应用。请给出窗口标题或应用名（如记事本、计算器、资源管理器）。",
            }
        resolved = fuzzy
    path, display = resolved
    # 微信等用目录名时再模糊一次拿真实路径
    if path in ("WeChat", "WXWork", "QQ") or (not path.lower().endswith(".exe") and "\\" not in path and "/" not in path):
        fuzzy = _fuzzy_resolve_app(display)
        if fuzzy:
            path, display = fuzzy
    return _launch_step(path, display)


def _find_running_window(title_hints: List[str]) -> Optional[Dict[str, Any]]:
    """在可见顶层窗口中按标题/进程名查找（含任务栏最小化但仍可见的 HWND）。"""
    try:
        from modules.desktop.desktop_discovery import list_visible_windows, list_running_processes

        wins = list_visible_windows() or []
        hints = [h.lower() for h in title_hints if h]
        for w in wins:
            title = (w.get("title") or "").lower()
            proc = (w.get("process") or "").lower()
            if any(h in title or h in proc for h in hints):
                return w
        # 进程在跑但窗口枚举不到标题时，仍返回进程信息供 attach 尝试
        for p in list_running_processes() or []:
            name = (p.get("name") or "").lower()
            if any(h in name for h in hints):
                return {"title": title_hints[0], "process": p.get("name"), "pid": p.get("pid"), "hwnd": 0}
    except Exception:
        pass
    return None


def _attach_step(display: str, title_hint: str = "") -> Dict[str, Any]:
    hint = (title_hint or display or "").strip() or "微信"
    step = {
        "action": "attach_window",
        "target": hint,
        "selector_value": hint,
        "input_value": hint,
        "desktop_spec": {"title_contains": hint},
        "description": f"附着已运行窗口「{hint}」",
        "automation_layer": "desktop",
    }
    try:
        from modules.desktop.desktop_agent_client import desktop_agent_enabled, remote_execute_step

        if desktop_agent_enabled():
            r0 = remote_execute_step(step)
            return {"ok": True, "display": display, "step": step, "result": r0, "via": "desktop_gateway"}
    except Exception as e:
        gateway_err = str(e)[:160]
    else:
        gateway_err = ""
    try:
        from modules.desktop.desktop_automation import DesktopAutomation

        eng = DesktopAutomation()
        out = eng.execute_step(step)
        return {
            "ok": True,
            "display": display,
            "step": step,
            "result": out,
            "via": "local_desktop",
            "gateway_warning": gateway_err,
        }
    except Exception as e:
        err = str(e)[:200]
        if gateway_err:
            err = f"{err}；gateway: {gateway_err}"
        return {"ok": False, "error": err, "display": display, "step": step}


_QUOTE_CHARS = "「」『』\"'“”‘’＂"
_OPEN_QUOTES = "「『\"“‘＂"
_CLOSE_QUOTES = "」』\"”’＂"
_STRUCTURAL_CONTACT_BLOCKLIST = frozenset(
    {
        "为",
        "是",
        "的",
        "人",
        "一个",
        "备注",
        "备注名",
        "联系人",
        "好友",
        "发送",
        "消息",
        "发送消息",
        "内容",
        "微信",
    }
)


def _extract_quoted_segments(text: str) -> List[str]:
    """抽取各类引号中的片段（至少 1 字）。"""
    if not text:
        return []
    # 成对引号：中文弯引号 / 直角引号 / 英文 / 全角
    pairs = (
        ("「", "」"),
        ("『", "』"),
        ("“", "”"),
        ("‘", "’"),
        ('"', '"'),
        ("'", "'"),
        ("＂", "＂"),
    )
    out: List[str] = []
    for left, right in pairs:
        for m in re.finditer(
            re.escape(left) + r"([^" + re.escape(left + right) + r"]{1,200})" + re.escape(right),
            text,
        ):
            seg = (m.group(1) or "").strip()
            if seg and seg not in out:
                out.append(seg)
    return out


def _looks_like_structural_filler(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return True
    if v in _STRUCTURAL_CONTACT_BLOCKLIST:
        return True
    if len(v) <= 2 and v in ("为", "是", "的", "和", "与", "给", "向"):
        return True
    # 「，消息内容为」这类把模板词当成正文
    if v.startswith("，") or v.startswith(","):
        return True
    if v in ("消息内容为", "消息内容是", "发送消息", "内容为", "内容是"):
        return True
    if re.fullmatch(r"[，,。.\s]*消息内容[为是]?[，,。.\s]*", v):
        return True
    return False


def _parse_wechat_send(message: str) -> Optional[Tuple[str, str]]:
    """解析「给XX发消息YYY」→ (contact, text)。

    高置信：两条引号内实体；否则再走结构化短语。解析结果若像模板词则视为失败。
    """
    t = (message or "").strip()
    if not t:
        return None
    tl = t.lower()
    looks_wechat = any(k in t or k in tl for k in ("微信", "wechat", "weixin"))
    looks_send = any(k in t for k in ("发送", "发消息", "发给", "备注", "发一句", "发条"))
    if not (looks_wechat or looks_send):
        return None

    quoted = _extract_quoted_segments(t)
    contact = ""
    body = ""

    if quoted:
        contact_cand = ""
        body_cand = ""
        for seg in quoted:
            pos = t.find(seg)
            prefix = t[max(0, pos - 16) : pos] if pos >= 0 else ""
            if any(k in prefix for k in ("备注", "联系人", "好友", "给", "向", "找")):
                if not contact_cand:
                    contact_cand = seg
            if any(k in prefix for k in ("消息内容", "内容为", "内容是", "说")):
                body_cand = seg
        if len(quoted) >= 2:
            contact = contact_cand or quoted[0].strip()
            body = body_cand or quoted[1].strip()
            # 若误把同一段赋给两边，用首尾两段
            if contact == body and len(quoted) >= 2:
                contact, body = quoted[0].strip(), quoted[-1].strip()
        elif len(quoted) == 1:
            body = body_cand or quoted[0].strip()

    if not contact:
        m = re.search(
            r"(?:备注名(?:为|是)?|联系人|好友)\s*["
            + _OPEN_QUOTES
            + r"]([^"
            + _CLOSE_QUOTES
            + r"]{1,40})["
            + _CLOSE_QUOTES
            + r"]",
            t,
        )
        if m:
            contact = m.group(1).strip()
    if not contact:
        m = re.search(
            r"(?:备注名(?:为|是)?|联系人|好友)\s*["
            + _OPEN_QUOTES
            + r"]?([^"
            + _CLOSE_QUOTES
            + r"\s，,。]{2,40})["
            + _CLOSE_QUOTES
            + r"]?",
            t,
        )
        if m:
            contact = re.sub(r"的人$", "", m.group(1).strip()).strip()
    if not contact:
        m = re.search(
            r"给\s*["
            + _OPEN_QUOTES
            + r"]?([^"
            + _CLOSE_QUOTES
            + r"\s，,。]{2,40})["
            + _CLOSE_QUOTES
            + r"]?\s*(?:发送|发消息|发一条|发一句|发)",
            t,
        )
        if m:
            contact = re.sub(r"的人$", "", m.group(1).strip()).strip()
    if not contact:
        m = re.search(
            r"(?:向|找)\s*["
            + _OPEN_QUOTES
            + r"]?([^"
            + _CLOSE_QUOTES
            + r"\s，,。]{2,40})["
            + _CLOSE_QUOTES
            + r"]?\s*(?:发送|发消息|发)",
            t,
        )
        if m:
            contact = m.group(1).strip()

    if not body:
        m2 = re.search(
            r"消息内容(?:为|是)?\s*["
            + _OPEN_QUOTES
            + r"]([^"
            + _CLOSE_QUOTES
            + r"]{1,200})["
            + _CLOSE_QUOTES
            + r"]",
            t,
        )
        if m2:
            body = m2.group(1).strip()
    if not body:
        m2 = re.search(
            r"消息内容(?:为|是)?\s*[" + _OPEN_QUOTES + r"]?(.+)$",
            t,
        )
        if m2:
            body = m2.group(1).strip().strip(_QUOTE_CHARS + "，,。 ")
    if not body:
        m2 = re.search(
            r"(?:内容(?:为|是)?|说)\s*["
            + _OPEN_QUOTES
            + r"]([^"
            + _CLOSE_QUOTES
            + r"]{1,200})["
            + _CLOSE_QUOTES
            + r"]",
            t,
        )
        if m2:
            body = m2.group(1).strip()
    if not body and contact:
        m3 = re.search(
            re.escape(contact)
            + r"(?:的人)?\s*(?:发送|发消息|发一条|发一句|发)\s*["
            + _OPEN_QUOTES
            + r"]?([^"
            + _CLOSE_QUOTES
            + r"]{1,200})["
            + _CLOSE_QUOTES
            + r"]?",
            t,
        )
        if m3:
            body = m3.group(1).strip()
            m4 = re.search(
                r"消息内容(?:为|是)?\s*[" + _OPEN_QUOTES + r"]?(.+)$",
                body,
            )
            if m4:
                body = m4.group(1).strip().strip(_QUOTE_CHARS + "，,。 ")

    contact = (contact or "").strip().strip(_QUOTE_CHARS)
    body = (body or "").strip().strip(_QUOTE_CHARS)
    if not contact or not body:
        return None
    if _looks_like_structural_filler(contact) or _looks_like_structural_filler(body):
        return None
    return contact, body


def looks_like_wechat_send_task(message: str) -> bool:
    """粗判是否像微信发消息（不要求正则抽参成功）。"""
    t = (message or "").strip()
    if not t:
        return False
    tl = t.lower()
    has_app = any(k in t or k in tl for k in ("微信", "wechat", "weixin"))
    has_send = any(k in t for k in ("发送", "发消息", "发给", "消息内容", "备注", "发一句", "发条"))
    return has_app and has_send


def is_wechat_send_task(message: str) -> bool:
    """是否已能用规则可靠解析出联系人与正文。"""
    return _parse_wechat_send(message) is not None


def llm_extract_wechat_send(
    message: str,
    *,
    local_ai_service: Any = None,
    profile: Optional[Dict[str, Any]] = None,
    abort_event: Any = None,
    timeout: int = 45,
) -> Optional[Tuple[str, str]]:
    """用当前配置的 LLM 抽取 (contact, text)。失败返回 None。"""
    t = (message or "").strip()
    if not t:
        return None
    if profile is None or local_ai_service is None:
        return None
    try:
        from modules.ai.ai_multi_provider import dispatch_chat_completion_messages
    except Exception:
        return None

    messages = [
        {
            "role": "system",
            "content": (
                "你是参数抽取器。从用户中文指令中提取微信发消息的联系人备注名与消息正文。"
                "只输出一行 JSON，不要 markdown，不要解释。格式："
                '{"contact":"...","text":"..."}\n'
                "规则：contact 是人名/备注（如舒琪宝宝大王），绝不是「为/是/备注名」等句式词；"
                "text 是要发送的正文（如你好，我是AI），绝不是「消息内容为」等模板词。"
                "若无法确定任一字段，输出 {\"contact\":\"\",\"text\":\"\"}。"
            ),
        },
        {"role": "user", "content": t},
    ]
    try:
        resp = dispatch_chat_completion_messages(
            messages,
            None,
            profile,
            local_ai_service,
            temperature=0.0,
            timeout=max(15, int(timeout)),
            abort_event=abort_event,
        )
    except Exception:
        return None

    content = ""
    if isinstance(resp, dict):
        content = (resp.get("content") or "").strip()
        if not content:
            # OpenAI style choices
            try:
                content = (
                    ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                ).strip()
            except Exception:
                content = ""
    if not content:
        return None

    # 剥 markdown 代码块
    m = re.search(r"\{[^{}]+\}", content, re.S)
    raw = m.group(0) if m else content
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    contact = str(data.get("contact") or data.get("to") or "").strip()
    body = str(data.get("text") or data.get("message") or data.get("body") or "").strip()
    if not contact or not body:
        return None
    if _looks_like_structural_filler(contact) or _looks_like_structural_filler(body):
        return None
    return contact, body


def resolve_wechat_send_args(
    message: str,
    *,
    local_ai_service: Any = None,
    profile: Optional[Dict[str, Any]] = None,
    abort_event: Any = None,
) -> Tuple[Optional[Tuple[str, str]], str]:
    """解析微信发消息参数。

    返回 ((contact, text)|None, via)：
    via = regex | llm | none
    """
    parsed = _parse_wechat_send(message)
    if parsed:
        return parsed, "regex"
    llm = llm_extract_wechat_send(
        message,
        local_ai_service=local_ai_service,
        profile=profile,
        abort_event=abort_event,
    )
    if llm:
        return llm, "llm"
    return None, "none"


def _run_desktop_steps(steps: List[Dict[str, Any]]) -> Tuple[bool, List[Dict[str, Any]], str]:
    """逐步执行；gateway 连接失败时回退本机 DesktopAutomation。返回逐步结果。"""
    results: List[Dict[str, Any]] = []
    try:
        from modules.desktop.desktop_agent_client import desktop_agent_enabled

        use_gw = desktop_agent_enabled()
    except Exception:
        use_gw = False
    eng = None

    def _local_eng():
        nonlocal eng
        if eng is None:
            from modules.desktop.desktop_automation import DesktopAutomation

            eng = DesktopAutomation()
        return eng

    for st in steps:
        try:
            r = None
            via = "local"
            if use_gw:
                try:
                    from modules.desktop.desktop_agent_client import remote_execute_step

                    r = remote_execute_step(st)
                    via = "gateway"
                except Exception as gw_ex:
                    err_s = str(gw_ex)
                    # 连接拒绝 / 超时：回退本机，避免「假计划步骤」与仅 attach 成功
                    if any(
                        k in err_s.lower()
                        for k in (
                            "10061",
                            "refused",
                            "actively refused",
                            "连接",
                            "timed out",
                            "timeout",
                            "unreachable",
                        )
                    ):
                        r = _local_eng().execute_step(st)
                        via = "local_fallback"
                    else:
                        raise
            else:
                r = _local_eng().execute_step(st)
            # 部分引擎返回 dict 且带 status/ok
            step_ok = True
            if isinstance(r, dict):
                if r.get("ok") is False or r.get("status") == "error":
                    step_ok = False
            if not step_ok:
                results.append({"step": st, "result": r, "ok": False, "via": via})
                return False, results, str((r or {}).get("error") or "步骤返回失败")[:200]
            results.append({"step": st, "result": r, "ok": True, "via": via})
        except Exception as e:
            results.append({"step": st, "error": str(e)[:200], "ok": False})
            return False, results, str(e)[:200]
    return True, results, ""


def _try_wechat_send_message(message: str, steps_acc: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    parsed = _parse_wechat_send(message)
    if not parsed:
        return None
    contact, body = parsed
    # 发消息时需要抢焦点，否则热键/粘贴进不了微信
    old_focus = os.environ.get("DESKTOP_STEAL_FOCUS")
    os.environ["DESKTOP_STEAL_FOCUS"] = "1"
    try:
        return _try_wechat_send_message_inner(message, steps_acc, contact, body)
    finally:
        if old_focus is None:
            os.environ.pop("DESKTOP_STEAL_FOCUS", None)
        else:
            os.environ["DESKTOP_STEAL_FOCUS"] = old_focus


def _try_wechat_send_message_inner(
    message: str,
    steps_acc: List[Dict[str, Any]],
    contact: str,
    body: str,
) -> Dict[str, Any]:
    # 已运行则附着，否则先 launch
    win = _find_running_window(["微信", "wechat", "weixin"])
    prep_steps: List[Dict[str, Any]] = []
    if win and win.get("hwnd"):
        att = _attach_step("微信", (win.get("title") or "微信"))
        if att.get("ok") and att.get("step"):
            prep_steps.append(att["step"])
            steps_acc.append(att["step"])
        elif not att.get("ok"):
            # 附着失败仍尝试 launch（可能托盘唤醒）
            launched = _launch_step("微信", "微信")
            if launched.get("ok") and launched.get("step"):
                prep_steps.append(launched["step"])
                steps_acc.append(launched["step"])
            else:
                return {
                    "ok": False,
                    "partial": True,
                    "error": att.get("error") or "无法附着微信窗口",
                    "display": "微信",
                    "steps": steps_acc,
                    "via": "desktop_wechat_send",
                    "reply": (
                        "未能附着微信窗口。若微信仅在托盘/后台且无可见顶层窗口，"
                        "请先手动点开微信主窗口，再重试发消息。"
                    ),
                }
    else:
        launched = _launch_step("微信", "微信")
        if launched.get("ok") and launched.get("step"):
            prep_steps.append(launched["step"])
            steps_acc.append(launched["step"])
            # launch 后立即 attach
            att = _attach_step("微信", "微信")
            if att.get("ok") and att.get("step"):
                prep_steps.append(att["step"])
                steps_acc.append(att["step"])
        else:
            # 进程可能已在跑但 launch 误判失败：仍尝试 attach
            att = _attach_step("微信", "微信")
            if att.get("ok") and att.get("step"):
                prep_steps.append(att["step"])
                steps_acc.append(att["step"])
            else:
                return {
                    "ok": False,
                    "error": (launched or {}).get("error") or (att or {}).get("error") or "微信未就绪",
                    "display": "微信",
                    "steps": steps_acc,
                    "via": "desktop_wechat_send",
                }

    flow = [
        {"action": "wait", "input_value": "0.8", "description": "等待微信前台", "automation_layer": "desktop"},
        {
            "action": "hotkey",
            "input_value": "^f",
            "description": "打开微信搜索",
            "automation_layer": "desktop",
        },
        {"action": "wait", "input_value": "0.5", "description": "等待搜索框", "automation_layer": "desktop"},
        {
            "action": "input",
            "input_value": contact,
            "description": f"搜索联系人 {contact}",
            "automation_layer": "desktop",
            "compare_type": "keyboard",
            "desktop_spec": {"keyboard_only": True},
        },
        {"action": "wait", "input_value": "0.6", "description": "等待搜索结果", "automation_layer": "desktop"},
        {
            "action": "hotkey",
            "input_value": "{ENTER}",
            "description": "打开会话",
            "automation_layer": "desktop",
        },
        {"action": "wait", "input_value": "0.7", "description": "等待聊天窗", "automation_layer": "desktop"},
        {
            "action": "input",
            "input_value": body,
            "description": "输入消息内容",
            "automation_layer": "desktop",
            "compare_type": "keyboard",
            "desktop_spec": {"keyboard_only": True},
        },
        {"action": "wait", "input_value": "0.3", "description": "输入后短等", "automation_layer": "desktop"},
        {
            "action": "hotkey",
            "input_value": "{ENTER}",
            "description": "发送消息",
            "automation_layer": "desktop",
        },
    ]
    ok, results, err = _run_desktop_steps(flow)
    # 只把真正执行过的步骤写入 steps（含失败的那一步），禁止把未执行的计划步骤标成成功
    executed_steps: List[Dict[str, Any]] = []
    step_results: List[Dict[str, Any]] = []
    # 预备步骤（attach/launch）视为已成功执行
    for ps in prep_steps:
        step_results.append({"step": ps, "ok": True, "via": "prep"})
    for item in results:
        st = item.get("step") if isinstance(item, dict) else None
        if isinstance(st, dict):
            executed_steps.append(st)
            step_results.append(item)
    steps_acc.extend(executed_steps)
    if ok:
        return {
            "ok": True,
            "partial": False,
            "display": "微信",
            "steps": steps_acc,
            "step_results": step_results,
            "result": results,
            "via": "desktop_wechat_send",
            "reply": (
                f"已尝试在微信中向「{contact}」发送「{body}」。"
                "请在微信窗口目视确认是否送达（弱 UIA 界面下可能偶发焦点偏移）。"
            ),
        }
    return {
        "ok": False,
        "partial": True,
        "display": "微信",
        "steps": steps_acc,
        "step_results": step_results,
        "result": results,
        "via": "desktop_wechat_send",
        "error": err,
        "reply": (
            f"已附着/打开微信，但向「{contact}」发消息未完整成功（{err}）。"
            "请确认微信主窗口在前台且未被遮挡后重试；托盘-only 状态需先手动点开窗口。"
        ),
    }


def execute_desktop_nl(message: str) -> Dict[str, Any]:
    """
    桌面自然语言统一入口（仅 DESKTOP_NL_FASTPATH=1 调试旁路）：
    通用 launch/attach + DesktopAgent；不再自动抢跑任一应用死模板宏。
    """
    import os

    t = (message or "").strip()
    if not t:
        return {"ok": False, "error": "指令为空"}

    steps: List[Dict[str, Any]] = []

    # 应用专属宏默认关闭；仅 COMPOSITE_APP_FASTPATH=1 时保留旧微信路径（调试）
    if os.environ.get("COMPOSITE_APP_FASTPATH", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        wechat_try = _try_wechat_send_message(t, steps)
        if wechat_try is not None:
            return wechat_try

    launched = None
    resolved = resolve_desktop_launch_target(t)
    if resolved:
        path, display = resolved
        if path in ("WeChat", "WXWork", "QQ") or (
            not str(path).lower().endswith(".exe") and "\\" not in str(path)
        ):
            fuzzy = _fuzzy_resolve_app(display)
            if fuzzy:
                path, display = fuzzy

        # 已在运行：优先附着，避免重复 launch
        win = _find_running_window([display, path, "微信" if "微信" in display else display])
        if win and (win.get("hwnd") or win.get("title")):
            att = _attach_step(display, (win.get("title") or display))
            if att.get("ok"):
                if att.get("step"):
                    steps.append(att["step"])
                if not is_complex_desktop_task(t):
                    return {
                        "ok": True,
                        "display": display,
                        "steps": steps,
                        "result": att.get("result"),
                        "via": att.get("via"),
                        "reply": f"已附着正在运行的「{display}」窗口（含任务栏最小化后的可见窗口）。",
                    }
                launched = att
            else:
                launched = _launch_step(path, display)
        else:
            launched = _launch_step(path, display)

        if launched.get("ok") and launched.get("step") and launched.get("step") not in steps:
            steps.append(launched["step"])
        if launched.get("ok") and not is_complex_desktop_task(t):
            return {
                "ok": True,
                "display": display,
                "steps": steps,
                "result": launched.get("result"),
                "via": launched.get("via"),
                "reply": f"已在本机打开「{display}」。",
            }
        if not launched.get("ok") and not is_complex_desktop_task(t):
            return launched

    # 复杂桌面：DesktopAgent（技能框架；部分技能仍可能缺 app_query）
    try:
        from modules.desktop.desktop_intelligent_api import DesktopAgent

        agent = DesktopAgent()
        agent.initialize()
        # 补齐 app_query，避免 LaunchAppSkill 校验失败
        try:
            resolved2 = resolve_desktop_launch_target(t)
            if resolved2:
                agent.context.set_variable("app_query", resolved2[1])
        except Exception:
            pass
        skill_result = agent.execute(t)
        try:
            from modules.desktop.desktop_skill_framework import SkillStatus

            st = getattr(skill_result, "status", None)
            ok = st in (SkillStatus.SUCCESS, SkillStatus.PARTIAL_SUCCESS)
        except Exception:
            ok = False
        msg = str(getattr(skill_result, "message", "") or "")
        if not msg and getattr(skill_result, "error", None):
            msg = str(skill_result.error)
        data = getattr(skill_result, "data", None) or {}
        if ok:
            return {
                "ok": True,
                "display": (launched or {}).get("display") or "桌面应用",
                "steps": steps,
                "result": data or msg,
                "via": "desktop_agent",
                "reply": msg
                or (
                    "桌面指令已执行"
                    + (
                        f"；此前已打开「{(launched or {}).get('display')}」"
                        if launched and launched.get("ok")
                        else ""
                    )
                ),
            }
        if launched and launched.get("ok"):
            display = launched.get("display") or "应用"
            return {
                "ok": True,
                "partial": True,
                "display": display,
                "steps": steps,
                "result": launched.get("result"),
                "via": launched.get("via"),
                "reply": (
                    f"已打开/附着「{display}」。"
                    f"后续细粒度操作未完整覆盖（{msg or '无匹配技能'}）。"
                    "可开启共享屏幕后让智能体重试，或先手动点开目标窗口。"
                ),
            }
        return {
            "ok": False,
            "error": msg or "桌面技能未能执行该指令",
            "via": "desktop_agent",
        }
    except Exception as e:
        if launched and launched.get("ok"):
            display = launched.get("display") or "应用"
            return {
                "ok": True,
                "partial": True,
                "display": display,
                "steps": steps,
                "via": launched.get("via"),
                "reply": (
                    f"已打开/附着「{display}」。"
                    f"复杂桌面操作引擎暂不可用（{str(e)[:120]}）。"
                ),
            }
        return {"ok": False, "error": f"桌面执行失败: {str(e)[:200]}"}
