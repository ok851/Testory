# -*- coding: utf-8 -*-
"""
桌面 UIA 元素快照：录制时懒加载，执行时结构定位（COM 线程安全）。
"""

from __future__ import annotations

import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_uia_snapshot 仅支持 Windows")

_VOLATILE_NAME_PATTERNS = (
    re.compile(r"修改日期|大小|类型[:：]|^\d{4}[/\-]", re.I),
    re.compile(r"\d+\s*(KB|MB|GB|字节)", re.I),
)

_DESKTOP_ROOT_NAME_PATTERN = re.compile(r"^(桌面|Desktop|desktop)\s*\d*$", re.I)


def _is_desktop_root_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    return bool(_DESKTOP_ROOT_NAME_PATTERN.match(n))

_TEXT_LIKE = frozenset({"text", "static", "document"})
_LIST_ITEM = "listitem"
_SYS_LISTVIEW = "syslistview32"

_PSEUDO_CONTAINER_PATTERNS = (
    "chrome_renderwidgethosthwnd",
    "chrome_widgetwin",
    "renderwidget",
    "legacy window",
    "corewindow",
    "webview2",
    "cefclient",
    "cefrender",
)

_INTERACTIVE_TYPES = frozenset({
    "button",
    "edit",
    "listitem",
    "hyperlink",
    "combobox",
    "checkbox",
    "radiobutton",
    "menuitem",
    "toolbar",
    "tab",
})

_CONTAINER_TYPES = frozenset({
    "pane",
    "window",
    "group",
    "frame",
    "scrollbar",
    "dialog",
    "client",
    "desktop",
})


@dataclass
class SnapshotCaptureResult:
    ok: bool
    element_snapshot: Optional[Dict[str, Any]] = None
    error_code: str = ""
    message: str = ""
    screen_center: Optional[Tuple[int, int]] = None
    bounding_rect: Optional[Tuple[int, int, int, int]] = None
    element_label: str = ""
    control_type: str = ""
    window_title: str = ""
    process_name: str = ""


@dataclass
class ElementPeekResult:
    ok: bool
    bounding_rect: Optional[Tuple[int, int, int, int]] = None
    element_label: str = ""
    control_type: str = ""
    class_name: str = ""
    error_code: str = ""


@dataclass
class UiaResolveResult:
    ok: bool
    x: int = 0
    y: int = 0
    score: float = 1.0
    error_code: str = ""
    anchor: Optional[Tuple[int, int]] = None
    message: str = ""


def _normalize_class_for_chain(class_name: str) -> str:
    cn = (class_name or "").strip()
    if not cn:
        return ""
    low = cn.lower()
    if low in ("workerw", "progman"):
        return "regex:(WorkerW|Progman)"
    return cn


def _is_volatile_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return True
    for pat in _VOLATILE_NAME_PATTERNS:
        if pat.search(n):
            return True
    return False


def _extract_filename_label(name: str) -> str:
    n = (name or "").strip()
    if not n:
        return ""
    if "\n" in n:
        n = n.split("\n")[0].strip()
    return n


def _element_control_type(el: Any) -> str:
    try:
        if hasattr(el, "friendly_class_name"):
            return (el.friendly_class_name() or "").strip()
    except Exception:
        pass
    try:
        if hasattr(el, "element_info") and el.element_info:
            return (getattr(el.element_info, "control_type", "") or "").strip()
    except Exception:
        pass
    return ""


def _element_class_name(el: Any) -> str:
    try:
        return (el.class_name() or "").strip() if hasattr(el, "class_name") else ""
    except Exception:
        return ""


def _element_name(el: Any) -> str:
    try:
        return (el.window_text() or "").strip() if hasattr(el, "window_text") else ""
    except Exception:
        return ""


def _element_automation_id(el: Any) -> str:
    try:
        return (el.automation_id() or "").strip() if hasattr(el, "automation_id") else ""
    except Exception:
        return ""


def _element_rect(el: Any) -> Optional[Tuple[int, int, int, int]]:
    try:
        if hasattr(el, "rectangle"):
            r = el.rectangle()
            if r and r.width() > 0 and r.height() > 0:
                return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        pass
    return None


