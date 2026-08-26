# -*- coding: utf-8 -*-
"""
Shell.Application COM：按名称打开桌面图标，零 UI、不截图、不抢鼠标。

适用于 ListItem 类桌面步骤；优先于 SysListView32 PostMessage 与视觉匹配。
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

if sys.platform != "win32":
    raise RuntimeError("desktop_shell_application 仅支持 Windows")

# CSIDL_DESKTOP = 0
_DESKTOP_NAMESPACE_ID = 0


def shell_com_enabled() -> bool:
    raw = (os.environ.get("DESKTOP_SHELL_COM") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass
class ShellComTarget:
    icon_name: str
    matched_name: str


def _strip_guid_suffix(name: str) -> str:
    n = (name or "").strip()
    if ".{" in n:
        return n.split(".{", 1)[0].strip()
    return n


def _match_shell_item_name(actual: str, expected: str) -> bool:
    from modules.desktop.desktop_shell_listview import _match_icon_name

    a = (actual or "").strip()
    e = (expected or "").strip()
    if not a or not e:
        return False
    if _match_icon_name(a, e):
        return True
    return _match_icon_name(_strip_guid_suffix(a), _strip_guid_suffix(e))


def _get_desktop_namespace() -> Any:
    try:
        import win32com.client
    except ImportError as exc:
        logger.info("shell_com: 缺少 pywin32: %s", exc)
        return None
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        return shell.NameSpace(_DESKTOP_NAMESPACE_ID)
    except Exception as exc:
        logger.info("shell_com: 无法获取 Shell.Application 桌面命名空间: %s", exc)
        return None


def find_desktop_item_by_name(icon_name: str) -> Optional[Tuple[str, Any]]:
    """返回 (matched_name, shell_item) 或 None。"""
    expected = (icon_name or "").strip()
    if not expected:
        return None
    namespace = _get_desktop_namespace()
    if namespace is None:
        return None
    try:
        items = namespace.Items()
    except Exception as exc:
        logger.info("shell_com: 枚举桌面项失败: %s", exc)
        return None
    try:
        count = int(items.Count)
    except Exception:
        count = -1
    if count == 0:
        logger.info("shell_com: 桌面命名空间无项目")
        return None
    try:
        iterator = iter(items)
    except TypeError:
        iterator = (items.Item(i) for i in range(max(0, count)))
    for item in iterator:
        try:
            nm = (item.Name or "").strip()
        except Exception:
            continue
        if _match_shell_item_name(nm, expected):
            return nm, item
    logger.info("shell_com: 桌面中未找到图标「%s」", expected)
    return None


def invoke_desktop_item(item: Any, action: str) -> None:
    """执行 Shell 动词（默认 open ≈ 双击打开）。"""
    act = (action or "click").strip().lower()
    if act == "right_click":
        for verb in ("properties", "属性"):
            try:
                item.InvokeVerb(verb)
                return
            except Exception:
                continue
        raise RuntimeError("Shell COM 无法对桌面项执行右键/属性动词")
    if act == "double_click":
        try:
            item.InvokeVerb("open")
            return
        except Exception:
            pass
    try:
        item.InvokeVerb()
    except Exception as exc:
        raise RuntimeError(f"Shell COM InvokeVerb 失败: {exc}") from exc


def resolve_shell_application_icon(icon_name: str) -> Optional[ShellComTarget]:
    found = find_desktop_item_by_name(icon_name)
    if not found:
        return None
    matched_name, _item = found
    return ShellComTarget(icon_name=(icon_name or "").strip(), matched_name=matched_name)


def execute_shell_application_action(
    step: dict,
    action: str,
    *,
    target: Optional[ShellComTarget] = None,
) -> ShellComTarget:
    from modules.desktop.desktop_shell_listview import icon_name_from_step

    resolved = target
    name = (resolved.icon_name if resolved else "") or icon_name_from_step(step)
    if not name:
        raise RuntimeError("Shell COM 桌面操作缺少图标名称")
    found = find_desktop_item_by_name(name)
    if not found:
        raise RuntimeError(f"Shell COM 桌面中未找到图标「{name}」")
    matched_name, item = found
    invoke_desktop_item(item, action)
    return ShellComTarget(icon_name=name, matched_name=matched_name)


def try_resolve_shell_application_step(step: dict) -> Optional[ShellComTarget]:
    if not shell_com_enabled():
        return None
    from modules.desktop.desktop_shell_listview import icon_name_from_step, is_desktop_listitem_step

    if not is_desktop_listitem_step(step):
        return None
    name = icon_name_from_step(step)
    if not name:
        return None
    return resolve_shell_application_icon(name)
