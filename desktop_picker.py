# -*- coding: utf-8 -*-
"""
Windows 桌面控件拾取 / 录制器（悬浮工具条 + 鼠标点选 UIA 控件）。

拾取模式：点「拾取控件」后在目标程序上点击，将定位写入步骤表单。
录制模式：无需预先选择窗口；启动后显示左上角悬浮条与屏幕红框提示。
录制中按住 Ctrl 并点击目标控件即可捕获（自动识别单击/输入等），ESC 结束。
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PICKER_AVAILABLE = sys.platform == "win32"

RECORD_ACTION_CHOICES: List[Tuple[str, str]] = [
    ("auto", "自动识别"),
    ("click", "单击"),
    ("double_click", "双击"),
    ("input", "输入"),
    ("right_click", "右键"),
    ("verify", "验证码"),
    ("wait", "等待"),
    ("hotkey", "快捷键"),
]

VERIFY_TYPE_CHOICES: List[Tuple[str, str]] = [
    ("auto", "自动识别"),
    ("slider", "滑动方块"),
    ("image", "点击图片文字"),
]

_INPUT_CONTROL_HINTS = ("edit", "document", "text", "password", "spinner")
_CLICK_CONTROL_HINTS = (
    "button",
    "hyperlink",
    "menuitem",
    "listitem",
    "treeitem",
    "tabitem",
    "checkbox",
    "radiobutton",
    "splitbutton",
    "thumb",
    "cell",
)
_VERIFY_NAME_HINTS = ("验证码", "captcha", "verify", "滑块", "人机验证")

_PICKER_UI_HWNDS: set = set()
_last_attach_spec_key: Optional[Tuple[Any, ...]] = None

_session_lock = threading.Lock()
_session: Dict[str, Any] = {
    "active": False,
    "record_mode": False,
    "unified_mode": False,
    "recording": False,
    "paused": False,
    "armed": False,
    "desktop_spec": {},
    "last_pick": None,
    "recorded_steps": [],
    "error": "",
    "picker_closed": False,
    "shutdown_requested": False,
    "message": "",
}


def desktop_picker_available() -> bool:
    if not _PICKER_AVAILABLE:
        return False
    try:
        from desktop_locator import desktop_runtime_available

        return desktop_runtime_available()
    except ImportError:
        return False


def _session_snapshot() -> Dict[str, Any]:
    proc_dead = _picker_proc is not None and _picker_proc.poll() is not None
    src = _load_session_from_disk() if (_picker_proc and not proc_dead) else None
    if src is None:
        with _session_lock:
            src = dict(_session)
    if proc_dead and not src.get("picker_closed"):
        src = {
            **src,
            "active": False,
            "picker_closed": True,
            "error": src.get("error") or "桌面捕获窗口已退出，请重新启动捕获",
        }
    return {
        "active": bool(src.get("active")),
        "record_mode": bool(src.get("record_mode")),
        "unified_mode": bool(src.get("unified_mode")),
        "recording": bool(src.get("recording")),
        "paused": bool(src.get("paused")),
        "armed": bool(src.get("armed")),
        "last_pick": src.get("last_pick"),
        "recorded_steps": list(src.get("recorded_steps") or []),
        "_sent_count": int(src.get("_sent_count") or 0),
        "error": src.get("error") or "",
        "picker_closed": bool(src.get("picker_closed")),
        "message": src.get("message") or "",
        "desktop_spec": dict(src.get("desktop_spec") or {}),
        "case_id": int(src.get("case_id") or 0),
    }


def _session_file_path() -> Path:
    raw = (os.environ.get("UAT_DESKTOP_PICKER_SESSION") or "").strip()
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "uat_desktop_picker_session.json"


def _is_picker_child_process() -> bool:
    return os.environ.get("UAT_PICKER_CHILD") == "1"


def _json_safe_session_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """去掉不可 JSON 序列化的字段，避免子进程录制步骤写盘失败（父进程轮询读不到）。"""
    out = dict(payload)
    steps = out.get("recorded_steps")
    if isinstance(steps, list):
        safe_steps: List[Dict[str, Any]] = []
        for st in steps:
            if not isinstance(st, dict):
                continue
            s = dict(st)
            meta = s.get("record_meta")
            if isinstance(meta, dict):
                pick = meta.get("pick")
                if isinstance(pick, dict):
                    s["record_meta"] = {
                        "inferred": bool(meta.get("inferred")),
                        "pick": {
                            k: pick.get(k)
                            for k in (
                                "selector_type",
                                "selector_value",
                                "control_type",
                                "name",
                                "automation_id",
                                "class_name",
                                "rectangle",
                                "uia_path",
                                "label",
                                "value_text",
                                "desktop_spec",
                                "window_title",
                            )
                            if k in pick
                        },
                    }
            safe_steps.append(s)
        out["recorded_steps"] = safe_steps
    try:
        json.dumps(out, ensure_ascii=False, default=str)
        return out
    except Exception:
        out.pop("last_pick", None)
        return out


def _persist_session_to_disk() -> None:
    try:
        with _session_lock:
            payload = _json_safe_session_payload(dict(_session))
        existing = _load_session_from_disk() or {}
        sent_disk = int(existing.get("_sent_count") or 0)
        sent_local = int(payload.get("_sent_count") or 0)
        if sent_disk > sent_local:
            payload["_sent_count"] = sent_disk
        disk_steps = list(existing.get("recorded_steps") or [])
        local_steps = list(payload.get("recorded_steps") or [])
        if len(local_steps) >= len(disk_steps):
            payload["recorded_steps"] = local_steps
        else:
            payload["recorded_steps"] = disk_steps
        path = _session_file_path()
        path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _patch_session_on_disk(**fields: Any) -> None:
    """仅更新 session 文件中的指定字段，避免覆盖子进程刚写入的 recorded_steps。"""
    try:
        data = _load_session_from_disk() or {}
        data.update(fields)
        _session_file_path().write_text(
            json.dumps(_json_safe_session_payload(data), ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _load_session_from_disk() -> Optional[Dict[str, Any]]:
    path = _session_file_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _clear_last_pick_on_disk() -> None:
    data = _load_session_from_disk()
    if not data:
        return
    if not data.get("last_pick"):
        return
    data["last_pick"] = None
    try:
        _session_file_path().write_text(
            json.dumps(data, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _set_session(**kwargs: Any) -> None:
    with _session_lock:
        _session.update(kwargs)
    if _is_picker_child_process():
        _persist_session_to_disk()


def _clear_pick_buffers() -> None:
    _set_session(last_pick=None, error="")


def _reset_attach_tracking() -> None:
    global _last_attach_spec_key
    _last_attach_spec_key = None


_BROWSER_CLASS_HINTS = (
    "Chrome_WidgetWin",
    "MozillaWindowClass",
    "ApplicationFrameWindow",
    "IEFrame",
)


def _is_browser_top_window(hwnd: int) -> bool:
    import ctypes

    buf = ctypes.create_unicode_buffer(256)
    if not ctypes.windll.user32.GetClassNameW(int(hwnd), buf, 256):
        return False
    cls = buf.value or ""
    return any(h in cls for h in _BROWSER_CLASS_HINTS)


_OVERLAY_EXE_BASENAMES = frozenset({
    "nvidia overlay.exe",
    "nvcontainer.exe",
    "textinputhost.exe",
    "gamebar.exe",
    "gamebarftserver.exe",
    "microsoft.notes.exe",
})


def _hwnd_process_basename(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        from desktop_discovery import process_image_path

        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        path = process_image_path(int(pid.value))
        return os.path.basename(path).lower() if path else ""
    except Exception:
        return ""


def _hwnd_extended_style(hwnd: int) -> int:
    try:
        import ctypes

        GWL_EXSTYLE = -20
        return int(ctypes.windll.user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE) or 0)
    except Exception:
        return 0


def _should_skip_hwnd_for_pick(hwnd: int, exclude: set) -> bool:
    """跳过透明 overlay、输入法宿主等不应作为捕获目标的顶层窗口。"""
    if not hwnd or hwnd in exclude:
        return True
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if not user32.IsWindow(int(hwnd)) or not user32.IsWindowVisible(int(hwnd)):
            return True
        root = int(user32.GetAncestor(int(hwnd), 2) or hwnd)
        if root != int(hwnd):
            return True
        cls = _hwnd_class_name(hwnd)
        if cls in ("Progman", "WorkerW"):
            return False
        ex = _hwnd_extended_style(hwnd)
        if ex & 0x20:  # WS_EX_TRANSPARENT
            return True
        exe = _hwnd_process_basename(hwnd)
        if exe in _OVERLAY_EXE_BASENAMES:
            return True
        if "overlay" in exe and exe.endswith(".exe"):
            return True
        from ctypes import wintypes

        rect = wintypes.RECT()
        if user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            w = int(rect.right) - int(rect.left)
            h = int(rect.bottom) - int(rect.top)
            if w > 0 and h > 0 and w * h < 64:
                return True
        if cls.lower() in ("cef-osc-widget",) and exe and "nvidia" in exe:
            return True
    except Exception:
        pass
    return False


def _all_top_level_hwnds_at_point(x: int, y: int, exclude: set) -> List[int]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hits: List[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_cb(hwnd, _lparam):
        if hwnd in exclude or not user32.IsWindowVisible(hwnd):
            return True
        root = int(user32.GetAncestor(hwnd, 2) or hwnd)
        if root != int(hwnd):
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.left <= x < rect.right and rect.top <= y < rect.bottom:
            hits.append(int(hwnd))
        return True

    user32.EnumWindows(_enum_cb, 0)
    return hits


def _top_level_hwnd_at(x: int, y: int, exclude: set) -> Optional[int]:
    import ctypes
    from ctypes import wintypes

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32 = ctypes.windll.user32
    GA_ROOT = 2
    chosen: Optional[int] = None

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_cb(hwnd, _lparam):
        nonlocal chosen
        if _should_skip_hwnd_for_pick(hwnd, exclude):
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.left <= x < rect.right and rect.top <= y < rect.bottom:
            chosen = int(hwnd)
            return False
        return True

    user32.EnumWindows(_enum_cb, 0)
    if chosen:
        return chosen

    h = int(user32.WindowFromPoint(_POINT(x, y)) or 0)
    if not h or h in exclude:
        return None
    root = int(user32.GetAncestor(h, GA_ROOT) or h)
    if root in exclude or not user32.IsWindow(root):
        return None
    if not _should_skip_hwnd_for_pick(root, exclude):
        return root
    # 点击穿透 overlay 时：再枚举一次，取第一个含该点且非 overlay 的窗口
    for hwnd in _all_top_level_hwnds_at_point(x, y, exclude):
        if not _should_skip_hwnd_for_pick(hwnd, exclude):
            return hwnd
    return root


def _desktop_spec_at_point(x: int, y: int, exclude: set) -> Dict[str, Any]:
    hwnd = _top_level_hwnd_at(x, y, exclude)
    if not hwnd:
        raise RuntimeError("未识别到有效窗口，请点击应用或桌面上的控件")
    from desktop_discovery import attachment_spec_for_window

    spec, title = attachment_spec_for_window(hwnd)
    class_name = (spec.get("class_name") or "").strip()
    if class_name in ("Progman", "WorkerW") or (title or "").strip().lower() in (
        "program manager",
        "progman",
    ):
        spec["surface"] = "desktop_shell"
    return spec


def _sync_attach_for_spec(spec: Dict[str, Any]) -> None:
    if not spec:
        return
    _set_session(desktop_spec=dict(spec))
    try:
        from desktop_automation import sync_desktop_attach_from_spec

        sync_desktop_attach_from_spec(spec)
    except Exception:
        pass


def _maybe_append_attach_step(spec: Dict[str, Any]) -> None:
    """不再自动插入 attach_window：每步已带 desktop_spec，执行时会按 spec 附着/切换窗口。"""
    return


def _normalize_desktop_uia_path(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    对齐 RPA 竞品路径：Progman → SHELLDLL_DefView → SysListView32(桌面) → ListItem。
    捕获链若缺少 DefView，在 SysListView32 前自动补一层。
    """
    out = [dict(n) for n in (nodes or [])]
    cls_set = {(n.get("class_name") or "").strip() for n in out}
    if "SHELLDLL_DefView" not in cls_set:
        insert_at = 0
        for i, n in enumerate(out):
            if (n.get("class_name") or "").strip() == "SysListView32":
                insert_at = i
                break
        if insert_at == 0 and out:
            insert_at = max(0, len(out) - 1)
        out.insert(
            insert_at,
            {
                "automation_id": "",
                "name": "",
                "control_type": "Pane",
                "class_name": "SHELLDLL_DefView",
            },
        )
    for n in out:
        ct = (n.get("control_type") or "").strip()
        if ct == "List" and not (n.get("class_name") or "").strip():
            n["class_name"] = "SysListView32"
    return out