def _element_rect_center(el: Any) -> Optional[Tuple[int, int]]:
    rect = _element_rect(el)
    if not rect:
        return None
    l, t, r, b = rect
    return (int((l + r) // 2), int((t + b) // 2))


def _is_pseudo_container(el: Any) -> bool:
    name = (_element_name(el) or "").lower()
    cls = (_element_class_name(el) or "").lower()
    ct = (_element_control_type(el) or "").lower()
    combined = f"{name} {cls} {ct}"
    return any(p in combined for p in _PSEUDO_CONTAINER_PATTERNS)


def _is_interactive_type(ct: str) -> bool:
    return ct.lower() in _INTERACTIVE_TYPES or any(t in ct.lower() for t in _INTERACTIVE_TYPES)


def _is_container_type(ct: str) -> bool:
    return ct.lower() in _CONTAINER_TYPES or any(t in ct.lower() for t in _CONTAINER_TYPES)


def _find_deepest_interactive_element(
    el: Any,
    max_depth: int = 8,
    depth: int = 0,
    visited: Optional[set] = None,
) -> Any:
    if el is None or depth >= max_depth:
        return el

    if visited is None:
        visited = set()

    try:
        el_id = id(el)
        if el_id in visited:
            return el
        visited.add(el_id)
    except Exception:
        pass

    ct = _element_control_type(el).lower()

    if _is_interactive_type(ct):
        return el

    children = []
    try:
        children = list(el.children())
    except Exception:
        pass

    if not children:
        try:
            children = list(el.descendants(depth=1))[:20]
        except Exception:
            return el

    best_child = None
    best_score = -1

    for child in children:
        try:
            child_ct = _element_control_type(child).lower()
            if _is_container_type(child_ct) and not _is_pseudo_container(child):
                continue

            child_rect = _element_rect(child)
            if not child_rect:
                continue

            child_w = child_rect[2] - child_rect[0]
            child_h = child_rect[3] - child_rect[1]
            if child_w < 8 or child_h < 8:
                continue

            result = _find_deepest_interactive_element(child, max_depth, depth + 1, visited)

            if result is None:
                continue

            result_ct = _element_control_type(result).lower()
            score = 0

            if _is_interactive_type(result_ct):
                score += 10
            if result_ct in ("button", "edit", "listitem"):
                score += 5
            if _element_name(result):
                score += 3

            if score > best_score:
                best_score = score
                best_child = result
        except Exception:
            continue

    if best_child is not None:
        return best_child

    return el


def rect_with_padding(
    rect: Tuple[int, int, int, int], *, pad: int = 4, min_side: int = 24
) -> Tuple[int, int, int, int]:
    l, t, r, b = rect
    l -= pad
    t -= pad
    r += pad
    b += pad
    if r - l < min_side:
        cx = (l + r) // 2
        l = cx - min_side // 2
        r = cx + min_side // 2
    if b - t < min_side:
        cy = (t + b) // 2
        t = cy - min_side // 2
        b = cy + min_side // 2
    return int(l), int(t), int(r), int(b)


def _normalize_desktop_hit(el: Any) -> Any:
    """ListView 上 ElementFromPoint 常命中 Text，归一到 ListItem。"""
    if el is None:
        return el
    cur = el
    for _ in range(12):
        ct = _element_control_type(cur).lower()
        cls = _element_class_name(cur).lower()
        if ct == _LIST_ITEM or "listitem" in ct:
            return cur
        if _SYS_LISTVIEW in cls:
            break
        parent = None
        try:
            parent = cur.parent()
        except Exception:
            break
        if parent is None:
            break
        pct = _element_control_type(parent).lower()
        if pct == _LIST_ITEM or "listitem" in pct:
            return parent
        if ct in _TEXT_LIKE or "text" in ct:
            cur = parent
            continue
        cur = parent
    center = _element_rect_center(cur)
    if center:
        try:
            from pywinauto import Desktop  # type: ignore

            hit = Desktop(backend="uia").from_point(center[0], center[1])
            ct2 = _element_control_type(hit).lower()
            if ct2 == _LIST_ITEM or "listitem" in ct2:
                return hit
        except Exception:
            pass
    return cur


def _build_parent_chain(el: Any, *, max_depth: int = 5) -> List[Dict[str, Any]]:
    chain: List[Dict[str, Any]] = []
    cur = el
    depth = 0
    while cur is not None and depth < max_depth:
        ct = _element_control_type(cur)
        if not ct and depth > 0:
            try:
                cur = cur.parent()
            except Exception:
                break
            depth += 1
            continue
        node: Dict[str, Any] = {
            "control_type": ct or "Control",
            "class_name": _normalize_class_for_chain(_element_class_name(cur)),
            "name": _element_name(cur),
            "automation_id": _element_automation_id(cur),
        }
        if depth == max_depth - 1 and ct.lower() in ("window", "pane"):
            low_cls = (_element_class_name(cur) or "").lower()
            if low_cls in ("workerw", "progman"):
                node["app"] = "explorer"
        if _is_desktop_root_name(str(node.get("name", ""))) or (
            ct.lower() in ("window", "pane") and (node.get("class_name") or "").lower() in (
                "regex:(workerw|progman)", "workerw", "progman"
            )
        ):
            break
        chain.insert(0, {k: v for k, v in node.items() if v})
        try:
            cur = cur.parent()
        except Exception:
            break
        depth += 1
    return _merge_redundant_panes(chain)


def _merge_redundant_panes(chain: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(chain) < 2:
        return chain
    out: List[Dict[str, Any]] = []
    for node in chain:
        if (
            out
            and (node.get("control_type") or "").lower() == "pane"
            and (out[-1].get("control_type") or "").lower() == "pane"
            and not node.get("name")
            and not node.get("automation_id")
        ):
            continue
        out.append(node)
    return out


def sanitize_selector(el: Any, parent_chain: List[Dict[str, Any]]) -> Dict[str, Any]:
    ct = _element_control_type(el)
    name = _extract_filename_label(_element_name(el))
    aid = _element_automation_id(el)
    anchor = ct or "Control"
    if "listitem" in anchor.lower():
        anchor = "ListItem"

    key_candidates: List[Dict[str, str]] = []
    if aid and not _is_volatile_name(aid):
        key_candidates.append(
            {"property": "automation_id", "value": aid, "match": "equals"}
        )
    if name and not _is_volatile_name(name):
        key_candidates.append({"property": "uia-name", "value": name, "match": "equals"})
    if not key_candidates and name:
        key_candidates.append({"property": "uia-name", "value": name, "match": "equals"})

    return {
        "anchor_props": anchor,
        "key_candidates": key_candidates,
        "parent_chain": parent_chain,
    }


def _capture_impl(x: int, y: int) -> SnapshotCaptureResult:
    uia_result = SnapshotCaptureResult(ok=False, error_code="no_element", message="未命中控件")

    # 桌面图标：ListView HitTest 精确到单个 ListItem（避免 FolderView 整壳）
    try:
        from desktop_shell_listview import capture_desktop_icon_snapshot_at_point

        icon = capture_desktop_icon_snapshot_at_point(int(x), int(y))
        if icon and icon.get("ok") and icon.get("bounding_rect"):
            return SnapshotCaptureResult(
                ok=True,
                element_snapshot=icon.get("element_snapshot"),
                screen_center=tuple(icon["screen_center"]) if icon.get("screen_center") else (int(x), int(y)),
                bounding_rect=tuple(icon["bounding_rect"]),
                element_label=icon.get("element_label") or "",
                control_type=icon.get("control_type") or "ListItem",
                window_title="桌面",
                process_name="explorer.exe",
            )
    except Exception:
        pass

    try:
        from desktop_uia_core import capture_element_at_point

        core_result = capture_element_at_point(int(x), int(y))
        if core_result.get("ok"):
            rect = core_result.get("bounding_rect")
            label = core_result.get("element_label", "")
            ct = core_result.get("control_type", "")
            cls = core_result.get("class_name", "")
            selector = core_result.get("selector", {}) or {}

            # 桌面根 / 伪容器不得作为成功结果提前返回，继续走 dialog/Win32
            combined = f"{label} {cls} {ct}".lower()
            is_pseudo = any(p in combined for p in _PSEUDO_CONTAINER_PATTERNS)
            if _is_desktop_root_name(label):
                uia_result = SnapshotCaptureResult(
                    ok=False,
                    error_code="desktop_root",
                    message=f"命中桌面根节点「{label}」，非应用元素",
                    bounding_rect=rect if isinstance(rect, (list, tuple)) else None,
                    element_label="",
                    control_type=ct,
                )
            elif is_pseudo:
                uia_result = SnapshotCaptureResult(
                    ok=False,
                    error_code="fake_container",
                    message="UIA仅命中渲染容器，无法识别内部元素",
                    bounding_rect=rect if isinstance(rect, (list, tuple)) else None,
                    element_label=label,
                    control_type=ct,
                )
            elif rect and len(rect) == 4 and (rect[2] - rect[0]) >= 1 and (rect[3] - rect[1]) >= 1:
                # 过大宿主（整窗/整屏）通常不是可点控件，优先交给 Win32 deepest child
                rw = int(rect[2]) - int(rect[0])
                rh = int(rect[3]) - int(rect[1])
                if rw >= 900 or rh >= 700:
                    uia_result = SnapshotCaptureResult(
                        ok=False,
                        error_code="oversized_host",
                        message="UIA命中过大宿主控件，继续深层兜底",
                        bounding_rect=tuple(int(v) for v in rect),
                        element_label=label,
                        control_type=ct,
                    )
                else:
                    center = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
                    snap = {"selector": selector}
                    if cls:
                        snap["class_name"] = cls
                    return SnapshotCaptureResult(
                        ok=True,
                        element_snapshot=snap,
                        screen_center=center,
                        bounding_rect=rect,
                        element_label=label,
                        control_type=ct,
                    )
            # rect 无效或伪容器，回退到 pywinauto / dialog / Win32
        elif core_result.get("error") == "fake_container":
            rect = core_result.get("bounding_rect")
            uia_result = SnapshotCaptureResult(
                ok=False,
                error_code="fake_container",
                message="UIA仅命中渲染容器，无法识别内部元素",
                bounding_rect=rect if isinstance(rect, (list, tuple)) else None,
                element_label=core_result.get("element_label") or "",
                control_type=core_result.get("control_type") or "",
            )
    except Exception:
        pass

    try:
        import pythoncom  # type: ignore

        pythoncom.CoInitialize()
        try:
            from pywinauto import Desktop  # type: ignore

            desktop = Desktop(backend="uia")
            raw = desktop.from_point(int(x), int(y))
            el = _normalize_desktop_hit(raw)

            if el is None:
                uia_result = SnapshotCaptureResult(
                    ok=False, error_code="no_element", message="未命中控件"
                )
            else:
                if _is_pseudo_container(el):
                    el = _find_deepest_interactive_element(el)

                center = _element_rect_center(el)
                bounds = _element_rect(el)
                chain = _build_parent_chain(el)
                selector = sanitize_selector(el, chain)
                label = _extract_filename_label(_element_name(el))
                ct = _element_control_type(el)
                clean_label = (label or "").strip()
                is_desktop_root = _is_desktop_root_name(clean_label)
                if is_desktop_root:
                    # 必须立即失败并继续 dialog/Win32，避免 key_candidates=「桌面」被当成成功
                    uia_result = SnapshotCaptureResult(
                        ok=False,
                        error_code="desktop_root",
                        message=f"命中桌面根节点「{clean_label}」，非应用元素",
                        screen_center=center,
                        bounding_rect=bounds,
                        element_label="",
                        control_type=ct,
                    )
                else:
                    if not selector.get("key_candidates"):
                        label = label or ct or ""
                        if label and not _is_volatile_name(label):
                            selector["key_candidates"] = [
                                {"property": "uia-name", "value": label, "match": "equals"}
                            ]
                            selector["resolved_via"] = "uia_window"
                    if not selector.get("key_candidates"):
                        uia_result = SnapshotCaptureResult(
                            ok=False,
                            error_code="no_stable_key",
                            message="未提取到稳定定位属性",
                            screen_center=center,
                            bounding_rect=bounds,
                            element_label=label,
                            control_type=ct,
                        )
                    else:
                        return SnapshotCaptureResult(
                            ok=True,
                            element_snapshot={"selector": selector},
                            screen_center=center,
                            bounding_rect=bounds,
                            element_label=label,
                            control_type=ct,
                        )
        except Exception as exc:
            err_name = type(exc).__name__
            msg = str(exc) or err_name
            code = "uia_error"
            if "pywintypes" in err_name.lower() or "com" in err_name.lower():
                code = "access_denied"
            if "timeout" in msg.lower():
                code = "timeout"
            uia_result = SnapshotCaptureResult(ok=False, error_code=code, message=msg)
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    except ImportError:
        # 无 pythoncom 时仍继续 dialog / Win32 捕获
        if uia_result.error_code in ("", "no_element"):
            uia_result = SnapshotCaptureResult(
                ok=False, error_code="no_pythoncom", message="缺少 pythoncom，使用 Win32 兜底"
            )

    # dialog / Win32 之前：嵌入式 CDP（CEF/Electron/WebView2）
    try:
        from desktop_embed_cdp import capture_embed_element_at_point

        embed = capture_embed_element_at_point(int(x), int(y))
        if embed and embed.get("ok") and embed.get("element_snapshot"):
            rect = embed.get("bounding_rect")
            center = embed.get("screen_center") or (int(x), int(y))
            return SnapshotCaptureResult(
                ok=True,
                element_snapshot=embed.get("element_snapshot"),
                screen_center=tuple(center) if isinstance(center, (list, tuple)) else (int(x), int(y)),
                bounding_rect=tuple(rect) if rect and len(rect) == 4 else None,
                element_label=embed.get("element_label") or "",
                control_type=embed.get("control_type") or "Element",
                window_title=embed.get("window_title") or "",
                process_name=embed.get("process_name") or "",
            )
    except Exception:
        pass

    try:
        from desktop_dialog_handler import capture_dialog_element_at_point

        dialog = capture_dialog_element_at_point(int(x), int(y))
        if dialog and dialog.ok and dialog.element_snapshot:
            return SnapshotCaptureResult(
                ok=True,
                element_snapshot=dialog.element_snapshot,
                screen_center=dialog.screen_center or (int(x), int(y)),
                bounding_rect=dialog.bounding_rect,
                element_label=dialog.element_label,
                control_type=dialog.control_type,
                window_title=dialog.dialog_title,
            )
    except ImportError:
        pass

    try:
        from desktop_win32_snapshot import capture_win32_element_at_point

        win32 = capture_win32_element_at_point(int(x), int(y))
        if win32.ok and win32.element_snapshot:
            return SnapshotCaptureResult(
                ok=True,
                element_snapshot=win32.element_snapshot,
                screen_center=win32.screen_center or (int(x), int(y)),
                bounding_rect=win32.bounding_rect,
                element_label=win32.element_label,
                control_type=win32.control_type,
                window_title=win32.window_title,
                process_name=win32.process_name,
            )
        if win32.element_label:
            return SnapshotCaptureResult(
                ok=False,
                error_code=win32.error_code or "win32_fallback",
                message=win32.message or "Win32 兜底未提取到结构化信息",
                screen_center=win32.screen_center,
                bounding_rect=win32.bounding_rect,
                element_label=win32.element_label,
                control_type=win32.control_type,
                window_title=win32.window_title,
                process_name=win32.process_name,
            )
    except ImportError:
        pass

    return uia_result


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uia-snap")


def _peek_impl(x: int, y: int, *, wake_app: bool = True) -> ElementPeekResult:
    try:
        from desktop_shell_listview import peek_desktop_icon_at_point

        icon = peek_desktop_icon_at_point(int(x), int(y))
        if icon and icon.screen_rect:
            return ElementPeekResult(
                ok=True,
                bounding_rect=icon.screen_rect,
                element_label=icon.icon_name or "",
                control_type="ListItem",
                class_name="SysListView32",
            )
    except Exception:
        pass

    # 应用窗内：先唤醒无障碍树，再深层 ElementFromPoint（QQ/CEF/Electron）
    if wake_app:
        try:
            from desktop_win32_snapshot import get_window_class, window_from_point

            hwnd = window_from_point(int(x), int(y))
            cls = (get_window_class(hwnd) if hwnd else "") or ""
            low = cls.lower()
            if hwnd and not any(
                k in low for k in ("progman", "workerw", "shell_traywnd", "syslistview32")
            ):
                from desktop_uia_core import wake_accessibility_around_point

                wake_accessibility_around_point(int(x), int(y), max_children=48)
        except Exception:
            pass

    try:
        from desktop_uia_core import capture_element_at_point

        core_result = capture_element_at_point(int(x), int(y))
        if core_result.get("ok"):
            rect = core_result.get("bounding_rect")
            label = core_result.get("element_label", "")
            ct = core_result.get("control_type", "")
            cname = core_result.get("class_name", "") or ""

            if not rect or len(rect) != 4 or (rect[2] - rect[0]) < 1 or (rect[3] - rect[1]) < 1:
                pass
            elif _is_desktop_root_name(label):
                return ElementPeekResult(ok=False, error_code="desktop_root")
            else:
                rw = int(rect[2]) - int(rect[0])
                rh = int(rect[3]) - int(rect[1])
                low = (label or "").lower()
                try:
                    from desktop_highlight_win32 import rect_is_near_fullscreen

                    near_full = rect_is_near_fullscreen(rect)
                except Exception:
                    near_full = rw > 1600 and rh > 900
                if "folderview" in low or near_full:
                    pass
                else:
                    return ElementPeekResult(
                        ok=True,
                        bounding_rect=rect,
                        element_label=label,
                        control_type=ct,
                        class_name=cname,
                    )
        elif core_result.get("error") == "fake_container":
            # 唤醒后再试一次
            try:
                from desktop_uia_core import (
                    capture_element_at_point as _cap2,
                    wake_accessibility_around_point,
                )

                wake_accessibility_around_point(int(x), int(y), max_children=80)
                again = _cap2(int(x), int(y))
                if again.get("ok") and again.get("bounding_rect"):
                    rect = again["bounding_rect"]
                    try:
                        from desktop_highlight_win32 import rect_is_near_fullscreen

                        if not rect_is_near_fullscreen(rect):
                            return ElementPeekResult(
                                ok=True,
                                bounding_rect=rect,
                                element_label=again.get("element_label") or "",
                                control_type=again.get("control_type") or "",
                                class_name=again.get("class_name") or "",
                            )
                    except Exception:
                        return ElementPeekResult(
                            ok=True,
                            bounding_rect=rect,
                            element_label=again.get("element_label") or "",
                            control_type=again.get("control_type") or "",
                            class_name=again.get("class_name") or "",
                        )
            except Exception:
                pass
    except Exception:
        pass

    try:
        import pythoncom  # type: ignore
    except ImportError:
        return ElementPeekResult(ok=False, error_code="no_pythoncom")

    pythoncom.CoInitialize()
    try:
        from pywinauto import Desktop  # type: ignore

        raw = Desktop(backend="uia").from_point(int(x), int(y))
        el = _normalize_desktop_hit(raw)
        if el is None:
            return ElementPeekResult(ok=False, error_code="no_element")

        if _is_pseudo_container(el):
            el = _find_deepest_interactive_element(el)

        bounds = _element_rect(el)
        if not bounds:
            return ElementPeekResult(ok=False, error_code="no_rect")
        label = _extract_filename_label(_element_name(el))
        ct = _element_control_type(el)
        cname = _element_class_name(el)
        if _is_desktop_root_name(label):
            return ElementPeekResult(ok=False, error_code="desktop_root")
        low = (label or "").lower()
        bw = bounds[2] - bounds[0]
        bh = bounds[3] - bounds[1]
        if "folderview" in low or (
            _SYS_LISTVIEW in (cname or "").lower() and (bw > 400 or bh > 400)
        ):
            return ElementPeekResult(ok=False, error_code="desktop_shell")
        try:
            from desktop_highlight_win32 import rect_is_near_fullscreen

            if rect_is_near_fullscreen(bounds):
                return ElementPeekResult(ok=False, error_code="near_fullscreen")
        except Exception:
            pass
        return ElementPeekResult(
            ok=True,
            bounding_rect=bounds,
            element_label=label,
            control_type=ct,
            class_name=cname or "",
        )
    except Exception as exc:
        code = "access_denied" if "com" in type(exc).__name__.lower() else "peek_error"
        return ElementPeekResult(ok=False, error_code=code)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def peek_element_at_point(
    x: int, y: int, *, timeout_sec: float = 0.4, wake_app: bool = True
) -> ElementPeekResult:
    """悬停高亮：仅取控件矩形，不构建完整快照。"""
    try:
        fut = _executor.submit(_peek_impl, int(x), int(y), wake_app=bool(wake_app))
        return fut.result(timeout=float(timeout_sec))
    except FuturesTimeout:
        return ElementPeekResult(ok=False, error_code="timeout")
    except Exception:
        return ElementPeekResult(ok=False, error_code="peek_error")


def capture_element_snapshot_at_point(
    x: int, y: int, *, timeout_sec: float = 2.0
) -> SnapshotCaptureResult:
    """在独立 COM 线程捕获 UIA 快照；失败不抛错。"""
    try:
        fut = _executor.submit(_capture_impl, int(x), int(y))
        return fut.result(timeout=float(timeout_sec))
    except FuturesTimeout:
        return SnapshotCaptureResult(
            ok=False, error_code="timeout", message="UIA 快照超时"
        )
    except Exception as exc:
        return SnapshotCaptureResult(
            ok=False, error_code="uia_error", message=str(exc)
        )


def _control_type_matches(expected: str, actual: str) -> bool:
    e = (expected or "").strip().lower()
    a = (actual or "").strip().lower()
    if not e:
        return True
    if e == a or e in a or a in e:
        return True
    equiv = {
        "list": {"list", "listbox", "listview"},
        "listbox": {"list", "listbox", "listview"},
        "listview": {"list", "listbox", "listview"},
        "pane": {"pane", "window"},
        "window": {"pane", "window"},
    }
    return a in equiv.get(e, {e})


def _name_matches(expected: str, actual: str) -> bool:
    exp = (expected or "").strip()
    act = (actual or "").strip()
    if not exp:
        return True
    if not act:
        return False
    if exp == act:
        return True
    return exp.lower() in act.lower() or act.lower() in exp.lower()


def _class_matches(expected: str, actual: str) -> bool:
    exp = (expected or "").strip()
    act = (actual or "").strip()
    if not exp:
        return True
    if exp.startswith("regex:"):
        pat = exp[6:].strip()
        try:
            return bool(re.search(pat, act, re.I))
        except re.error:
            return exp.lower() == act.lower()
    return exp.lower() == act.lower()


def _match_key_candidates(pool: List[Any], candidates: List[Dict[str, Any]]) -> Optional[Any]:
    for cand in candidates:
        prop = (cand.get("property") or "").strip().lower()
        val = (cand.get("value") or "").strip()
        match_mode = (cand.get("match") or "equals").strip().lower()
        if not val:
            continue
        for node in pool:
            if prop == "automation_id":
                if _element_automation_id(node) == val:
                    return node
            elif prop in ("uia-name", "name"):
                nm = _element_name(node)
                if match_mode == "contains" and val in nm:
                    return node
                if _name_matches(val, nm):
                    return node
    return None


def _resolve_desktop_listitem_fallback(
    selector: Dict[str, Any],
) -> Optional[Tuple[Any, Tuple[int, int]]]:
    """桌面图标：在 Explorer 桌面 ListView 中按 key_candidates 查找 ListItem。"""
    from pywinauto import Desktop  # type: ignore

    candidates = selector.get("key_candidates") or []
    if not candidates:
        return None
    anchor = (selector.get("anchor_props") or "ListItem").lower()
    want_listitem = "listitem" in anchor

    def _scan_container(container: Any) -> Optional[Any]:
        try:
            nodes = [container] + list(container.descendants())[:400]
        except Exception:
            nodes = [container]
        for node in nodes:
            ct = _element_control_type(node).lower()
            if want_listitem and "listitem" not in ct:
                continue
            if not want_listitem and ct and "listitem" in ct:
                pass
            hit = _match_key_candidates([node], candidates)
            if hit is not None:
                return hit
        return None

    desktop = Desktop(backend="uia")
    for win in desktop.windows():
        try:
            cls = _element_class_name(win).lower()
            title = _element_name(win).lower()
            if cls not in ("workerw", "progman") and "program manager" not in title:
                continue
            hit = _scan_container(win)
            if hit is not None:
                center = _element_rect_center(hit)
                if center:
                    return hit, center
        except Exception:
            continue
    try:
        hit = _scan_container(desktop)
        if hit is not None:
            center = _element_rect_center(hit)
            if center:
                return hit, center
    except Exception:
        pass
    return None


def _resolve_impl(selector: Dict[str, Any]) -> UiaResolveResult:
    try:
        import pythoncom  # type: ignore
    except ImportError:
        return UiaResolveResult(ok=False, error_code="no_pythoncom")

    pythoncom.CoInitialize()
    try:
        from pywinauto import Desktop  # type: ignore

        desktop = Desktop(backend="uia")
        chain = selector.get("parent_chain") or []
        candidates = selector.get("key_candidates") or []
        # 桌面图标：父级链在 WorkerW/Progman 切换时易断，优先按名称扫 ListItem
        if candidates:
            fb_early = _resolve_desktop_listitem_fallback(selector)
            if fb_early:
                _hit, center = fb_early
                return UiaResolveResult(
                    ok=True, x=center[0], y=center[1], score=1.0, anchor=center
                )
        root = desktop
        el = None
        for node in chain:
            ct = (node.get("control_type") or "").strip()
            cls = node.get("class_name") or ""
            nm = node.get("name") or ""
            children = []
            try:
                children = root.children()
            except Exception:
                children = []
            if not children:
                try:
                    children = root.descendants(depth=1)
                except Exception:
                    return UiaResolveResult(ok=False, error_code="chain_break")
            found = None
            for ch in children:
                ch_ct = _element_control_type(ch)
                if ct and not _control_type_matches(ct, ch_ct):
                    continue
                if cls and not _class_matches(cls, _element_class_name(ch)):
                    continue
                if nm and not _name_matches(nm, _element_name(ch)):
                    continue
                found = ch
                break
            if found is None:
                fb = _resolve_desktop_listitem_fallback(selector)
                if fb:
                    _hit, center = fb
                    return UiaResolveResult(
                        ok=True, x=center[0], y=center[1], score=1.0, anchor=center
                    )
                return UiaResolveResult(ok=False, error_code="chain_miss")
            root = found
            el = found

        if el is None:
            fb = _resolve_desktop_listitem_fallback(selector)
            if fb:
                _hit, center = fb
                return UiaResolveResult(
                    ok=True, x=center[0], y=center[1], score=1.0, anchor=center
                )
            return UiaResolveResult(ok=False, error_code="no_target")

        target = el
        if candidates:
            try:
                pool = [el] + list(el.descendants())[:200]
            except Exception:
                pool = [el]
            matched = _match_key_candidates(pool, candidates)
            if matched is not None:
                target = matched

        center = _element_rect_center(target)
        if not center:
            return UiaResolveResult(ok=False, error_code="no_rect")
        return UiaResolveResult(
            ok=True, x=center[0], y=center[1], score=1.0, anchor=center
        )
    except Exception as exc:
        err_name = type(exc).__name__.lower()
        code = (
            "access_denied"
            if "pywintypes" in err_name or "com" in err_name
            else "uia_error"
        )
        return UiaResolveResult(ok=False, error_code=code, message=str(exc))
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def resolve_uia_click_point(
    element_snapshot: Dict[str, Any], *, timeout_sec: float = 3.0
) -> UiaResolveResult:
    sel = (element_snapshot or {}).get("selector") or element_snapshot
    if not isinstance(sel, dict):
        return UiaResolveResult(ok=False, error_code="invalid_selector")
    try:
        fut = _executor.submit(_resolve_impl, sel)
        return fut.result(timeout=float(timeout_sec))
    except FuturesTimeout:
        return UiaResolveResult(ok=False, error_code="timeout")
    except Exception as exc:
        return UiaResolveResult(ok=False, error_code="uia_error", message=str(exc))


def _resolve_win32_impl(selector: Dict[str, Any]) -> UiaResolveResult:
    candidates = selector.get("key_candidates") or []
    if not candidates:
        return UiaResolveResult(ok=False, error_code="no_candidates")

    parent_chain = selector.get("parent_chain") or []
    window_name = ""
    for node in parent_chain:
        ct = (node.get("control_type") or "").strip().lower()
        if ct == "window":
            window_name = (node.get("name") or "").strip()
            break

    try:
        from desktop_win32_snapshot import (
            enumerate_child_windows,
            get_top_level_window,
            get_window_rect,
            get_window_text,
            window_from_point,
        )
        from desktop_input import _enum_visible_windows

        target_name = (candidates[0].get("value") or "").strip()
        if not target_name:
            return UiaResolveResult(ok=False, error_code="no_target_name")

        for hwnd, title, cls_name in _enum_visible_windows():
            if window_name and window_name not in title:
                continue
            if not window_name and not title:
                continue

            children = enumerate_child_windows(hwnd, max_depth=3)
            for child in children:
                child_name = child.get("name") or ""
                if target_name.lower() in child_name.lower():
                    center = child.get("center")
                    if center:
                        return UiaResolveResult(
                            ok=True,
                            x=center[0],
                            y=center[1],
                            score=0.85,
                            anchor=center,
                        )
    except ImportError:
        pass

    return UiaResolveResult(ok=False, error_code="win32_miss")


def resolve_win32_click_point(
    element_snapshot: Dict[str, Any], *, timeout_sec: float = 3.0
) -> UiaResolveResult:
    sel = (element_snapshot or {}).get("selector") or element_snapshot
    if not isinstance(sel, dict):
        return UiaResolveResult(ok=False, error_code="invalid_selector")
    try:
        fut = _executor.submit(_resolve_win32_impl, sel)
        return fut.result(timeout=float(timeout_sec))
    except FuturesTimeout:
        return UiaResolveResult(ok=False, error_code="timeout")
    except Exception as exc:
        return UiaResolveResult(ok=False, error_code="win32_error", message=str(exc))
