# -*- coding: utf-8 -*-
"""Windows 桌面控件定位（pywinauto UIA / Win32）。"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from desktop_discovery import format_resolve_error, resolve_executable, resolve_executable_with_meta
except ImportError:
    def resolve_executable(query: str) -> str:
        return (query or "").strip()

    def resolve_executable_with_meta(query: str):
        return None

    def format_resolve_error(meta) -> str:
        return "找不到可执行程序"

_DESKTOP_AVAILABLE = sys.platform == "win32"
if _DESKTOP_AVAILABLE:
    try:
        from pywinauto import Application  # type: ignore
        from pywinauto.findwindows import ElementNotFoundError  # type: ignore
    except ImportError:
        _DESKTOP_AVAILABLE = False
        Application = None  # type: ignore
        ElementNotFoundError = Exception  # type: ignore


def desktop_runtime_available() -> bool:
    return _DESKTOP_AVAILABLE


def parse_desktop_spec(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return {}
    return {}


def is_desktop_shell_spec(spec: Optional[Dict[str, Any]]) -> bool:
    """Program Manager / 桌面图标层：宜优先坐标或 UIA 路径，不宜仅用顶层窗口名定位。"""
    s = spec or {}
    if (s.get("surface") or "").strip().lower() == "desktop_shell":
        return True
    title = (s.get("window_title") or "").strip().lower()
    if title in ("program manager", "progman", "program manager "):
        return True
    proc = (s.get("process") or "").strip().lower()
    if proc == "explorer.exe" and title.startswith("program"):
        return True
    return False


def _split_coordinate(value: str) -> Tuple[int, int]:
    parts = re.split(r"[,;\s]+", (value or "").strip())
    if len(parts) < 2:
        raise ValueError(f"坐标格式无效，应为 x,y：{value}")
    return int(float(parts[0])), int(float(parts[1]))


def _coerce_search_root(
    window: Any,
    desktop_spec: Optional[Dict[str, Any]] = None,
    app: Any = None,
) -> Any:
    """
    pywinauto 的 child_window 仅在 WindowSpecification 上可用；
    attach 后若持有 UIAWrapper，需通过 Application.window(handle) 转回规格对象。
    """
    if window is None:
        raise RuntimeError("未附着桌面窗口，请先执行 attach_window 或 launch_app")
    if hasattr(window, "child_window"):
        return window
    handle = int(getattr(window, "handle", 0) or 0)
    if not handle and desktop_spec:
        handle = int(desktop_spec.get("hwnd") or 0)
    if not handle:
        raise RuntimeError("无法解析窗口句柄，请重新捕获元素或检查 desktop_spec.hwnd")
    if app is not None and hasattr(app, "window"):
        return app.window(handle=handle)
    be = (desktop_spec or {}).get("backend") or "uia"
    if be not in ("uia", "win32"):
        be = "uia"
    bound = Application(backend=be).connect(handle=handle, timeout=15)
    return bound.window(handle=handle)


def _normalize_best_match(spec: Dict[str, Any]) -> Any:
    """从 desktop_spec 读取 best_match；False/0 表示精确匹配，不得传给 pywinauto。"""
    raw = (spec or {}).get("best_match")
    if raw is None:
        return None
    if isinstance(raw, bool):
        return True if raw else None
    if isinstance(raw, (int, float)):
        return True if raw else None
    s = str(raw).strip().lower()
    if s in ("", "0", "false", "no", "off"):
        return None
    if s in ("1", "true", "yes", "on"):
        return True
    return str(raw).strip()


def _child_window_search(root: Any, *, spec: Optional[Dict[str, Any]] = None, **criteria: Any) -> Any:
    """
    pywinauto.findwindows：best_match 只要非 None 就会走模糊匹配。
    传入 False 会被当成搜索词 \"False\"，导致 MatchError。
    """
    kw = {k: v for k, v in criteria.items() if v is not None and v != ""}
    bm = _normalize_best_match(spec or {})
    if bm is True:
        return root.child_window(best_match=True, **kw)
    if isinstance(bm, str):
        return root.child_window(best_match=bm, **kw)
    return root.child_window(**kw)


def _path_node_to_kwargs(node: Dict[str, Any]) -> Dict[str, Any]:
    """将捕获的 UIA 节点转为 pywinauto child_window 参数（对齐竞品：class + uia-name）。"""
    kwargs: Dict[str, Any] = {}
    if node.get("automation_id"):
        kwargs["auto_id"] = node["automation_id"]
    name = (node.get("name") or "").strip()
    if name:
        kwargs["title"] = name
    cls = (node.get("class_name") or "").strip()
    if cls:
        kwargs["class_name"] = cls
    ct = (node.get("control_type") or "").strip()
    if ct and not cls:
        if ct in ("ListItem", "List", "Pane", "Window", "Button", "Edit", "Text"):
            kwargs["control_type"] = ct
    return kwargs


def _explorer_executable_path(spec: Optional[Dict[str, Any]] = None) -> str:
    s = spec or {}
    path = (s.get("path") or "").strip()
    if path and os.path.isfile(path):
        return path
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(sysroot, "explorer.exe")


def _find_explorer_pid() -> int:
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name"]):
            if (proc.info.get("name") or "").lower() == "explorer.exe":
                return int(proc.info["pid"] or 0)
    except Exception:
        pass
    return 0


def attach_desktop_shell(
    desktop_spec: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Any]:
    """
    附着 Windows 桌面图标层（explorer / Progman / WorkerW），与竞品
    Window[cls=WorkerW|Progman, app=explorer] 一致。

    注意：pywinauto.connect(process=) 仅接受整数 PID，不能传 "explorer.exe"。
    """
    if not _DESKTOP_AVAILABLE:
        raise RuntimeError("桌面自动化不可用")
    spec = desktop_spec or {}
    be = (spec.get("backend") or "uia").strip().lower()
    if be not in ("uia", "win32"):
        be = "uia"
    timeout = int(spec.get("timeout", 20) or 20)
    hwnd = int(spec.get("hwnd") or 0)
    pid_raw = spec.get("pid")
    pid = int(pid_raw) if pid_raw is not None and str(pid_raw).strip().isdigit() else 0

    app: Any = None
    win: Any = None
    last_err: Optional[Exception] = None

    if hwnd:
        app = Application(backend=be).connect(handle=hwnd, timeout=timeout)
        win = app.window(handle=hwnd)
        try:
            win.wait("exists", timeout=min(timeout, 8))
        except Exception:
            pass
        return app, win.wrapper_object() if hasattr(win, "wrapper_object") else win

    if pid > 0:
        try:
            app = Application(backend=be).connect(process=pid, timeout=timeout)
        except Exception as exc:
            last_err = exc

    if app is None:
        exe = _explorer_executable_path(spec)
        if os.path.isfile(exe):
            try:
                app = Application(backend=be).connect(path=exe, timeout=timeout)
            except Exception as exc:
                last_err = exc

    if app is None:
        ep = _find_explorer_pid()
        if ep > 0:
            try:
                app = Application(backend=be).connect(process=ep, timeout=timeout)
            except Exception as exc:
                last_err = exc

    if app is None:
        for tre in (
            r".*Program Manager.*",
            r".*Program Manager",
        ):
            try:
                app = Application(backend=be).connect(title_re=tre, timeout=timeout)
                break
            except Exception as exc:
                last_err = exc

    if app is None:
        raise RuntimeError(
            "无法附着 Windows 桌面（explorer/Program Manager）。"
            f"请重新「选择当前窗口」或捕获元素。{last_err or ''}"
        )

    for criteria in (
        {"class_name": "Progman"},
        {"class_name": "WorkerW", "title_re": r".*"},
    ):
        try:
            cand = app.window(**criteria)
            cand.wait("exists", timeout=3)
            win = cand
            break
        except Exception:
            continue
    if win is None:
        if hwnd:
            win = app.window(handle=hwnd)
        else:
            win = _resolve_main_window(
                app, timeout=min(timeout, 15), title_re=r".*Program Manager.*"
            )
    return app, win.wrapper_object() if hasattr(win, "wrapper_object") else win


def resolve_shell_desktop_icon(
    icon_name: str,
    window: Any = None,
    desktop_spec: Optional[Dict[str, Any]] = None,
    app: Any = None,
) -> Any:
    """在桌面 SysListView32 下按名称解析 ListItem（竞品 uia-name=控制面板）。"""
    name = (icon_name or "").strip()
    if not name:
        raise ValueError("桌面图标名称为空")
    if app is None or window is None:
        app, window = attach_desktop_shell(desktop_spec)
    root = _coerce_search_root(window, desktop_spec, app)
    chains = [
        [
            {"class_name": "SHELLDLL_DefView"},
            {"class_name": "SysListView32"},
            {"name": name, "control_type": "ListItem"},
        ],
        [
            {"class_name": "SysListView32"},
            {"name": name, "control_type": "ListItem"},
        ],
        [{"name": name, "control_type": "ListItem"}],
    ]
    last_err: Optional[Exception] = None
    for nodes in chains:
        try:
            return _resolve_uia_path_chain(window, nodes, desktop_spec, app)
        except Exception as exc:
            last_err = exc
    if last_err:
        raise last_err
    raise RuntimeError(f"未在桌面找到图标「{name}」")


# 与 UiPath/影刀等一致：已知系统文件夹用 shell: 协议打开（invoke/坐标失败时回退）
_SHELL_FOLDER_URI: Dict[str, str] = {
    "回收站": "shell:RecycleBinFolder",
    "recycle bin": "shell:RecycleBinFolder",
    "控制面板": "shell:ControlPanelFolder",
    "control panel": "shell:ControlPanelFolder",
    "此电脑": "shell:MyComputerFolder",
    "我的电脑": "shell:MyComputerFolder",
    "this pc": "shell:MyComputerFolder",
    "computer": "shell:MyComputerFolder",
}


def shell_open_folder(icon_name: str) -> bool:
    """通过 Windows Shell 打开已知桌面文件夹/图标（不依赖 UIA invoke）。"""
    key = (icon_name or "").strip()
    if not key:
        return False
    uri = _SHELL_FOLDER_URI.get(key) or _SHELL_FOLDER_URI.get(key.lower())
    if not uri:
        return False
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def _point_in_rect(x: int, y: int, rect: Any) -> bool:
    try:
        return (
            int(rect.left) <= int(x) <= int(rect.right)
            and int(rect.top) <= int(y) <= int(rect.bottom)
        )
    except Exception:
        return False


def _desktop_sys_list_view(root: Any, spec: Optional[Dict[str, Any]] = None) -> Any:
    """定位桌面图标列表 SysListView32（Progman → DefView → ListView）。"""
    chains = (
        [
            {"class_name": "SHELLDLL_DefView"},
            {"class_name": "SysListView32"},
        ],
        [{"class_name": "SysListView32"}],
    )
    last_err: Optional[Exception] = None
    for nodes in chains:
        try:
            ctrl = root
            for node in nodes:
                ctrl = _child_window_search(ctrl, spec=spec, **node)
            return ctrl
        except Exception as exc:
            last_err = exc
    if last_err:
        raise last_err
    raise RuntimeError("未找到桌面 SysListView32")


_SHELL_ICON_CACHE_TTL = float(
    os.environ.get("DESKTOP_ICON_CACHE_SEC", "4") or "4"
)
_SHELL_ICON_CACHE: Dict[str, Any] = {
    "key": 0,
    "ts": 0.0,
    "bounds": [],
    "refreshing": False,
}
_SHELL_ICON_CACHE_LOCK = threading.Lock()


def invalidate_desktop_icon_cache() -> None:
    with _SHELL_ICON_CACHE_LOCK:
        _SHELL_ICON_CACHE["ts"] = 0.0
        _SHELL_ICON_CACHE["bounds"] = []


def _cache_key(spec: Optional[Dict[str, Any]]) -> int:
    return int((spec or {}).get("hwnd") or 0)


def _collect_desktop_icon_bounds(
    window: Any,
    desktop_spec: Optional[Dict[str, Any]] = None,
    app: Any = None,
) -> List[Dict[str, Any]]:
    """扫描桌面 ListItem 矩形（优先 children，避免 descendants 全树遍历）。"""
    root = _coerce_search_root(window, desktop_spec, app)
    lv = _desktop_sys_list_view(root, desktop_spec)
    rows: List[Dict[str, Any]] = []
    try:
        items = lv.children()
    except Exception:
        items = []
    if not items:
        try:
            items = lv.descendants(control_type="ListItem")
        except Exception:
            items = []
    for raw in items:
        try:
            item = raw.wrapper_object() if hasattr(raw, "wrapper_object") else raw
            rect = item.rectangle()
            name = ""
            try:
                name = (getattr(item.element_info, "name", None) or "").strip()
            except Exception:
                pass
            rows.append(
                {
                    "left": int(rect.left),
                    "top": int(rect.top),
                    "right": int(rect.right),
                    "bottom": int(rect.bottom),
                    "name": name,
                }
            )
        except Exception:
            continue
    return rows


def _refresh_desktop_icon_cache_sync(
    desktop_spec: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
) -> List[Dict[str, Any]]:
    spec = dict(desktop_spec or {})
    key = _cache_key(spec)
    now = time.time()
    with _SHELL_ICON_CACHE_LOCK:
        if (
            not force
            and key
            and key == _SHELL_ICON_CACHE["key"]
            and _SHELL_ICON_CACHE["bounds"]
            and now - float(_SHELL_ICON_CACHE["ts"] or 0) < _SHELL_ICON_CACHE_TTL
        ):
            return list(_SHELL_ICON_CACHE["bounds"])
    app, window = attach_desktop_shell(spec)
    bounds = _collect_desktop_icon_bounds(window, spec, app)
    with _SHELL_ICON_CACHE_LOCK:
        _SHELL_ICON_CACHE["key"] = key
        _SHELL_ICON_CACHE["ts"] = time.time()
        _SHELL_ICON_CACHE["bounds"] = bounds
    return bounds


def _schedule_desktop_icon_cache_refresh(
    desktop_spec: Optional[Dict[str, Any]] = None,
) -> None:
    with _SHELL_ICON_CACHE_LOCK:
        if _SHELL_ICON_CACHE.get("refreshing"):
            return
        _SHELL_ICON_CACHE["refreshing"] = True

    def _worker() -> None:
        try:
            _refresh_desktop_icon_cache_sync(desktop_spec, force=True)
        except Exception:
            pass
        finally:
            with _SHELL_ICON_CACHE_LOCK:
                _SHELL_ICON_CACHE["refreshing"] = False

    threading.Thread(target=_worker, daemon=True, name="desktop-icon-cache").start()


def refresh_desktop_icon_cache(
    desktop_spec: Optional[Dict[str, Any]] = None,
    *,
    force: bool = False,
    background: bool = False,
) -> List[Dict[str, Any]]:
    """
    获取桌面图标矩形缓存。background=True 时仅异步刷新，立即返回已有缓存（供悬停高亮）。
    """
    spec = dict(desktop_spec or {})
    key = _cache_key(spec)
    now = time.time()
    with _SHELL_ICON_CACHE_LOCK:
        stale = (
            not key
            or key != _SHELL_ICON_CACHE["key"]
            or now - float(_SHELL_ICON_CACHE["ts"] or 0) >= _SHELL_ICON_CACHE_TTL
        )
        bounds = list(_SHELL_ICON_CACHE["bounds"] or [])
    if background:
        if stale:
            _schedule_desktop_icon_cache_refresh(spec)
        return bounds
    if force or stale or not bounds:
        return _refresh_desktop_icon_cache_sync(spec, force=True)
    return bounds


def _hit_test_icon_bounds(
    x: int,
    y: int,
    bounds: List[Dict[str, Any]],
) -> Optional[Tuple[int, int, int, int, str]]:
    best: Optional[Dict[str, Any]] = None
    best_area: Optional[int] = None
    for b in bounds:
        if not _point_in_rect(x, y, b):
            continue
        area = max(
            1,
            (int(b["right"]) - int(b["left"])) * (int(b["bottom"]) - int(b["top"])),
        )
        if best is None or area < (best_area or area + 1):
            best = b
            best_area = area
    if not best:
        return None
    return (
        int(best["left"]),
        int(best["top"]),
        int(best["right"]),
        int(best["bottom"]),
        str(best.get("name") or ""),
    )


def desktop_icon_rect_at_point(
    x: int,
    y: int,
    desktop_spec: Optional[Dict[str, Any]] = None,
    *,
    background_refresh: bool = True,
) -> Optional[Tuple[int, int, int, int]]:
    """悬停高亮用：仅矩形命中，不附着 UIA 控件。"""
    bounds = refresh_desktop_icon_cache(
        desktop_spec, background=background_refresh
    )
    if not bounds and background_refresh:
        bounds = refresh_desktop_icon_cache(desktop_spec, force=True)
    hit = _hit_test_icon_bounds(x, y, bounds)
    if not hit:
        return None
    return hit[0], hit[1], hit[2], hit[3]


def desktop_listitem_at_screen_point(
    x: int,
    y: int,
    window: Any = None,
    desktop_spec: Optional[Dict[str, Any]] = None,
    app: Any = None,
) -> Any:
    """
    在桌面图标层按屏幕坐标命中 ListItem（竞品/RPA 常用：列表矩形包含点，而非 from_point）。
    多个重叠时取面积最小者。
    """
    if app is None or window is None:
        app, window = attach_desktop_shell(desktop_spec)
    bounds = refresh_desktop_icon_cache(desktop_spec, force=True)
    hit = _hit_test_icon_bounds(x, y, bounds)
    if hit and hit[4]:
        try:
            return resolve_shell_desktop_icon(
                hit[4], window, desktop_spec, app=app
            )
        except Exception:
            pass
    root = _coerce_search_root(window, desktop_spec, app)
    lv = _desktop_sys_list_view(root, desktop_spec)
    best: Any = None
    best_area: Optional[int] = None
    try:
        items = lv.children()
    except Exception:
        items = []
    if not items:
        try:
            items = lv.descendants(control_type="ListItem")
        except Exception:
            items = []
    for raw in items:
        try:
            item = raw.wrapper_object() if hasattr(raw, "wrapper_object") else raw
            rect = item.rectangle()
            if not _point_in_rect(x, y, rect):
                continue
            area = max(1, (int(rect.right) - int(rect.left)) * (int(rect.bottom) - int(rect.top)))
            if best is None or area < (best_area or area + 1):
                best = item
                best_area = area
        except Exception:
            continue
    return best


def resolve_desktop_icon_at_point(
    x: int,
    y: int,
    window: Any = None,
    desktop_spec: Optional[Dict[str, Any]] = None,
    app: Any = None,
) -> Any:
    ctrl = desktop_listitem_at_screen_point(x, y, window, desktop_spec, app)
    if ctrl is None:
        raise RuntimeError(
            f"屏幕坐标 ({x},{y}) 未命中任何桌面图标，请将光标对准图标中心后重新捕获"
        )
    return ctrl


def _uia_path_nodes(selector_value: str) -> List[Dict[str, Any]]:
    path = (selector_value or "").strip()
    if not path:
        return []
    if path.startswith("["):
        nodes = json.loads(path)
    else:
        nodes = json.loads(path)
    return [n for n in nodes if isinstance(n, dict)]


def _resolve_uia_path_chain(
    window: Any,
    nodes: List[Dict[str, Any]],
    desktop_spec: Optional[Dict[str, Any]] = None,
    app: Any = None,
) -> Any:
    root = _coerce_search_root(window, desktop_spec, app)
    ctrl = root
    for node in nodes:
        kwargs = _path_node_to_kwargs(node)
        if not kwargs:
            continue
        ctrl = _child_window_search(ctrl, spec=desktop_spec, **kwargs)
    return ctrl.wrapper_object() if hasattr(ctrl, "wrapper_object") else ctrl


def resolve_control(
    window: Any,
    selector_type: str,
    selector_value: str,
    desktop_spec: Optional[Dict[str, Any]] = None,
    app: Any = None,
) -> Any:
    """
    在已附着窗口内解析控件。
    selector_type: automation_id | name | control_type | uia_path | coordinate
    """
    st = (selector_type or "automation_id").strip().lower()
    sv = (selector_value or "").strip()
    spec = desktop_spec or {}

    if st == "coordinate":
        x, y = _split_coordinate(sv or spec.get("coordinate", ""))
        w = _coerce_search_root(window, spec, app) if window is not None else window
        target = w.wrapper_object() if hasattr(w, "wrapper_object") else w
        return target.click_input(coords=(x, y))

    if st == "uia_path":
        nodes = _uia_path_nodes(sv)
        if not nodes:
            raise ValueError("uia_path 为空")
        return _resolve_uia_path_chain(window, nodes, spec, app)

    kwargs: Dict[str, Any] = {}
    if st in ("automation_id", "auto_id"):
        kwargs["auto_id"] = sv
    elif st == "name":
        if is_desktop_shell_spec(spec):
            return resolve_shell_desktop_icon(
                sv, window, spec, app=app
            )
        kwargs["title"] = sv
    elif st == "control_type":
        kwargs["control_type"] = sv
    elif st == "class_name":
        kwargs["class_name"] = sv
    else:
        kwargs["title_re"] = sv

    root = _coerce_search_root(window, spec, app)
    ctrl = _child_window_search(root, spec=spec, **kwargs)
    return ctrl.wrapper_object() if hasattr(ctrl, "wrapper_object") else ctrl


def _resolve_main_window(
    app: Any,
    *,
    timeout: int = 30,
    title_re: str = "",
) -> Any:
    """启动/连接后等待主窗口出现（避免进程已起但窗口未创建）。"""
    deadline = time.time() + max(1, int(timeout))
    last_err: Optional[Exception] = None
    tre = (title_re or "").strip()

    while time.time() < deadline:
        try:
            if tre:
                win = app.window(title_re=tre)
                win.wait("exists", timeout=2)
                return win
            win = app.top_window()
            try:
                win.wait("ready", timeout=2)
            except Exception:
                pass
            return win.wrapper_object() if hasattr(win, "wrapper_object") else win
        except RuntimeError as e:
            last_err = e
            if "No windows for that process" not in str(e):
                raise
        except Exception as e:
            last_err = e
        time.sleep(0.25)

    if last_err:
        raise RuntimeError(
            "应用进程已启动，但在限定时间内未找到可用窗口。"
            "若刚点击了「停止执行」，请重新运行用例；否则请检查程序名是否正确、"
            "或改用「附着窗口」+「选择当前窗口」。"
        ) from last_err
    raise RuntimeError("未能获取应用主窗口")


def attach_application(
    desktop_spec: Dict[str, Any],
    backend: Optional[str] = None,
) -> Tuple[Any, Any]:
    """
    附着或启动应用，返回 (Application, 主窗口对象，多为 UIAWrapper 或 WindowSpecification)。
    desktop_spec 支持: path, process, window_title, window_title_re, cmd_line, backend
    """
    if not _DESKTOP_AVAILABLE:
        raise RuntimeError(
            "桌面自动化不可用：请在 Windows 上安装 pywinauto（pip install pywinauto）"
        )

    be = (backend or desktop_spec.get("backend") or "uia").strip().lower()
    if be not in ("uia", "win32"):
        be = "uia"

    hwnd_raw = desktop_spec.get("hwnd")
    path = (desktop_spec.get("path") or desktop_spec.get("exe") or "").strip()
    process = (desktop_spec.get("process") or "").strip()
    pid = desktop_spec.get("pid")
    title = (desktop_spec.get("window_title") or "").strip()
    title_re = (desktop_spec.get("window_title_re") or title or "").strip()
    cmd_line = (desktop_spec.get("cmd_line") or "").strip()
    timeout = int(desktop_spec.get("timeout", 25) or 25)
    window_wait = min(timeout, 25)

    app: Any = None
    if hwnd_raw is not None and str(hwnd_raw).strip() != "":
        h = int(hwnd_raw)
        app = Application(backend=be).connect(handle=h, timeout=timeout)
        win = app.window(handle=h)
        try:
            win.wait("exists", timeout=min(window_wait, 10))
        except Exception:
            pass
        return app, win.wrapper_object() if hasattr(win, "wrapper_object") else win
    if pid is not None and str(pid).strip() != "":
        app = Application(backend=be).connect(process=int(pid), timeout=timeout)
        win = _resolve_main_window(app, timeout=window_wait, title_re=title_re if title_re else "")
        return app, win
    if path or cmd_line:
        cmd = (cmd_line or path).strip()
        if not cmd_line and path:
            resolved = resolve_executable(path)
            if resolved:
                cmd = resolved
            elif os.path.isfile(path):
                cmd = path
            else:
                meta = resolve_executable_with_meta(path)
                raise FileNotFoundError(
                    format_resolve_error(meta)
                    if meta
                    else f"找不到可执行程序「{path}」"
                )
        app = Application(backend=be).start(cmd, timeout=min(timeout, 20))
    elif process:
        app = Application(backend=be).connect(path=process, timeout=timeout)
    elif title_re:
        app = Application(backend=be).connect(title_re=title_re, timeout=timeout)
    elif title:
        app = Application(backend=be).connect(title=title, timeout=timeout)
    else:
        raise ValueError("desktop_spec 需包含 path、process 或 window_title/window_title_re")

    win = _resolve_main_window(
        app, timeout=window_wait, title_re=title_re if title_re else ""
    )
    return app, win