def _is_spurious_top_level_pick(wrapper: Any, root_handle: int) -> bool:
    """UIA 误识别为 Overlay 顶层 Pane 时返回 True（勿对正常大窗口一律判假）。"""
    try:
        ei = wrapper.element_info
        ct = str(getattr(ei, "control_type", "") or "").lower()
        if "window" not in ct and ct != "pane":
            return False
        name = (getattr(ei, "name", None) or "").strip().lower()
        if name and "overlay" in name:
            return True
        handle = int(getattr(ei, "handle", 0) or getattr(wrapper, "handle", 0) or 0)
        if root_handle and handle and handle == int(root_handle):
            rect = wrapper.rectangle()
            w = int(rect.right) - int(rect.left)
            h = int(rect.bottom) - int(rect.top)
            if w < 4 or h < 4:
                return True
    except Exception:
        return False
    return False


def _coordinate_pick_at(x: int, y: int, spec: Dict[str, Any]) -> Dict[str, Any]:
    px, py = int(x), int(y)
    merged = dict(spec or {})
    merged["pick_center"] = f"{px},{py}"
    return {
        "selector_type": "coordinate",
        "selector_value": f"{px},{py}",
        "control_type": "Coordinate",
        "name": "",
        "automation_id": "",
        "class_name": "",
        "rectangle": {"left": px, "top": py, "right": px + 2, "bottom": py + 2},
        "uia_path": [],
        "label": f"坐标 ({px},{py})",
        "value_text": "",
        "desktop_spec": merged,
        "window_title": merged.get("window_title") or "",
        "pick_point": {"x": px, "y": py},
    }


def _element_to_locator(
    wrapper: Any,
    root_handle: int,
    *,
    click_x: Optional[int] = None,
    click_y: Optional[int] = None,
) -> Tuple[str, str, List[Dict[str, Any]]]:
    """返回 (selector_type, selector_value, uia_path_nodes)。"""
    ei = wrapper.element_info
    aid = (getattr(ei, "automation_id", None) or "").strip()
    name = (getattr(ei, "name", None) or "").strip()
    ct = str(getattr(ei, "control_type", "") or "")

    path_nodes: List[Dict[str, Any]] = []
    w = wrapper
    for _ in range(32):
        try:
            pei = w.element_info
            path_nodes.insert(
                0,
                {
                    "automation_id": (getattr(pei, "automation_id", None) or "").strip(),
                    "name": (getattr(pei, "name", None) or "").strip(),
                    "control_type": str(getattr(pei, "control_type", "") or ""),
                    "class_name": (getattr(pei, "class_name", None) or "").strip(),
                },
            )
            parent = w.parent()
            if parent is None:
                break
            ph = int(getattr(parent, "handle", 0) or 0)
            if ph and ph == root_handle:
                break
            w = parent
        except Exception:
            break

    trimmed = path_nodes[-8:] if len(path_nodes) > 8 else path_nodes
    last_ct = str((trimmed[-1] or {}).get("control_type") or "").lower()
    is_desktop_icon = "listitem" in last_ct or (
        trimmed
        and str((trimmed[0] or {}).get("name") or "").strip() in ("桌面", "Desktop")
    )

    rel_path = [
        n
        for n in trimmed
        if (
            n.get("automation_id")
            or n.get("name")
            or n.get("control_type")
            or n.get("class_name")
        )
    ]
    if is_desktop_icon and len(rel_path) >= 1:
        rel_path = _normalize_desktop_uia_path(rel_path)
        return "uia_path", json.dumps(rel_path, ensure_ascii=False), rel_path

    if _is_spurious_top_level_pick(wrapper, root_handle):
        if click_x is not None and click_y is not None:
            return "coordinate", f"{int(click_x)},{int(click_y)}", trimmed

    if aid and len(aid) <= 200 and aid.lower() not in ("", "titlebar"):
        return "automation_id", aid, trimmed
    if name and 0 < len(name) <= 120:
        return "name", name, trimmed

    if len(rel_path) >= 1:
        return "uia_path", json.dumps(rel_path, ensure_ascii=False), rel_path

    try:
        rect = wrapper.rectangle()
        cx = int((rect.left + rect.right) / 2)
        cy = int((rect.top + rect.bottom) / 2)
        return "coordinate", f"{cx},{cy}", trimmed
    except Exception:
        return "coordinate", "0,0", path_nodes


def _hwnd_class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(int(hwnd), buf, 256)
        return (buf.value or "").strip()
    except Exception:
        return ""


def _minimal_desktop_spec_at(x: int, y: int, exclude: set) -> Optional[Dict[str, Any]]:
    """轻量判断是否在桌面 Progman/WorkerW 上（悬停路径避免 attachment_spec_for_window）。"""
    hwnd = _top_level_hwnd_at(x, y, exclude)
    if not hwnd:
        return None
    try:
        from desktop_shell_win32 import is_desktop_root_hwnd

        if not is_desktop_root_hwnd(hwnd):
            return None
    except Exception:
        if _hwnd_class_name(hwnd) != "Progman":
            return None
    return {
        "hwnd": int(hwnd),
        "surface": "desktop_shell",
        "window_title": "Program Manager",
        "process": "explorer.exe",
    }


def _desktop_shell_wrapper_at(x: int, y: int, exclude: set, *, force_icon_cache: bool = False) -> Optional[Any]:
    """桌面图标层：点击时解析 ListItem（使用矩形缓存 + 按名解析，避免每次全树扫描）。"""
    spec = _minimal_desktop_spec_at(x, y, exclude)
    if not spec:
        return None
    from desktop_locator import attach_desktop_shell, desktop_listitem_at_screen_point

    try:
        app, win = attach_desktop_shell(spec)
        return desktop_listitem_at_screen_point(
            x, y, win, spec, app, force_cache=force_icon_cache
        )
    except Exception:
        return None


