# -*- coding: utf-8 -*-
"""
桌面自动化环境配置（从 .env 读取，用户只需在项目 .env 配置一次）。

DESKTOP_DEFAULT_LAUNCH_PATH   默认启动 exe（launch_app 步骤 input 为空时使用）
DESKTOP_DEFAULT_ATTACH_TITLE_RE  默认附着窗口标题正则（attach_window / 空 spec）
DESKTOP_APP_ALIASES           JSON。字符串形式：{"erp":"C:\\\\ERP\\\\client.exe"}；
                              对象形式（可带启动参数）：{"erp":{"path":"...","args":["..."],"window_title_re":".*"}}
DESKTOP_DEFAULT_BACKEND       uia | win32，默认 uia
DESKTOP_STEP_RETRY            桌面指针步骤失败后的额外重试次数（默认 1）
DESKTOP_FAILURE_SCREENSHOT    指针失败时保存虚拟桌面截图（默认 1）
无人值守：本地版用 DESKTOP_EXECUTION_MODE=inprocess + desktop_automation_gateway；
企业多机用 DEPLOYMENT_PROFILE=enterprise + DESKTOP_EXECUTION_MODE=remote。
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any, Dict, Optional

try:
    from desktop_discovery import (
        discovery_available,
        format_resolve_error,
        resolve_executable_with_meta,
        smart_resolve_launch_path,
    )
except ImportError:
    def discovery_available() -> bool:
        return False

    def smart_resolve_launch_path(raw: str) -> str:
        return (raw or "").strip()

    def resolve_executable_with_meta(query: str):  # type: ignore
        return None

    def format_resolve_error(meta) -> str:  # type: ignore
        return "找不到可执行程序"

_ALIAS_RE = re.compile(r"^@?([a-zA-Z0-9_-]+)$")


def _truthy(name: str, default: str = "1") -> bool:
    return (os.environ.get(name, default) or default).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def deployment_profile() -> str:
    """local（默认）| enterprise（多机远程桌面等）。"""
    raw = (os.environ.get("DEPLOYMENT_PROFILE") or "local").strip().lower()
    return raw if raw in ("local", "enterprise") else "local"


def is_local_deployment() -> bool:
    return deployment_profile() == "local"


def desktop_execution_mode() -> str:
    """
    inprocess：本机 DesktopWorker（本地版默认）
    gateway：HTTP 调本机 desktop_automation_gateway
    remote：HTTP 调远程 Agent（仅 enterprise + 显式配置）
    """
    raw = (os.environ.get("DESKTOP_EXECUTION_MODE") or "").strip().lower()
    if not raw:
        return "inprocess" if is_local_deployment() else "gateway"
    if raw not in ("inprocess", "gateway", "remote"):
        return "inprocess"
    return raw


def remote_desktop_enabled() -> bool:
    """remote 模式且允许走远程 Agent（本地版默认关闭）。"""
    if desktop_execution_mode() != "remote":
        return False
    if is_local_deployment():
        return _truthy("DESKTOP_ALLOW_REMOTE_IN_LOCAL", "0")
    return True


def desktop_auto_start_gateway() -> bool:
    if desktop_execution_mode() == "inprocess":
        return False
    if is_local_deployment():
        return _truthy("DESKTOP_AUTO_START_GATEWAY", "0")
    return _truthy("DESKTOP_AUTO_START_GATEWAY", "1")


def desktop_default_backend() -> str:
    be = (os.environ.get("DESKTOP_DEFAULT_BACKEND") or "uia").strip().lower()
    return be if be in ("uia", "win32") else "uia"


def _normalize_alias_entry(key: str, value: Any) -> Optional[Dict[str, Any]]:
    """将别名值规范为 {path, args?, window_title_re?, alias}。"""
    name = (key or "").strip().lower()
    if not name:
        return None
    if isinstance(value, str):
        path = value.strip()
        if not path:
            return None
        return {"alias": name, "path": path, "args": [], "window_title_re": ""}
    if isinstance(value, dict):
        path = str(value.get("path") or value.get("exe") or value.get("launch") or "").strip()
        if not path:
            return None
        raw_args = value.get("args") or value.get("arguments") or []
        args: list = []
        if isinstance(raw_args, (list, tuple)):
            args = [str(a) for a in raw_args]
        elif isinstance(raw_args, str) and raw_args.strip():
            args = [raw_args.strip()]
        title_re = str(
            value.get("window_title_re")
            or value.get("title_re")
            or value.get("window_title")
            or ""
        ).strip()
        return {
            "alias": name,
            "path": path,
            "args": args,
            "window_title_re": title_re,
        }
    return None


def load_app_alias_specs() -> Dict[str, Dict[str, Any]]:
    """.env / 目录别名 → 规范化规格（含可选 args / window_title_re）。"""
    merged: Dict[str, Dict[str, Any]] = {}
    try:
        from desktop_app_catalog import catalog_aliases_map

        for k, v in (catalog_aliases_map() or {}).items():
            entry = _normalize_alias_entry(str(k), v)
            if entry:
                merged[entry["alias"]] = entry
    except Exception:
        pass
    raw = (os.environ.get("DESKTOP_APP_ALIASES") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for k, v in data.items():
                    entry = _normalize_alias_entry(str(k), v)
                    if entry:
                        merged[entry["alias"]] = entry
        except json.JSONDecodeError:
            pass
    return merged


def load_app_aliases() -> Dict[str, str]:
    """.env 别名 + 本机开始菜单自动目录（目录项可被 .env 覆盖）；仅返回 path 字符串。"""
    return {k: str(v.get("path") or "") for k, v in load_app_alias_specs().items() if v.get("path")}


def substitute_alias_tokens(text: str, variables: Optional[Dict[str, Any]] = None) -> str:
    """替换别名 args / 标题中的 {order_id} / {{order_id}}。"""
    s = str(text or "")
    if not s:
        return s
    vars_map = {str(k): "" if v is None else str(v) for k, v in (variables or {}).items()}
    for k, v in vars_map.items():
        s = s.replace("{{" + k + "}}", v).replace("{" + k + "}", v)
    return s


def resolve_launch_spec(
    value: str,
    *,
    variables: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """解析 launch 输入：别名 erp/@erp → {path, args, window_title_re, alias}；非别名返回 None。"""
    v = (value or "").strip()
    if not v:
        return None
    key = v[1:].strip().lower() if v.startswith("@") else v.lower()
    # 仅纯别名键（无路径分隔符）才查表，避免把 C:\\erp\\a.exe 当别名
    if "/" in key or "\\" in key or key.endswith(".exe") or key.endswith(".bat"):
        return None
    m = _ALIAS_RE.match(key if not key.startswith("@") else key[1:])
    if not m and _ALIAS_RE.match(v.lstrip("@")):
        m = _ALIAS_RE.match(v.lstrip("@"))
    if not m:
        # 允许 key 直接命中别名表（如 erp）
        specs = load_app_alias_specs()
        if key not in specs:
            return None
        entry = dict(specs[key])
    else:
        specs = load_app_alias_specs()
        alias_key = m.group(1).lower()
        if alias_key not in specs:
            return None
        entry = dict(specs[alias_key])
    vars_map = dict(variables or {})
    entry["path"] = substitute_alias_tokens(entry.get("path") or "", vars_map)
    entry["args"] = [
        substitute_alias_tokens(a, vars_map) for a in (entry.get("args") or [])
    ]
    if entry.get("window_title_re"):
        entry["window_title_re"] = substitute_alias_tokens(
            entry.get("window_title_re") or "", vars_map
        )
    return entry


def resolve_path_or_alias(value: str) -> str:
    """input_value 可为完整路径、notepad.exe、或别名 erp / @erp。"""
    v = (value or "").strip()
    if not v:
        return ""
    launch = resolve_launch_spec(v)
    if launch and launch.get("path"):
        return str(launch["path"])
    m = _ALIAS_RE.match(v)
    if m:
        key = m.group(1).lower()
        aliases = load_app_aliases()
        if key in aliases:
            return aliases[key]
    aliases = load_app_aliases()
    if v.lower() in aliases:
        return aliases[v.lower()]
    return v


def default_launch_path() -> str:
    return (os.environ.get("DESKTOP_DEFAULT_LAUNCH_PATH") or "").strip()


def default_attach_title_re() -> str:
    return (os.environ.get("DESKTOP_DEFAULT_ATTACH_TITLE_RE") or "").strip()


def auto_attach_default_title() -> bool:
    """为 true 时，attach_window 未指定窗口才使用 DESKTOP_DEFAULT_ATTACH_TITLE_RE。"""
    return _truthy("DESKTOP_AUTO_ATTACH_DEFAULT", "0")


def merge_desktop_spec(spec: Dict[str, Any], action: str = "") -> Dict[str, Any]:
    out = dict(spec or {})
    if not out.get("backend"):
        out["backend"] = desktop_default_backend()
    act = (action or "").strip().lower()
    if act == "attach_window" and auto_attach_default_title():
        if not (out.get("window_title_re") or out.get("window_title") or out.get("process")):
            tre = default_attach_title_re()
            if tre:
                out["window_title_re"] = tre
    return out


def prepare_desktop_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """
    用 .env 默认值补全步骤，避免每步手写 desktop_spec.path。
    返回新 dict，不修改入参。
    """
    s = deepcopy(step)
    raw_spec = s.get("desktop_spec")
    if isinstance(raw_spec, str) and raw_spec.strip():
        try:
            spec = json.loads(raw_spec)
        except json.JSONDecodeError:
            spec = {}
    elif isinstance(raw_spec, dict):
        spec = dict(raw_spec)
    else:
        spec = {}

    tc = (spec.get("title_contains") or spec.get("title") or "").strip()
    if tc and not spec.get("window_title_re") and not spec.get("window_title"):
        spec["window_title_re"] = f".*{re.escape(tc)}.*"
    # 兼容 AI/模板常用 title_re
    if spec.get("title_re") and not spec.get("window_title_re"):
        spec["window_title_re"] = str(spec.get("title_re")).strip()

    action = (s.get("action") or "").strip()
    iv = (s.get("input_value") or "").strip()
    sel = (s.get("selector_value") or "").strip()
    # 用户常把程序名/窗口标题误填在「选择器」列，此处回退到 input_value
    if not iv and sel and action in ("launch_app", "attach_window"):
        iv = sel
        s["input_value"] = iv

    if action == "launch_app":
        has_args = bool(spec.get("args") or s.get("args"))
        alias_key = (spec.get("alias") or "").strip() or iv
        launch = resolve_launch_spec(alias_key) if alias_key else None
        if launch and not has_args and launch.get("args"):
            # 别名对象带 args：写入 spec，避免把整段命令行当 exe
            spec["path"] = launch["path"]
            spec["args"] = list(launch["args"])
            spec["alias"] = launch.get("alias") or alias_key.lstrip("@")
            if launch.get("window_title_re") and not spec.get("window_title_re"):
                spec["window_title_re"] = launch["window_title_re"]
            has_args = True
            path = launch["path"]
            s["input_value"] = f"@{launch.get('alias') or alias_key.lstrip('@')}"
        elif has_args and (spec.get("path") or spec.get("exe")):
            # 带参数启动：path 为解释器/主程序，勿把整段命令行当 exe 解析
            path = (spec.get("path") or spec.get("exe") or "").strip()
            if launch and not path:
                path = str(launch.get("path") or "")
        else:
            path = (
                resolve_path_or_alias(iv)
                or (spec.get("path") or spec.get("exe") or "").strip()
                or default_launch_path()
            )
        # 仅当步骤或 .env 显式写了 default / @default 时才走别名 default，避免空步骤总打开记事本
        if not path and iv.lower() in ("default", "@default"):
            path = resolve_path_or_alias("default")
        if not path:
            fb = (os.environ.get("DESKTOP_LAUNCH_FALLBACK") or "").strip()
            if fb and discovery_available():
                path = smart_resolve_launch_path(fb)
        if path:
            if not has_args:
                path = smart_resolve_launch_path(path)
            spec["path"] = path
            if not (s.get("input_value") or "").strip():
                s["input_value"] = path
    elif action == "attach_window":
        from desktop_run_context import guess_window_title_from_description, spec_has_window_target

        title_hint = (
            (s.get("selector_value") or "").strip()
            or guess_window_title_from_description(s.get("description") or "")
            or iv
        )
        if title_hint.lower() in ("exist", "visible", "clickable", "auto", "ok", "success"):
            title_hint = guess_window_title_from_description(s.get("description") or "") or iv
        if iv and not spec.get("window_title_re") and not spec.get("window_title"):
            if iv.startswith("re:"):
                spec["window_title_re"] = iv[3:].strip()
            elif "*" in iv or iv.startswith("."):
                spec["window_title_re"] = iv
            else:
                spec["window_title_re"] = f".*{re.escape(iv)}.*"
        if title_hint and not spec_has_window_target(spec):
            spec["title_contains"] = title_hint
            spec["window_title_re"] = f".*{re.escape(title_hint)}.*"
        if iv and not spec.get("process"):
            m = _ALIAS_RE.match(iv)
            if m and m.group(1).lower() in load_app_aliases():
                spec["path"] = resolve_path_or_alias(iv)
    elif action == "wait" and (s.get("compare_type") or "").lower() == "window":
        if iv and not spec.get("window_title_re"):
            spec["window_title_re"] = iv if ("*" in iv or iv.startswith(".")) else f".*{re.escape(iv)}.*"

    if spec.get("best_match") in (False, 0, "0", "false", "no", "off"):
        spec.pop("best_match", None)

    spec = merge_desktop_spec(spec, action=action)
    if spec:
        s["desktop_spec"] = spec
    return s


def launch_path_hint() -> str:
    p = default_launch_path()
    aliases = load_app_aliases()
    parts = []
    if p:
        parts.append(f"默认启动: {p}")
    if aliases:
        parts.append("别名: " + ", ".join(sorted(aliases.keys())))
    if discovery_available():
        return (
            "；".join(parts) + "；也可在步骤里点「选择当前窗口」或填程序名（如 notepad），无需改 .env"
            if parts
            else "无需配置 .env：请用「附着窗口」+「选择当前窗口」，或 launch_app 填程序名（如 notepad.exe）"
        )
    return "；".join(parts) if parts else "未配置默认路径（Windows 下可用「选择当前窗口」）"


def _catalog_public_meta() -> Dict[str, Any]:
    try:
        from desktop_app_catalog import catalog_meta

        return catalog_meta()
    except Exception:
        return {}


def public_config() -> Dict[str, Any]:
    return {
        "deployment_profile": deployment_profile(),
        "desktop_execution_mode": desktop_execution_mode(),
        "remote_desktop_enabled": remote_desktop_enabled(),
        "default_launch_path": default_launch_path(),
        "default_attach_title_re": default_attach_title_re() or ".*",
        "app_aliases": load_app_aliases(),
        "app_alias_specs": {
            k: {
                "path": v.get("path"),
                "has_args": bool(v.get("args")),
                "window_title_re": v.get("window_title_re") or "",
            }
            for k, v in load_app_alias_specs().items()
        },
        "default_backend": desktop_default_backend(),
        "auto_start_gateway": desktop_auto_start_gateway(),
        "gateway_url": (os.environ.get("DESKTOP_AGENT_GATEWAY_URL") or "").strip(),
        "hint": launch_path_hint(),
        "discovery_available": discovery_available(),
        "catalog": _catalog_public_meta(),
        "zero_config_hint": (
            "推荐：先手动打开被测客户端，添加「附着窗口」步骤并点「选择当前窗口」；"
            "或 launch_app 直接填 notepad.exe 等程序名，系统自动解析路径。"
            if discovery_available()
            else ""
        ),
    }


def desktop_operation_timeout(action: str = "") -> float:
    """桌面 Worker 单步超时（秒），launch/attach 默认更长。"""
    try:
        base = float(os.environ.get("DESKTOP_OPERATION_TIMEOUT", "120"))
    except ValueError:
        base = 120.0
    act = (action or "").strip().lower()
    if act in ("launch_app", "attach_window"):
        try:
            extra = float(os.environ.get("DESKTOP_LAUNCH_TIMEOUT", "0"))
        except ValueError:
            extra = 0.0
        if extra > 0:
            return extra
        return max(base, 120.0)
    return max(30.0, base)


def validate_launch_app_ready(step: Dict[str, Any]) -> Optional[str]:
    """若仍无法 launch，返回用户可读错误。"""
    s = prepare_desktop_step(step)
    spec = s.get("desktop_spec") if isinstance(s.get("desktop_spec"), dict) else {}
    if not isinstance(spec, dict):
        spec = {}
    has_args = bool(spec.get("args") or s.get("args"))
    if has_args and (spec.get("path") or spec.get("exe")):
        path = (spec.get("path") or spec.get("exe") or "").strip()
    else:
        path = (
            resolve_path_or_alias((s.get("input_value") or "").strip())
            or (spec.get("path") or spec.get("exe") or "").strip()
        )
    if not path:
        return (
            "launch_app 未填写程序名。请在步骤「输入值」填写 calc.exe 等，"
            "或改用「附着窗口」+「选择当前窗口」。"
        )
    # 带 args 时 path 常为已存在的 python.exe / 绝对路径
    if has_args and os.path.isfile(path):
        return None
    meta = resolve_executable_with_meta(path)
    if meta.found:
        return None
    return format_resolve_error(meta)
