# -*- coding: utf-8 -*-
"""
桌面自动化环境配置（从 .env 读取，用户只需在项目 .env 配置一次）。

DESKTOP_DEFAULT_LAUNCH_PATH   默认启动 exe（launch_app 步骤 input 为空时使用）
DESKTOP_DEFAULT_ATTACH_TITLE_RE  默认附着窗口标题正则（attach_window / 空 spec）
DESKTOP_APP_ALIASES           JSON，如 {"erp":"C:\\\\ERP\\\\client.exe","default":"notepad.exe"}
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


def load_app_aliases() -> Dict[str, str]:
    """.env 别名 + 本机开始菜单自动目录（目录项可被 .env 覆盖）。"""
    merged: Dict[str, str] = {}
    try:
        from desktop_app_catalog import catalog_aliases_map

        merged.update(catalog_aliases_map())
    except Exception:
        pass
    raw = (os.environ.get("DESKTOP_APP_ALIASES") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for k, v in data.items():
                    if v:
                        merged[str(k).strip().lower()] = str(v).strip()
        except json.JSONDecodeError:
            pass
    return merged


def default_launch_path() -> str:
    return (os.environ.get("DESKTOP_DEFAULT_LAUNCH_PATH") or "").strip()


def default_attach_title_re() -> str:
    return (os.environ.get("DESKTOP_DEFAULT_ATTACH_TITLE_RE") or "").strip()


def resolve_path_or_alias(value: str) -> str:
    """input_value 可为完整路径、notepad.exe、或别名 erp / @erp。"""
    v = (value or "").strip()
    if not v:
        return ""
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

    action = (s.get("action") or "").strip()
    iv = (s.get("input_value") or "").strip()
    sel = (s.get("selector_value") or "").strip()
    # 用户常把程序名/窗口标题误填在「选择器」列，此处回退到 input_value
    if not iv and sel and action in ("launch_app", "attach_window"):
        iv = sel
        s["input_value"] = iv

    if action == "launch_app":
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
            path = smart_resolve_launch_path(path)
            spec["path"] = path
            if not (s.get("input_value") or "").strip():
                s["input_value"] = path
    elif action == "attach_window":
        if iv and not spec.get("window_title_re") and not spec.get("window_title"):
            if iv.startswith("re:"):
                spec["window_title_re"] = iv[3:].strip()
            elif "*" in iv or iv.startswith("."):
                spec["window_title_re"] = iv
            else:
                spec["window_title_re"] = f".*{re.escape(iv)}.*"
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
    path = (
        resolve_path_or_alias((s.get("input_value") or "").strip())
        or (spec.get("path") or spec.get("exe") or "").strip()
    )
    if not path:
        return (
            "launch_app 未填写程序名。请在步骤「输入值」填写 calc.exe 等，"
            "或改用「附着窗口」+「选择当前窗口」。"
        )
    meta = resolve_executable_with_meta(path)
    if meta.found:
        return None
    return format_resolve_error(meta)