def _prefer_coordinate_picking() -> bool:
    """优先使用坐标拾取模式（更可靠、无超时）。通过环境变量 DESKTOP_PICKER_COORDINATE_FIRST=1 控制，默认开启。"""
    raw = (os.environ.get("DESKTOP_PICKER_COORDINATE_FIRST") or "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _pure_coordinate_picking() -> bool:
    """纯坐标模式：拾取时完全不调用 UIA，只使用 Win32 API。通过 DESKTOP_PICKER_PURE_COORDINATE=1 控制，默认开启。"""
    raw = (os.environ.get("DESKTOP_PICKER_PURE_COORDINATE") or "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _pick_control_at(x: int, y: int, toolbar_hwnds: set) -> Optional[Dict[str, Any]]:
    """
    优化后的拾取逻辑：把桌面当作"画布"，优先使用坐标定位。

    核心思路（按用户建议）：
    1. 不深入解析 UIA 控件树（避免超时）
    2. 只关心"在哪个应用的什么位置"
    3. 记录坐标 + 窗口上下文，执行时直接在该位置执行动作
    """
    exclude = set(toolbar_hwnds) | set(_PICKER_UI_HWNDS)
    pt_hwnd = _top_level_hwnd_at(x, y, exclude)
    if not pt_hwnd:
        raise RuntimeError(
            "未识别到有效窗口（可能点在捕获工具条上），请对准目标应用内控件"
        )

    # 获取窗口规格（应用上下文）- 这是"在哪个应用"
    spec = _desktop_spec_at_point(x, y, exclude)
    if _is_picker_child_process():
        _set_session(desktop_spec=dict(spec))
    else:
        # 纯坐标模式下跳过可能引入 UIA 的 attach 操作
        if not _pure_coordinate_picking():
            _sync_attach_for_spec(spec)

    root_handle = int(spec.get("hwnd") or 0)

    # ===== 快速路径 1：桌面图标层（特殊处理，有缓存优化）=====
    # 纯坐标模式下：即使是桌面也直接用坐标，完全不调用 UIA
    if not _pure_coordinate_picking():
        shell_spec = _minimal_desktop_spec_at(x, y, exclude)
        if shell_spec:
            wrapper = _desktop_shell_wrapper_at(x, y, exclude, force_icon_cache=True)
            if wrapper is not None:
                # 桌面图标成功解析，走 UIA 路径（有优化缓存，不会慢）
                st, sv, path_nodes = _element_to_locator(wrapper, root_handle, click_x=int(x), click_y=int(y))
                ei = wrapper.element_info
                pick = _build_pick_result(wrapper, ei, st, sv, path_nodes, spec, x, y)
                pick["desktop_spec"]["surface"] = "desktop_shell"
                return pick
            # 桌面层但图标解析失败，直接返回坐标（桌面空白处点击）
            return _coordinate_pick_at(x, y, shell_spec)

    # ===== 快速路径 2：优先坐标模式（用户建议的"网页式"思维）=====
    if _prefer_coordinate_picking():
        # 纯坐标模式：完全不调用 UIA（避免任何超时），只用 Win32 API 获取窗口信息
        if _pure_coordinate_picking():
            # 尝试用 Win32 获取控件文本（纯 Win32，不调用 UIA）
            win32_info = _try_win32_control_info(x, y, pt_hwnd)
            pick = _coordinate_pick_at(x, y, spec)
            if win32_info:
                pick["control_type"] = win32_info.get("class_name", "")
                pick["name"] = win32_info.get("text", "")
                pick["label"] = win32_info.get("label", f"坐标 ({x},{y})")
            else:
                pick["label"] = f"坐标 ({x},{y})"
            return pick

        # 尝试轻量 UIA 获取控件信息（带超时保护，不影响功能）
        light_info = _try_lightweight_uia_info(x, y, root_handle)
        if light_info:
            # 有 UIA 信息，但用坐标作为定位器（最可靠）
            pick = _coordinate_pick_at(x, y, spec)
            pick["control_type"] = light_info.get("control_type", "")
            pick["name"] = light_info.get("name", "")
            pick["label"] = light_info.get("label", f"坐标 ({x},{y})")
            pick["_uia_hint"] = light_info  # 保留 UIA 信息作为参考
            return pick
        # 无 UIA 信息，纯坐标模式
        return _coordinate_pick_at(x, y, spec)

    # ===== 回退路径：完整 UIA 解析（传统模式，可能超时）=====
    from pywinauto import Desktop
    from pywinauto.controls.uiawrapper import UIAWrapper

    try:
        raw = Desktop(backend="uia").from_point(x, y)
        wrapper = raw if hasattr(raw, "element_info") else UIAWrapper(raw)
    except Exception:
        # UIA 失败时返回坐标拾取，避免超时
        return _coordinate_pick_at(x, y, spec)

    if _is_spurious_top_level_pick(wrapper, root_handle):
        return _coordinate_pick_at(x, y, spec)

    # 桌面层若最终仍是根窗格（大面积 Pane/Window），直接走坐标
    try:
        ct = str(getattr(wrapper.element_info, "control_type", "") or "").lower()
        if ct in ("pane", "window"):
            rect = wrapper.rectangle()
            w = int(rect.right) - int(rect.left)
            h = int(rect.bottom) - int(rect.top)
            if w > 400 or h > 300:
                return _coordinate_pick_at(x, y, spec)
    except Exception:
        pass

    st, sv, path_nodes = _element_to_locator(wrapper, root_handle, click_x=int(x), click_y=int(y))
    return _build_pick_result(wrapper, wrapper.element_info, st, sv, path_nodes, spec, x, y)


def _try_win32_control_info(x: int, y: int, hwnd: int) -> Optional[Dict[str, Any]]:
    """
    纯 Win32 API 获取控件信息（不调用 UIA/pywinauto，避免超时）。
    使用 WindowFromPoint 和 GetWindowText 获取基本信息。
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        # 获取指定点的窗口句柄
        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _POINT(x, y)
        h_wnd = user32.WindowFromPoint(pt)
        if not h_wnd:
            return None

        # 获取类名
        buf_cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h_wnd, buf_cls, 256)
        class_name = buf_cls.value or ""

        # 获取窗口文本
        buf_text = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h_wnd, buf_text, 512)
        text = buf_text.value or ""

        # 如果是子控件，尝试获取其父窗口文本作为上下文
        parent_text = ""
        parent = user32.GetParent(h_wnd)
        if parent:
            buf_parent = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(parent, buf_parent, 512)
            parent_text = buf_parent.value or ""

        # 生成标签
        if text and len(text) < 80:
            label = text
        elif parent_text and len(parent_text) < 80:
            label = f"{parent_text} - 坐标 ({x},{y})"
        else:
            label = f"坐标 ({x},{y})"

        return {
            "hwnd": int(h_wnd),
            "class_name": class_name,
            "text": text,
            "parent_text": parent_text,
            "label": label[:80],
        }
    except Exception:
        return None


def _try_lightweight_uia_info(x: int, y: int, root_handle: int) -> Optional[Dict[str, Any]]:
    """
    轻量级 UIA 信息获取：只取当前点的元素信息，不遍历控件树。
    【重要】带超时保护，避免 UIA 初始化卡住。
    """
    import threading
    import time

    result: list = [None]
    start_time = time.time()

    def _uia_fetch():
        try:
            from pywinauto import Desktop
            from pywinauto.controls.uiawrapper import UIAWrapper

            raw = Desktop(backend="uia").from_point(x, y)
            wrapper = raw if hasattr(raw, "element_info") else UIAWrapper(raw)
            ei = wrapper.element_info

            # 检查是否是虚假顶层
            if _is_spurious_top_level_pick(wrapper, root_handle):
                result[0] = None
                return

            name = (getattr(ei, "name", None) or "").strip()
            control_type = str(getattr(ei, "control_type", "") or "")
            automation_id = (getattr(ei, "automation_id", None) or "").strip()

            # 生成标签
            label = name or automation_id or control_type or f"坐标 ({x},{y})"

            result[0] = {
                "name": name,
                "control_type": control_type,
                "automation_id": automation_id,
                "label": label[:80],
            }
        except Exception:
            result[0] = None

    # 启动带超时的线程
    t = threading.Thread(target=_uia_fetch, name="uia-light-fetch")
    t.daemon = True
    t.start()
    t.join(timeout=2.0)  # 最多等2秒

    if t.is_alive():
        # 超时了，返回 None（将使用纯坐标模式）
        return None

    return result[0]


def _build_pick_result(wrapper, ei, st: str, sv: str, path_nodes: list, spec: Dict[str, Any], x: int, y: int) -> Dict[str, Any]:
    """构建拾取结果字典。"""
    label = (getattr(ei, "name", None) or getattr(ei, "automation_id", None) or st or "控件")
    pick = {
        "selector_type": st,
        "selector_value": sv,
        "control_type": str(getattr(ei, "control_type", "") or ""),
        "name": (getattr(ei, "name", None) or "").strip(),
        "automation_id": (getattr(ei, "automation_id", None) or "").strip(),
        "class_name": (getattr(ei, "class_name", None) or "").strip(),
        "rectangle": {
            "left": int(wrapper.rectangle().left),
            "top": int(wrapper.rectangle().top),
            "right": int(wrapper.rectangle().right),
            "bottom": int(wrapper.rectangle().bottom),
        },
        "uia_path": path_nodes,
        "label": str(label)[:80],
        "value_text": "",
    }
    try:
        pick["value_text"] = (wrapper.window_text() or "").strip()[:200]
    except Exception:
        pass
    pick["desktop_spec"] = dict(spec)
    pick["window_title"] = spec.get("window_title") or ""
    pick["pick_point"] = {"x": int(x), "y": int(y)}
    pick["desktop_spec"]["pick_center"] = f"{int(x)},{int(y)}"
    return pick


def _prewarm_desktop_icon_cache(_spec: Optional[Dict[str, Any]] = None) -> None:
    """后台用 Win32 ListView 预热桌面图标矩形（无 UIA）。默认关闭，避免启动时 EnumWindows 导致任务栏闪动。"""
    raw = (os.environ.get("DESKTOP_PICKER_PREWARM") or "0").strip().lower()
    if raw not in ("1", "true", "yes", "on"):
        return
    try:
        from desktop_shell_win32 import schedule_win32_desktop_icon_cache_refresh

        schedule_win32_desktop_icon_cache_refresh()
    except Exception:
        pass


def _picker_topmost_enabled() -> bool:
    """默认不用 Tk topmost（易触发 DWM 重绘、任务栏闪一下）；需始终置顶时设 DESKTOP_PICKER_TOPMOST=1。"""
    raw = (os.environ.get("DESKTOP_PICKER_TOPMOST") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _apply_picker_topmost(root: Any, enabled: bool = True) -> None:
    if not root:
        return
    try:
        root.attributes("-topmost", bool(enabled) and _picker_topmost_enabled())
    except Exception:
        pass


def _picker_pick_timeout_sec() -> float:
    try:
        return max(5.0, float(os.environ.get("DESKTOP_PICKER_PICK_TIMEOUT", "8") or "8"))
    except (TypeError, ValueError):
        return 8.0


def _hover_highlight_enabled() -> bool:
    raw = (os.environ.get("DESKTOP_PICKER_HOVER") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _lbutton_pressed() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)


def _lbutton_async_state() -> Tuple[bool, bool]:
    """返回 (当前是否按下, 自上次查询以来是否发生过按下)。用于捕获轮询间隔内的快点击。"""
    import ctypes

    state = int(ctypes.windll.user32.GetAsyncKeyState(0x01))
    return bool(state & 0x8000), bool(state & 0x0001)


def _control_rect_at(x: int, y: int, exclude: set) -> Optional[Tuple[int, int, int, int]]:
    """
    悬停高亮专用：桌面(Progman/WorkerW)仅图标小矩形；禁止对桌面/大窗口用整窗外框（会黑屏）。
    """
    try:
        from desktop_shell_win32 import control_rect_at_screen_point

        return control_rect_at_screen_point(x, y, exclude)
    except Exception:
        return None


def _action_label(action: str) -> str:
    for k, label in RECORD_ACTION_CHOICES:
        if k == action:
            return label
    return action or "操作"


def _control_type_lower(pick: Dict[str, Any]) -> str:
    return str(pick.get("control_type") or "").strip().lower()


def _pick_text_blob(pick: Dict[str, Any]) -> str:
    parts = [
        pick.get("name") or "",
        pick.get("automation_id") or "",
        pick.get("label") or "",
        pick.get("class_name") or "",
    ]
    return " ".join(parts).lower()


def _is_input_like_control(pick: Dict[str, Any]) -> bool:
    ct = _control_type_lower(pick)
    if any(h in ct for h in _INPUT_CONTROL_HINTS):
        return True
    blob = _pick_text_blob(pick)
    return any(k in blob for k in ("输入", "input", "password", "search", "搜索", "query"))


def _is_verify_like_control(pick: Dict[str, Any]) -> bool:
    blob = _pick_text_blob(pick)
    return any(h in blob for h in _VERIFY_NAME_HINTS)


def _infer_record_action(pick: Dict[str, Any]) -> str:
    if _is_verify_like_control(pick):
        return "verify"
    if _is_input_like_control(pick):
        return "input"
    ct = _control_type_lower(pick)
    try:
        from desktop_locator import is_desktop_shell_spec

        spec = pick.get("desktop_spec") or {}
        if "listitem" in ct and is_desktop_shell_spec(spec):
            return "double_click"
    except ImportError:
        pass
    if "combobox" in ct:
        return "click"
    if any(h in ct for h in _CLICK_CONTROL_HINTS):
        return "click"
    if pick.get("value_text"):
        return "input"
    return "click"


def _build_desktop_locator_candidates(
    pick: Optional[Dict[str, Any]], spec: Dict[str, Any]
) -> str:
    if not pick:
        return ""
    cands: List[Dict[str, Any]] = []

    def _add(st: str, sv: str, score: int) -> None:
        if sv and not any(c.get("selector_value") == sv and c.get("selector_type") == st for c in cands):
            cands.append({"selector_type": st, "selector_value": sv, "score": score})

    uia = pick.get("uia_path")
    if isinstance(uia, list) and uia:
        _add("uia_path", json.dumps(uia, ensure_ascii=False), 92)
    center = (spec or {}).get("pick_center") or ""
    if center:
        _add("coordinate", center, 88)
    aid = (pick.get("automation_id") or "").strip()
    if aid:
        _add("automation_id", aid, 82)
    name = (pick.get("name") or "").strip()
    if name and "overlay" not in name.lower():
        _add("name", name, 72)
    st = (pick.get("selector_type") or "").strip().lower()
    sv = (pick.get("selector_value") or "").strip()
    if st and sv and st not in ("name", "automation_id", "uia_path", "coordinate"):
        _add(st, sv, 75)
    return json.dumps(cands, ensure_ascii=False) if cands else ""


def _normalize_recorded_selector(
    pick: Optional[Dict[str, Any]], spec: Dict[str, Any]
) -> Tuple[str, str]:
    if not pick:
        return "automation_id", ""
    st = (pick.get("selector_type") or "automation_id").strip().lower()
    sv = (pick.get("selector_value") or "").strip()
    center = (spec or {}).get("pick_center") or ""
    name = (pick.get("name") or "").strip()
    ct = _control_type_lower(pick)
    try:
        from desktop_locator import is_desktop_shell_spec

        if is_desktop_shell_spec(spec) and pick.get("uia_path"):
            return "uia_path", json.dumps(pick["uia_path"], ensure_ascii=False)
    except ImportError:
        pass
    unreliable = (
        _is_spurious_top_level_pick_from_pick(pick)
        or "overlay" in name.lower()
        or (st == "name" and name and name == (spec.get("window_title") or "").strip())
    )
    if unreliable and center:
        return "coordinate", center
    if unreliable and pick.get("uia_path"):
        return "uia_path", json.dumps(pick["uia_path"], ensure_ascii=False)
    return st or "automation_id", sv


def _is_spurious_top_level_pick_from_pick(pick: Dict[str, Any]) -> bool:
    ct = _control_type_lower(pick)
    if "window" not in ct and "pane" not in ct:
        return False
    name = (pick.get("name") or "").strip().lower()
    if "overlay" in name:
        return True
    try:
        r = pick.get("rectangle") or {}
        w = int(r.get("right", 0)) - int(r.get("left", 0))
        h = int(r.get("bottom", 0)) - int(r.get("top", 0))
        if w < 4 or h < 4:
            return True
    except Exception:
        return True
    return False


def _append_recorded_step(
    pick: Optional[Dict[str, Any]],
    *,
    action: str = "click",
    input_value: str = "",
    compare_type: str = "",
    inferred: bool = False,
    desktop_spec_override: Optional[Dict[str, Any]] = None,
    description: str = "",
) -> Dict[str, Any]:
    spec = dict(
        desktop_spec_override
        or (pick or {}).get("desktop_spec")
        or _session_snapshot().get("desktop_spec")
        or {}
    )
    if pick and pick.get("pick_point"):
        try:
            pt = pick["pick_point"]
            spec["pick_center"] = f"{int(pt['x'])},{int(pt['y'])}"
        except Exception:
            pass
    if pick and pick.get("rectangle") and not spec.get("pick_center"):
        try:
            r = pick["rectangle"]
            spec["pick_center"] = (
                f"{int((int(r['left']) + int(r['right'])) / 2)},"
                f"{int((int(r['top']) + int(r['bottom'])) / 2)}"
            )
        except Exception:
            pass
    if pick and pick.get("uia_path"):
        spec["uia_path"] = pick["uia_path"]
    label = (pick or {}).get("label") or (pick or {}).get("name") or ""
    if label:
        spec["target_name"] = str(label).strip()[:120]
    act = (action or "click").strip().lower()
    iv = (input_value or "").strip()
    ct = (compare_type or "").strip()
    label = (pick or {}).get("label") or "控件"

    if act == "verify" and not iv:
        iv = ct or "auto"
        ct = iv
    if act == "wait" and not iv:
        iv = "1"
    if act == "hotkey" and not iv:
        iv = "^v"

    desc = (description or "").strip() or f"录制：{_action_label(act)}"
    if not description:
        if inferred and act != "auto":
            desc += "（自动）"
        if pick and label:
            desc += f"「{label}」"
        if spec.get("pick_center"):
            desc += f" @ {spec['pick_center']}"
    if act == "input" and iv:
        desc += f" → {iv[:40]}"
    elif act == "verify":
        desc += f"（{iv or 'auto'}）"
    elif act == "wait":
        desc += f" {iv} 秒"
    elif act == "hotkey" and iv:
        desc += f" {iv}"

    sel_type, sel_val = _normalize_recorded_selector(pick, spec)
    step = {
        "action": act,
        "automation_layer": "desktop",
        "selector_type": sel_type,
        "selector_value": sel_val,
        "input_value": iv,
        "compare_type": ct,
        "description": desc,
        "desktop_spec": spec,
        "locator_candidates": _build_desktop_locator_candidates(pick, spec),
        "record_meta": {"pick": pick, "inferred": inferred},
    }
    with _session_lock:
        recorded = list(_session.get("recorded_steps") or [])
        recorded.append(step)
        _session["recorded_steps"] = recorded
        if pick:
            _session["last_pick"] = {
                **pick,
                "record_action": act,
                "input_value": iv,
                "compare_type": ct,
            }
        _session["message"] = f"已录制第 {len(recorded)} 步（{_action_label(act)}）"
    if _is_picker_child_process():
        _persist_session_to_disk()
    return step


def _record_auto_pick_to_session(pick: Dict[str, Any]) -> None:
    """录制模式：无 UI 时将拾取结果写入 session（供 status 轮询落库）。"""
    spec = dict(pick.get("desktop_spec") or {})
    try:
        _maybe_append_attach_step(spec)
    except Exception:
        pass
    act = _infer_record_action(pick)
    iv = ""
    ct = ""
    if act == "input":
        iv = (pick.get("value_text") or "").strip()
        if not iv:
            act = "click"
    elif act == "verify":
        iv = "auto"
        ct = "auto"
    _append_recorded_step(
        pick,
        action=act,
        input_value=iv,
        compare_type=ct,
        inferred=True,
        desktop_spec_override=spec,
    )
    _set_session(
        armed=True,
        recording=True,
        paused=False,
        error="",
        message=_session_snapshot().get("message") or "已录制",
    )


def _hwnd_for_tk(widget: Any) -> int:
    import ctypes

    h = int(widget.winfo_id())
    root = int(ctypes.windll.user32.GetAncestor(h, 2) or 0)
    return root or h


def _picker_border_enabled() -> bool:
    """全屏红框为多个 topmost 窗口，默认关闭以免 DWM 黑屏（DESKTOP_PICKER_BORDER=1 开启）。"""
    raw = (os.environ.get("DESKTOP_PICKER_BORDER") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _cursor_pos() -> Tuple[int, int]:
    import ctypes

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def _ctrl_pressed() -> bool:
    import ctypes

    u = ctypes.windll.user32
    return bool(
        (u.GetAsyncKeyState(0x11) & 0x8000)
        or (u.GetAsyncKeyState(0xA2) & 0x8000)
        or (u.GetAsyncKeyState(0xA3) & 0x8000)
    )


def _shift_pressed() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)


def _escape_pressed() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)


class _ScreenBorderOverlay:
    """可选全屏红色捕获边框（默认关闭，避免多 topmost 窗口触发黑屏）。"""

    BORDER = 3
    COLOR = "#e53935"

    def __init__(self, master: Any) -> None:
        self._master = master
        self._tops: List[Any] = []

    def show(self) -> None:
        if not _picker_border_enabled():
            return
        self.hide()
        sw = max(1, int(self._master.winfo_screenwidth() or 1))
        sh = max(1, int(self._master.winfo_screenheight() or 1))
        b = self.BORDER
        import tkinter as tk

        for x, y, w, h in (
            (0, 0, sw, b),
            (0, max(0, sh - b), sw, b),
            (0, 0, b, sh),
            (max(0, sw - b), 0, b, sh),
        ):
            t = tk.Toplevel(self._master)
            t.overrideredirect(True)
            try:
                t.attributes("-topmost", False)
            except Exception:
                pass
            t.configure(bg=self.COLOR)
            t.geometry(f"{max(1, w)}x{max(1, h)}+{x}+{y}")
            try:
                t.lift()
            except Exception:
                pass
            self._tops.append(t)
            _PICKER_UI_HWNDS.add(_hwnd_for_tk(t))

    def hide(self) -> None:
        for t in self._tops:
            try:
                t.destroy()
            except Exception:
                pass
        self._tops = []


class _ElementHighlightOverlay:
    """悬停控件高亮边框（按住 Ctrl 移动鼠标时显示）。"""

    BORDER = 2
    COLOR = "#2f80ff"

    def __init__(self, master: Any) -> None:
        self._master = master
        self._tops: List[Any] = []
        self._hwnds: set = set()

    @property
    def hwnds(self) -> set:
        return set(self._hwnds)

    def show_rect(self, left: int, top: int, right: int, bottom: int) -> None:
        if right <= left or bottom <= top:
            self.hide()
            return
        b = self.BORDER
        segments = (
            (left, top, right - left, b),
            (left, bottom - b, right - left, b),
            (left, top, b, bottom - top),
            (right - b, top, b, bottom - top),
        )
        if len(self._tops) == 4:
            ok = True
            for t, (x, y, w, h) in zip(self._tops, segments):
                if w <= 0 or h <= 0:
                    ok = False
                    break
                try:
                    t.geometry(f"{w}x{h}+{x}+{y}")
                except Exception:
                    ok = False
                    break
            if ok:
                return
        self.hide()
        import tkinter as tk

        for x, y, w, h in segments:
            if w <= 0 or h <= 0:
                continue
            t = tk.Toplevel(self._master)
            t.overrideredirect(True)
            try:
                t.attributes("-topmost", False)
            except Exception:
                pass
            t.configure(bg=self.COLOR)
            t.geometry(f"{max(1, w)}x{max(1, h)}+{x}+{y}")
            try:
                t.lift()
            except Exception:
                pass
            self._tops.append(t)
            try:
                hwd = _hwnd_for_tk(t)
                self._hwnds.add(hwd)
                _PICKER_UI_HWNDS.add(hwd)
            except Exception:
                pass

    def hide(self) -> None:
        for t in self._tops:
            try:
                t.destroy()
            except Exception:
                pass
        self._tops = []
        self._hwnds = set()


class _DesktopPickerUI:
    """悬浮工具条（tkinter + 低级鼠标钩子，须在独立进程主线程运行）。"""

    def __init__(
        self,
        desktop_spec: Dict[str, Any],
        record_mode: bool,
        *,
        unified_mode: bool = False,
        prefer_web_clicks: bool = False,
    ):
        self._desktop_spec = dict(desktop_spec or {})
        self._record_mode = bool(record_mode)
        self._unified_mode = bool(unified_mode)
        self._prefer_web_clicks = bool(prefer_web_clicks)
        self._recording = False
        self._paused = False
        self._armed = False
        self._toolbar_hwnds: set = set()
        self._root = None
        self._stop_flag = False
        self._btn_start = None
        self._btn_pause = None
        self._btn_end = None
        self._btn_arm = None
        self._action_var = None
        self._param_var = None
        self._verify_var = None
        self._param_row = None
        self._param_label = None
        self._verify_row = None
        self._action_row = None
        self._border = None
        self._highlight: Optional[_ElementHighlightOverlay] = None
        self._pick_queue: queue.Queue = queue.Queue()
        self._input_poll_after_id: Optional[str] = None
        self._last_hover_rect: Optional[Tuple[int, int, int, int]] = None
        self._prev_lbutton_down = False
        self._prev_escape_down = False
        self._last_hover_ts = 0.0
        self._last_pick_ts = 0.0
        self._last_pick_sig: Optional[Tuple[Any, ...]] = None
        self._pick_gesture_active = False
        self._pick_gesture_xy: Tuple[int, int] = (0, 0)
        self._pick_gesture_armed = False
        self._input_poll_warmup = 0
        self._pick_inflight = False
        self._pick_timeout_after_id: Optional[str] = None
        self._pick_seq = 0

    def _exclude_hwnds(self) -> set:
        ex = set(self._toolbar_hwnds) | set(_PICKER_UI_HWNDS)
        if self._highlight:
            ex |= self._highlight.hwnds
        return ex

    def _capture_active(self) -> bool:
        """录制/统一捕获：recording 即可拾取；纯拾取模式仍依赖 armed。"""
        if self._paused:
            return False
        if self._record_mode or self._unified_mode:
            return bool(self._recording)
        return bool(self._armed)

    def _clear_hover_preview(self) -> None:
        self._last_hover_rect = None
        if self._highlight:
            self._highlight.hide()

    def _cancel_input_poll(self) -> None:
        if self._root and self._input_poll_after_id:
            try:
                self._root.after_cancel(self._input_poll_after_id)
            except Exception:
                pass
        self._input_poll_after_id = None

    def _enqueue_pick_at(self, x: int, y: int) -> None:
        self._pick_queue.put((int(x), int(y)))

    def _cancel_pick_timeout_watch(self) -> None:
        if self._root and self._pick_timeout_after_id:
            try:
                self._root.after_cancel(self._pick_timeout_after_id)
            except Exception:
                pass
        self._pick_timeout_after_id = None

    def _on_pick_worker_done(
        self,
        pick: Optional[Dict[str, Any]],
        err: Optional[BaseException],
    ) -> None:
        self._cancel_pick_timeout_watch()
        self._pick_inflight = False
        try:
            if err is not None:
                _set_session(error=str(err))
                if self._root and self._root.winfo_exists():
                    self._sync_buttons()
                return
            if not pick:
                _set_session(
                    error="未识别到控件，请对准目标应用内元素（勿点在捕获工具条上）"
                )
                if self._root and self._root.winfo_exists():
                    self._sync_buttons()
                return
            sig = (
                pick.get("selector_type"),
                pick.get("selector_value"),
                int((pick.get("rectangle") or {}).get("left") or 0),
                int((pick.get("rectangle") or {}).get("top") or 0),
            )
            now = time.time()
            if sig == self._last_pick_sig and now - self._last_pick_ts < 0.55:
                return
            self._last_pick_sig = sig
            self._last_pick_ts = now
            _set_session(message="正在写入步骤…", error="")
            if (self._record_mode or self._unified_mode) and not self._paused:
                if self._root and self._root.winfo_exists() and not self._stop_flag:
                    self._finish_pick(pick)
                else:
                    _record_auto_pick_to_session(pick)
            elif self._root and self._root.winfo_exists() and not self._stop_flag:
                self._finish_pick(pick)
            else:
                _set_session(
                    last_pick={**pick, "record_action": "click"},
                    message="已拾取控件",
                )
        except Exception as exc:
            _set_session(error=str(exc))
            if self._root and self._root.winfo_exists():
                self._sync_buttons()

    def _schedule_pick_at(self, x: int, y: int) -> None:
        """在后台线程执行 UIA 拾取，避免阻塞 Tk 主循环导致捕获窗卡死。"""
        if not self._root:
            return
        if self._pick_inflight:
            _set_session(message="上一次拾取尚未完成，请稍候…", error="")
            return
        if self._prefer_web_clicks:
            hwnd = _top_level_hwnd_at(x, y, self._toolbar_hwnds)
            if hwnd and _is_browser_top_window(hwnd):
                return

        self._pick_inflight = True
        self._pick_seq += 1
        pick_seq = self._pick_seq
        px, py = int(x), int(y)
        exclude = self._exclude_hwnds()
        _set_session(message="正在识别控件…", error="")

        def _worker() -> None:
            pick: Optional[Dict[str, Any]] = None
            err: Optional[BaseException] = None
            try:
                # 纯坐标模式下不需要 COM/UIA，直接拾取（避免 CoInitialize 卡住）
                if _pure_coordinate_picking():
                    pick = _pick_control_at(px, py, exclude)
                else:
                    # 传统模式需要 COM 初始化
                    import pythoncom
                    pythoncom.CoInitialize()
                    try:
                        pick = _pick_control_at(px, py, exclude)
                    finally:
                        pythoncom.CoUninitialize()
            except BaseException as exc:
                err = exc

            if self._root and not self._stop_flag:

                def _deliver(seq=pick_seq) -> None:
                    if seq != self._pick_seq:
                        return
                    self._on_pick_worker_done(pick, err)

                try:
                    self._root.after(0, _deliver)
                except Exception:
                    self._on_pick_worker_done(pick, err)
            else:
                self._on_pick_worker_done(pick, err)

        timeout_ms = int(_picker_pick_timeout_sec() * 1000)

        def _on_pick_timeout(seq=pick_seq) -> None:
            self._pick_timeout_after_id = None
            if seq != self._pick_seq or not self._pick_inflight:
                return
            self._pick_inflight = False
            self._pick_seq += 1
            # 纯坐标模式应该永远不会超时（只用 Win32 API）
            # 如果还超时，可能是 COM/UIA 初始化问题，建议关闭 UIA 完全使用纯 Win32
            _set_session(
                error="拾取超时：建议设置 DESKTOP_PICKER_PURE_COORDINATE=1 启用纯坐标模式（完全跳过 UIA），或检查 pywinauto 安装"
            )
            self._sync_buttons()

        self._cancel_pick_timeout_watch()
        self._pick_timeout_after_id = self._root.after(timeout_ms, _on_pick_timeout)
        threading.Thread(
            target=_worker, daemon=True, name="uat-desktop-uia-pick"
        ).start()

    def _run_pick_job(self, x: int, y: int) -> None:
        self._schedule_pick_at(x, y)

    def _schedule_input_poll(self) -> None:
        """
        定时轮询 Ctrl+左键、Esc 结束与悬停高亮。
        不使用 WH_MOUSE_LL / WH_KEYBOARD_LL（易导致系统输入卡顿/黑屏）。
        """
        if not self._root:
            return
        self._cancel_input_poll()

        def _tick() -> None:
            if self._stop_flag:
                return
            try:
                esc = _escape_pressed()
                if (
                    esc
                    and not self._prev_escape_down
                    and (self._record_mode or self._unified_mode)
                ):
                    self._end_recording()
                    return
                self._prev_escape_down = esc

                active = self._capture_active()
                ctrl = _ctrl_pressed()
                lmb_down = _lbutton_pressed()
                cx, cy = _cursor_pos()

                if active and ctrl and lmb_down and not self._prev_lbutton_down:
                    # 左键按下瞬间（上升沿）
                    self._pick_gesture_xy = (cx, cy)
                elif active and ctrl and not lmb_down and self._prev_lbutton_down:
                    # 左键松开瞬间（下降沿）
                    gx, gy = self._pick_gesture_xy if self._pick_gesture_xy != (0, 0) else (cx, cy)
                    self._trigger_pick_gesture(gx, gy)
                    self._pick_gesture_xy = (0, 0)

                self._prev_lbutton_down = lmb_down

                self._drain_pick_queue()

                if (
                    _hover_highlight_enabled()
                    and active
                    and ctrl
                    and not self._paused
                ):
                    self._update_hover_highlight(cx, cy)
                else:
                    self._clear_hover_preview()
            except Exception as poll_exc:
                _set_session(error=f"捕获轮询异常: {poll_exc}")
            if self._root and not self._stop_flag:
                delay = 10 if self._capture_active() else 250
                self._input_poll_after_id = self._root.after(delay, _tick)

        self._input_poll_after_id = self._root.after(50, _tick)

    def _trigger_pick_gesture(self, x: int, y: int) -> None:
        if self._prefer_web_clicks:
            hwnd = _top_level_hwnd_at(x, y, self._toolbar_hwnds)
            if hwnd and _is_browser_top_window(hwnd):
                return
        _set_session(message="已检测到 Ctrl+点击，正在识别控件…", error="")
        self._schedule_pick_at(x, y)

    def _ensure_capture_window_visible(self) -> None:
        if not self._root:
            return
        try:
            self._root.deiconify()
            self._root.lift()
            _apply_picker_topmost(self._root, True)
        except Exception:
            pass

    def _update_hover_highlight(self, x: int, y: int) -> None:
        if not self._highlight or self._paused or not _ctrl_pressed():
            self._clear_hover_preview()
            return
        now = time.time()
        if now - self._last_hover_ts < 0.12:
            return
        rect = _control_rect_at(x, y, self._exclude_hwnds())
        if not rect:
            self._last_hover_rect = None
            self._highlight.hide()
            return
        if self._last_hover_rect == rect:
            return
        self._last_hover_ts = now
        self._last_hover_rect = rect
        self._highlight.show_rect(*rect)

    def _drain_pick_queue(self) -> None:
        """处理由其它模块入队的 dict 拾取结果（鼠标捕获已改为轮询+后台线程）。"""
        while True:
            try:
                item = self._pick_queue.get_nowait()
            except queue.Empty:
                break
            if not isinstance(item, dict):
                continue
            err_msg = (item.get("__pick_error__") or "").strip()
            if err_msg:
                _set_session(error=err_msg)
                self._sync_buttons()
                continue
            try:
                pick = item
                sig = (
                    pick.get("selector_type"),
                    pick.get("selector_value"),
                    int((pick.get("rectangle") or {}).get("left") or 0),
                    int((pick.get("rectangle") or {}).get("top") or 0),
                )
                now = time.time()
                if (
                    sig == self._last_pick_sig
                    and now - self._last_pick_ts < 0.55
                ):
                    continue
                self._last_pick_sig = sig
                self._last_pick_ts = now
                self._finish_pick(pick)
            except Exception as exc:
                _set_session(error=str(exc))
                if not (self._recording or self._unified_mode):
                    self._armed = False
                    _set_session(armed=False)
                self._sync_buttons()

    def _sync_buttons(self) -> None:
        if not self._root or not self._root.winfo_exists():
            return
        if self._record_mode or self._unified_mode:
            if self._btn_pause and self._btn_pause.winfo_exists():
                if self._paused:
                    self._btn_pause.config(text="继续录制", state="normal")
                elif self._recording:
                    self._btn_pause.config(text="暂停录制", state="normal")
                else:
                    self._btn_pause.config(text="暂停录制", state="disabled")
            if hasattr(self, "_tip") and self._tip.winfo_exists():
                mode = (self._action_var.get() if self._action_var else "auto")
                mode_tip = (
                    "自动：编辑框→输入、按钮→单击"
                    if mode == "auto"
                    else f"下一步将录为：{_action_label(mode)}"
                )
                if self._recording and not self._paused:
                    n = len(_session_snapshot().get("recorded_steps") or [])
                    self._tip.config(
                        text=f"录制中 · 已录 {n} 步 · Ctrl+点击捕获 · {mode_tip}"
                    )
                elif self._recording and self._paused:
                    n = len(_session_snapshot().get("recorded_steps") or [])
                    self._tip.config(text=f"已暂停 · 已录 {n} 步 · 点「继续录制」恢复")
                else:
                    self._tip.config(
                        text="Ctrl + 点击录入；按住 Ctrl 移动可预览；ESC 结束录制"
                    )
            self._sync_param_rows()
        elif self._btn_arm and self._btn_arm.winfo_exists():
            if self._armed:
                self._btn_arm.config(text="取消拾取", bg="#ef4444")
            else:
                self._btn_arm.config(text="拾取控件", bg="#3b82f6")
            if hasattr(self, "_tip") and self._tip.winfo_exists():
                if self._armed:
                    self._tip.config(text="请在目标窗口按住 Ctrl 并点击要拾取的控件…")
                else:
                    self._tip.config(text="点「拾取控件」后 Ctrl + 点击目标")

    def _selected_action_mode(self) -> str:
        return (self._action_var.get() if self._action_var else "auto").strip().lower() or "auto"

    def _sync_param_rows(self) -> None:
        if not self._action_var:
            return
        act = self._selected_action_mode()
        param_labels = {
            "input": "预填输入（可留空，点选后弹窗填写）",
            "wait": "等待秒数",
            "hotkey": "快捷键（如 ^c、%+{F4}）",
        }
        if self._param_row:
            if act in param_labels:
                self._param_row.pack(fill="x", pady=(4, 0))
                if self._param_label:
                    self._param_label.config(text=param_labels[act])
            else:
                self._param_row.pack_forget()
        if self._verify_row:
            if act == "verify":
                self._verify_row.pack(fill="x", pady=(4, 0))
            else:
                self._verify_row.pack_forget()
        if act in ("wait", "hotkey") and self._param_var:
            defaults = {"wait": "1", "hotkey": "^v"}
            cur = (self._param_var.get() or "").strip()
            if act == "wait" and (not cur or cur == "^v"):
                self._param_var.set(defaults["wait"])
            elif act == "hotkey" and (not cur or re.match(r"^[\d.]+$", cur)):
                self._param_var.set(defaults["hotkey"])

    def _resolve_action_for_pick(self, pick: Dict[str, Any]) -> Tuple[str, bool]:
        mode = self._selected_action_mode()
        if mode == "auto":
            if _ctrl_pressed() and _shift_pressed():
                return "double_click", True
            return _infer_record_action(pick), True
        return mode, False

    def _param_preset(self) -> str:
        return (self._param_var.get() if self._param_var else "").strip()

    def _verify_preset(self) -> str:
        return (self._verify_var.get() if self._verify_var else "auto").strip() or "auto"

    def _brief_show_window(self) -> None:
        if not self._root:
            return
        try:
            self._root.deiconify()
            self._root.lift()
            _apply_picker_topmost(self._root, True)
        except Exception:
            pass

    def _prompt_input_value(self, pick: Dict[str, Any], default: str = "") -> str:
        from tkinter import simpledialog

        self._ensure_capture_window_visible()
        label = pick.get("label") or "控件"
        hint = pick.get("value_text") or ""
        initial = (default or hint or "").strip()
        val = simpledialog.askstring(
            "录制输入",
            f"请输入「{label}」的内容：",
            initialvalue=initial,
            parent=self._root,
        )
        self._ensure_capture_window_visible()
        return (val or "").strip()

    def _build_step_params(
        self, act: str, pick: Dict[str, Any], *, inferred: bool
    ) -> Tuple[str, str, bool]:
        """返回 (input_value, compare_type, ok)。"""
        iv = ""
        ct = ""
        preset = self._param_preset()

        if act == "input":
            iv = preset
            if not iv:
                iv = self._prompt_input_value(pick, pick.get("value_text") or "")
            if not iv:
                return "", "", False
        elif act == "verify":
            iv = self._verify_preset()
            ct = iv
        elif act == "wait":
            iv = preset or "1"
            if not re.match(r"^[\d.]+$", iv):
                iv = "1"
        elif act == "hotkey":
            iv = preset or "^v"
            if not iv:
                return "", "", False
        return iv, ct, True

    def _record_pick_step(self, pick: Dict[str, Any]) -> None:
        spec = dict(pick.get("desktop_spec") or {})
        try:
            _maybe_append_attach_step(spec)
        except Exception:
            pass
        act, inferred = self._resolve_action_for_pick(pick)
        iv, ct, ok = self._build_step_params(act, pick, inferred=inferred)
        if not ok:
            _set_session(message="已取消：未填写必要参数")
            return
        _append_recorded_step(
            pick,
            action=act,
            input_value=iv,
            compare_type=ct,
            inferred=inferred,
            desktop_spec_override=spec,
        )
        _set_session(
            armed=True,
            recording=True,
            paused=False,
            message=_session_snapshot().get("message") or "已录制",
        )
        self._armed = True
        self._ensure_capture_window_visible()
        self._sync_buttons()

    def _record_manual_action_from_toolbar(self) -> None:
        """等待/热键：填写参数后按 Enter 写入步骤（已移除「插入步骤」按钮）。"""
        act = self._selected_action_mode()
        if act not in ("wait", "hotkey"):
            return
        iv, ct, ok = self._build_step_params(act, {}, inferred=False)
        if not ok:
            return
        _append_recorded_step(None, action=act, input_value=iv, compare_type=ct, inferred=False)
        _set_session(message=_session_snapshot().get("message") or "已录入")
        self._sync_buttons()

    def _finish_pick(self, pick: Dict[str, Any]) -> None:
        if (self._record_mode or self._unified_mode) and not self._paused:
            snap_rec = bool(self._record_mode or self._unified_mode)
            if snap_rec:
                mode = self._selected_action_mode()
                if mode in ("wait", "hotkey"):
                    _set_session(
                        message=f"「{_action_label(mode)}」请填写参数后按 Enter 录入，无需点选控件"
                    )
                    self._sync_buttons()
                    return
                self._record_pick_step(pick)
            else:
                act, inferred = self._resolve_action_for_pick(pick)
                iv, ct, ok = self._build_step_params(act, pick, inferred=inferred)
                if ok:
                    _set_session(
                        last_pick={
                            **pick,
                            "record_action": act,
                            "input_value": iv,
                            "compare_type": ct,
                            "automation_layer": "desktop",
                        },
                        armed=True,
                        message="已捕获桌面元素",
                    )
                    self._armed = True
                    self._ensure_capture_window_visible()
            self._sync_buttons()
            return
        if not self._record_mode and not self._unified_mode:
            _set_session(
                last_pick={**pick, "record_action": "click"},
                message="已拾取控件",
            )
            self._armed = False
            _set_session(armed=False, message=_session_snapshot().get("message") or "已拾取")
            if self._highlight:
                self._highlight.hide()
            self._sync_buttons()

    def _toggle_arm(self) -> None:
        self._armed = not self._armed
        _set_session(armed=self._armed, error="")
        self._sync_buttons()

    def _start_recording(self) -> None:
        if self._recording and self._paused:
            self._paused = False
            self._recording = True
            self._armed = True
            self._clear_hover_preview()
            _set_session(recording=True, paused=False, armed=True, error="")
            if self._border:
                self._border.show()
            _set_session(message="录制中：按住 Ctrl 可预览，Ctrl + 点击录入")
            _prewarm_desktop_icon_cache(self._desktop_spec)
            self._sync_buttons()
            return

        self._recording = True
        self._paused = False
        self._armed = True
        self._clear_hover_preview()
        _set_session(recording=True, paused=False, armed=True, error="")
        if self._border:
            self._border.show()
        _set_session(message="录制中：按住 Ctrl 可预览，Ctrl + 点击录入")
        _prewarm_desktop_icon_cache(self._desktop_spec)
        self._sync_buttons()

    def _pause_recording(self) -> None:
        if self._paused:
            self._start_recording()
            return
        if not self._recording:
            return
        self._paused = True
        self._armed = False
        _set_session(recording=True, paused=True, armed=False, message="录制已暂停")
        if self._border:
            self._border.hide()
        if self._highlight:
            self._highlight.hide()
        if self._root:
            try:
                self._root.deiconify()
                self._root.lift()
                _apply_picker_topmost(self._root, True)
            except Exception:
                pass
        self._sync_buttons()

    def _end_recording(self) -> None:
        self._on_close()

    def _on_close(self) -> None:
        self._stop_flag = True
        self._armed = False
        self._recording = False
        self._paused = False
        if self._border:
            self._border.hide()
        self._clear_hover_preview()
        msg = "录制已结束" if self._record_mode else "拾取器已关闭"
        _set_session(
            active=False,
            armed=False,
            recording=False,
            paused=False,
            picker_closed=True,
            message=msg,
        )
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass

    def run_ui(self) -> None:
        if not _PICKER_AVAILABLE:
            _set_session(active=False, error="仅支持 Windows")
            return

        import tkinter as tk

        root = tk.Tk()
        self._root = root
        rec = self._record_mode or self._unified_mode
        ui_bg = "#ffffff" if rec else "#1f2937"
        ui_fg = "#111827" if rec else "#f9fafb"
        ui_sub = "#4b5563" if rec else "#cbd5e1"
        if self._unified_mode:
            win_title = "捕获元素接口"
        elif self._record_mode:
            win_title = "桌面录制"
        else:
            win_title = "桌面拾取"
        root.title(win_title)
        _apply_picker_topmost(root, True)
        root.resizable(False, False)
        root.configure(bg=ui_bg, padx=12, pady=10)
        try:
            root.update_idletasks()
            root.geometry("+16+16" if rec else f"+{max(0, root.winfo_screenwidth() - 360)}+24")
        except Exception:
            pass

        self._highlight = _ElementHighlightOverlay(root)
        if rec:
            self._border = _ScreenBorderOverlay(root)

        bar = tk.Frame(root, bg=ui_bg)
        bar.pack(fill="x")
        if rec:
            tk.Label(
                bar,
                text="按下  Ctrl  +  点击  捕获元素（桌面 / 统一模式）",
                fg="#111827",
                bg=ui_bg,
                font=("Segoe UI", 11, "bold"),
            ).pack(anchor="w")
            if self._unified_mode:
                tk.Label(
                    bar,
                    text="网页：在浏览器窗口按住 Ctrl 并点击页面元素",
                    fg=ui_sub,
                    bg=ui_bg,
                    font=("Segoe UI", 9),
                ).pack(anchor="w", pady=(2, 0))
            tk.Label(
                bar,
                text="悬停预览：按住 Ctrl 移动鼠标显示边框",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(2, 0))
            tk.Label(
                bar,
                text="双击捕获  Ctrl + Shift + 点击",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(2, 0))
            tk.Label(
                bar,
                text="结束捕获  ESC",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(0, 6))
        title = tk.Label(
            bar,
            text="" if rec else "桌面拾取",
            fg=ui_fg,
            bg=ui_bg,
            font=("Segoe UI", 10, "bold"),
        )
        if not rec:
            title.pack(anchor="w")
        self._tip = tk.Label(
            bar,
            text=(
                "Ctrl + 点击 捕获桌面控件；浏览器内 Ctrl + 点击网页元素；ESC 结束"
                if self._unified_mode
                else (
                    "Ctrl + 点击录入；按住 Ctrl 可预览；ESC 结束"
                    if rec
                    else "点「拾取控件」后 Ctrl + 点击目标"
                )
            ),
            fg=ui_sub,
            bg=ui_bg,
            font=("Segoe UI", 9),
            wraplength=300,
            justify="left",
        )
        self._tip.pack(anchor="w", pady=(0, 6))

        if self._record_mode or self._unified_mode:
            self._action_row = tk.Frame(bar, bg=ui_bg)
            self._action_row.pack(fill="x", pady=(0, 4))
            tk.Label(
                self._action_row,
                text="步骤类型（下一步 Ctrl+点击）",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 8),
            ).pack(anchor="w")
            self._action_var = tk.StringVar(value="auto")
            act_menu = tk.OptionMenu(
                self._action_row,
                self._action_var,
                _action_label("auto"),
            )
            act_menu.config(
                bg="#f3f4f6",
                fg=ui_fg,
                activebackground="#e5e7eb",
                highlightthickness=0,
                font=("Segoe UI", 9),
            )
            m = act_menu["menu"]
            m.delete(0, "end")
            m.config(bg="#f9fafb", fg=ui_fg)

            def _set_action(key: str, label: str) -> None:
                self._action_var.set(key)
                act_menu.config(text=label)
                self._sync_buttons()

            for key, label in RECORD_ACTION_CHOICES:
                m.add_command(label=label, command=lambda k=key, lb=label: _set_action(k, lb))
            act_menu.config(text=_action_label("auto"))
            act_menu.pack(anchor="w", fill="x")

            self._param_row = tk.Frame(bar, bg=ui_bg)
            self._param_label = tk.Label(
                self._param_row,
                text="预填输入",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 8),
            )
            self._param_label.pack(anchor="w")
            self._param_var = tk.StringVar(value="")
            self._param_entry = tk.Entry(
                self._param_row,
                textvariable=self._param_var,
                bg="#f9fafb",
                fg=ui_fg,
                insertbackground=ui_fg,
                font=("Segoe UI", 9),
            )
            self._param_entry.pack(fill="x", pady=(2, 0))
            self._param_entry.bind(
                "<Return>",
                lambda _e: self._record_manual_action_from_toolbar(),
            )

            self._verify_row = tk.Frame(bar, bg=ui_bg)
            tk.Label(
                self._verify_row,
                text="验证码类型",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 8),
            ).pack(anchor="w")
            self._verify_var = tk.StringVar(value="auto")
            vmenu = tk.OptionMenu(self._verify_row, self._verify_var, "自动识别")
            vmenu.config(bg="#f3f4f6", fg=ui_fg, highlightthickness=0, font=("Segoe UI", 9))
            vm = vmenu["menu"]
            vm.delete(0, "end")
            vm.config(bg="#f9fafb", fg=ui_fg)

            def _set_verify(key: str, label: str) -> None:
                self._verify_var.set(key)
                vmenu.config(text=label)

            for key, label in VERIFY_TYPE_CHOICES:
                vm.add_command(label=label, command=lambda k=key, lb=label: _set_verify(k, lb))
            vmenu.pack(anchor="w", fill="x")
            self._sync_param_rows()

        btn_row = tk.Frame(bar, bg=ui_bg)
        btn_row.pack(fill="x")

        if self._record_mode or self._unified_mode:
            self._btn_pause = tk.Button(
                btn_row,
                text="暂停录制",
                command=self._pause_recording,
                bg="#f59e0b",
                fg="white",
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", 9),
                state="disabled",
            )
            self._btn_pause.pack(side="left", padx=(0, 6))
            self._btn_end = tk.Button(
                btn_row,
                text="结束捕获" if self._unified_mode else "结束录制",
                command=self._end_recording,
                bg="#ef4444",
                fg="white",
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", 9),
            )
            self._btn_end.pack(side="left", padx=(0, 6))
        else:
            self._btn_arm = tk.Button(
                btn_row,
                text="拾取控件",
                command=self._toggle_arm,
                bg="#3b82f6",
                fg="white",
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", 9),
            )
            self._btn_arm.pack(side="left", padx=(0, 6))
            tk.Button(
                btn_row,
                text="关闭",
                command=self._on_close,
                bg="#4b5563",
                fg="white",
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", 9),
            ).pack(side="left")

        def _collect_hwnds(widget) -> None:
            try:
                h = _hwnd_for_tk(widget)
                self._toolbar_hwnds.add(h)
                _PICKER_UI_HWNDS.add(h)
            except Exception:
                pass
            for ch in widget.winfo_children():
                _collect_hwnds(ch)

        root.update_idletasks()
        _collect_hwnds(root)
        root.bind("<Escape>", lambda _e: self._end_recording())

        auto_rec = bool(self._record_mode or self._unified_mode)
        _set_session(
            active=True,
            picker_closed=False,
            record_mode=self._record_mode,
            unified_mode=self._unified_mode,
            recording=auto_rec,
            paused=False,
            armed=auto_rec,
            desktop_spec=self._desktop_spec,
            message="元素捕获已启动"
            if self._unified_mode
            else ("录制器已启动" if self._record_mode else "拾取器已启动"),
        )
        if auto_rec:
            self._recording = True
            self._armed = True

        def _pump_session() -> None:
            if self._stop_flag:
                return
            disk = _load_session_from_disk() or {}
            if disk.get("shutdown_requested"):
                self._on_close()
                return
            try:
                self._drain_pick_queue()
            except Exception as exc:
                _set_session(error=str(exc))
            root.after(200, _pump_session)

        root.after(200, _pump_session)
        root.after(200, self._schedule_input_poll)
        self._sync_buttons()
        if self._record_mode or self._unified_mode:
            root.after(0, self._clear_hover_preview)
            root.after(50, self._start_recording)
        try:
            root.protocol("WM_DELETE_WINDOW", self._on_close)
            root.mainloop()
        finally:
            self._cancel_input_poll()
            if self._border:
                try:
                    self._border.hide()
                except Exception:
                    pass
            if self._highlight:
                try:
                    self._highlight.hide()
                except Exception:
                    pass


_picker_proc: Optional[subprocess.Popen] = None
_picker_ui_lock = threading.RLock()


def _request_picker_shutdown() -> None:
    """通知子进程自行关闭 UI（先隐藏高亮边框，再 destroy）。"""
    data = _load_session_from_disk() or {}
    with _session_lock:
        merged = {**dict(_session), **data}
        merged["shutdown_requested"] = True
        merged["active"] = False
        merged["armed"] = False
        merged["recording"] = False
        _session.update(merged)
    try:
        _session_file_path().write_text(
            json.dumps(merged, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _stop_picker_process(timeout: float = 8.0, *, fast: bool = False) -> None:
    """结束桌面拾取子进程（tkinter 必须在子进程主线程，不可在 Flask 线程里跑）。"""
    global _picker_proc
    proc = _picker_proc
    _picker_proc = None
    if not proc:
        return
    if fast:
        timeout = min(float(timeout), 1.5)
    if proc.poll() is None:
        _request_picker_shutdown()
        grace = 0.8 if fast else min(2.5, float(timeout))
        deadline = time.time() + grace
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.08 if fast else 0.12)
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1.5 if fast else 3.0)
                except Exception:
                    pass
    _PICKER_UI_HWNDS.clear()


def _picker_child_main(cfg_path: str) -> None:
    """子进程入口：在本进程主线程运行 tkinter。"""
    os.environ["UAT_PICKER_CHILD"] = "1"
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    os.environ["UAT_DESKTOP_PICKER_SESSION"] = str(cfg.get("session_path") or _session_file_path())
    disk = _load_session_from_disk()
    if disk:
        with _session_lock:
            _session.clear()
            _session.update(disk)
    auto_rec = bool(cfg.get("record_mode") or cfg.get("unified_mode"))
    _set_session(
        active=True,
        picker_closed=False,
        message="正在启动捕获器…",
        record_mode=bool(cfg.get("record_mode")),
        unified_mode=bool(cfg.get("unified_mode")),
        recording=auto_rec,
        armed=auto_rec,
        error="",
    )
    ui = _DesktopPickerUI(
        cfg.get("desktop_spec") or {},
        bool(cfg.get("record_mode")),
        unified_mode=bool(cfg.get("unified_mode")),
        prefer_web_clicks=bool(cfg.get("prefer_web_clicks")),
    )
    ui.run_ui()


def _spawn_picker_process(
    desktop_spec: Dict[str, Any],
    record_mode: bool,
    unified_mode: bool,
    *,
    prefer_web_clicks: bool = False,
) -> None:
    global _picker_proc
    session_path = Path(tempfile.gettempdir()) / "uat_desktop_picker_session.json"
    try:
        session_path.unlink(missing_ok=True)
    except OSError:
        pass
    os.environ["UAT_DESKTOP_PICKER_SESSION"] = str(session_path)
    cfg_path = Path(tempfile.gettempdir()) / "uat_desktop_picker_cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "desktop_spec": desktop_spec,
                "record_mode": record_mode,
                "unified_mode": unified_mode,
                "prefer_web_clicks": prefer_web_clicks,
                "session_path": str(session_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = str(Path(__file__).resolve())
    _picker_proc = subprocess.Popen(
        [sys.executable, script, "--picker-child", str(cfg_path)],
        cwd=str(Path(__file__).resolve().parent),
        env={
            **os.environ,
            "UAT_PICKER_CHILD": "1",
            "UAT_DESKTOP_PICKER_SESSION": str(session_path),
        },
    )


def start_desktop_picker(
    desktop_spec: Dict[str, Any],
    *,
    record_mode: bool = False,
    unified_mode: bool = False,
    prefer_web_clicks: bool = False,
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
    case_id: Optional[int] = None,
    skip_initial_stop: bool = False,
) -> Dict[str, Any]:
    """启动桌面拾取/录制/统一捕获悬浮窗（独立子进程，避免 tkinter 崩溃）。"""
    del record_action, input_value, verify_type
    if not desktop_picker_available():
        try:
            from desktop_locator import desktop_runtime_unavailable_reason

            err = desktop_runtime_unavailable_reason() or (
                "桌面拾取不可用（需 Windows 且已安装 pywinauto）"
            )
        except ImportError:
            err = "桌面拾取仅支持 Windows，且需安装 pywinauto（见 requirements-windows.txt）"
        return {"success": False, "error": err}

    with _picker_ui_lock:
        if not skip_initial_stop:
            stop_desktop_picker(fast=True)
        _reset_attach_tracking()
        _PICKER_UI_HWNDS.clear()
        _set_session(
            active=False,
            record_mode=record_mode,
            unified_mode=unified_mode,
            recording=False,
            paused=False,
            armed=False,
            desktop_spec=dict(desktop_spec or {}),
            last_pick=None,
            shutdown_requested=False,
            recorded_steps=[],
            _sent_count=0,
            case_id=int(case_id) if case_id else 0,
            error="",
            picker_closed=False,
            message="正在启动捕获器…",
            starting=True,
        )
        _persist_session_to_disk()

        if not desktop_spec and not record_mode and not unified_mode:
            return {"success": False, "error": "请先选择要操作的应用窗口（从运行中窗口列表点选）"}

        try:
            _spawn_picker_process(
                desktop_spec or {},
                record_mode,
                unified_mode,
                prefer_web_clicks=prefer_web_clicks,
            )
        except Exception as exc:
            return {"success": False, "error": f"无法启动桌面捕获悬浮窗: {exc}"}

        deadline = time.time() + 4.0
        snap: Dict[str, Any] = {}
        while time.time() < deadline:
            time.sleep(0.08)
            if _picker_proc and _picker_proc.poll() is not None:
                err = (_load_session_from_disk() or {}).get("error") or "桌面捕获进程已退出"
                return {"success": False, "error": err}
            snap = _session_snapshot()
            if snap.get("active") and snap.get("recording"):
                break
            if snap.get("active") and snap.get("message") and "启动" in snap.get("message", ""):
                break
        if _picker_proc and _picker_proc.poll() is not None:
            err = (_load_session_from_disk() or {}).get("error") or "桌面捕获进程已退出"
            return {"success": False, "error": err}
        if _picker_proc and _picker_proc.poll() is None:
            return {
                "success": True,
                "record_mode": record_mode,
                "starting": not bool(snap.get("active")),
                "message": snap.get("message") or "正在启动捕获器…",
            }
        return {
            "success": False,
            "error": "桌面悬浮窗未就绪，请查看是否被安全软件拦截",
        }


def stop_desktop_picker(*, fast: bool = False, reset_automation: bool = True) -> Dict[str, Any]:
    """关闭拾取悬浮窗。"""
    with _picker_ui_lock:
        with _session_lock:
            was_active = bool(_session.get("active"))
            recorded = list(_session.get("recorded_steps") or [])
            last_pick = _session.get("last_pick")

        disk = _load_session_from_disk()
        had_proc = _picker_proc is not None and _picker_proc.poll() is None
        _stop_picker_process(timeout=1.5 if fast else 8.0, fast=fast)
        if not fast:
            time.sleep(0.1)
        if disk:
            with _session_lock:
                _session.update(disk)

        if reset_automation and (was_active or had_proc):
            try:
                from desktop_automation import sync_reset_desktop_automation

                sync_reset_desktop_automation()
            except Exception:
                pass

        _set_session(active=False, armed=False, recording=False, paused=False, picker_closed=True)
        return {
            "success": True,
            "stopped": True,
            "recorded_steps": recorded,
            "last_pick": last_pick,
            "was_active": was_active,
        }


def _drain_unsent_recorded_steps(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从会话（含子进程 session 文件）取出尚未同步到前端的录制步骤。"""
    if not (snap.get("record_mode") or snap.get("unified_mode")):
        return []
    recorded = list(snap.get("recorded_steps") or [])
    sent = int(snap.get("_sent_count") or 0)
    if len(recorded) <= sent:
        return []
    return recorded[sent:]


def get_desktop_picker_status(*, consume_last_pick: bool = False) -> Dict[str, Any]:
    """供前端轮询；consume_last_pick 为真时返回 last_pick 后清空（避免重复填入）。"""
    snap = _session_snapshot()
    last = snap.get("last_pick")
    new_steps: List[Dict[str, Any]] = []
    with _session_lock:
        pending = _drain_unsent_recorded_steps(snap)
        if pending:
            new_steps = pending
            sent_after = len(snap.get("recorded_steps") or [])
            _session["_sent_count"] = sent_after
            if _picker_proc and _picker_proc.poll() is None:
                _patch_session_on_disk(_sent_count=sent_after)
        if consume_last_pick and last:
            if _picker_proc and _picker_proc.poll() is None:
                _clear_last_pick_on_disk()
            _session["last_pick"] = None
            last = None
    out = {
        "success": True,
        **snap,
        "last_pick": last,
        "new_recorded_steps": new_steps,
    }
    if snap.get("picker_closed"):
        out["recorded_steps"] = list(snap.get("recorded_steps") or [])
    return out


def sync_start_desktop_picker(
    desktop_spec: Dict[str, Any],
    *,
    record_mode: bool = False,
    unified_mode: bool = False,
    prefer_web_clicks: bool = False,
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
    case_id: Optional[int] = None,
    skip_initial_stop: bool = False,
) -> Dict[str, Any]:
    return start_desktop_picker(
        desktop_spec,
        record_mode=record_mode,
        unified_mode=unified_mode,
        prefer_web_clicks=prefer_web_clicks,
        record_action=record_action,
        input_value=input_value,
        verify_type=verify_type,
        case_id=case_id,
        skip_initial_stop=skip_initial_stop,
    )


def sync_stop_desktop_picker(*, fast: bool = False) -> Dict[str, Any]:
    return stop_desktop_picker(fast=fast)


def sync_get_desktop_picker_status(**kwargs: Any) -> Dict[str, Any]:
    return get_desktop_picker_status(**kwargs)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--picker-child":
        _picker_child_main(sys.argv[2])
    else:
        print("桌面拾取模块需由平台服务调用，或: python desktop_picker.py --picker-child <cfg.json>")
        sys.exit(1)
