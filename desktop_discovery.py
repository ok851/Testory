# -*- coding: utf-8 -*-
"""
本机桌面应用发现（无需 .env 配置 exe 路径）。

解析策略（按顺序，命中即返回）：
1. 已是存在的文件路径
2. PATH（shutil.which / where.exe）
3. %WINDIR%\\System32 / SysWOW64（系统小程序）
4. 注册表 App Paths（含 WOW6432Node）
5. 已安装程序卸载项（InstallLocation / DisplayIcon）
6. 开始菜单快捷方式索引（.lnk，启动时懒加载缓存）
7. 可选：Program Files 浅层搜索（DESKTOP_DEEP_SEARCH=1）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_DISCOVERY_AVAILABLE = sys.platform == "win32"

_SKIP_WINDOW_TITLES = frozenset({
    "",
    "Program Manager",
    "MSCTFIME UI",
    "Default IME",
    "Windows Input Experience",
})

_START_MENU_CACHE: Dict[str, str] = {}
_START_MENU_CACHE_AT: float = 0.0
_START_MENU_LOCK = threading.Lock()
_START_MENU_TTL = float(os.environ.get("DESKTOP_START_MENU_CACHE_SEC", "600") or "600")

_APP_PATHS_SUBKEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
)

_UNINSTALL_SUBKEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


@dataclass
class ResolveResult:
    """程序名解析结果。"""

    query: str
    path: str = ""
    method: str = ""
    tried: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.path and os.path.isfile(self.path))


def discovery_available() -> bool:
    return _DISCOVERY_AVAILABLE


def _normalize_query(query: str) -> Tuple[str, str, str, str]:
    """返回 (raw, basename, basename.exe, stem)。"""
    q = (query or "").strip().strip('"').strip("'")
    if not q:
        return "", "", "", ""
    base = os.path.basename(q.replace("/", os.sep))
    stem, ext = os.path.splitext(base)
    if ext.lower() == ".exe":
        base_exe = base
    else:
        base_exe = f"{base}.exe" if base else ""
        if not stem:
            stem = base
    return q, base, base_exe, stem.lower()


def _record(tried: List[str], label: str) -> None:
    if label not in tried:
        tried.append(label)


def _deep_search_enabled() -> bool:
    return (os.environ.get("DESKTOP_DEEP_SEARCH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _resolve_direct_file(q: str, tried: List[str]) -> str:
    _record(tried, "direct_path")
    if os.path.isfile(q):
        return os.path.normpath(q)
    return ""


def _resolve_via_path_env(q: str, base: str, base_exe: str, tried: List[str]) -> str:
    _record(tried, "path_env")
    for candidate in (q, base, base_exe):
        if not candidate:
            continue
        if os.path.isfile(candidate):
            return os.path.normpath(candidate)
        found = shutil.which(candidate)
        if found and os.path.isfile(found):
            return os.path.normpath(found)
    return ""


def _resolve_via_where(base_exe: str, tried: List[str]) -> str:
    if not base_exe:
        return ""
    _record(tried, "where_exe")
    try:
        cp = subprocess.run(
            ["where", base_exe],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if cp.returncode == 0 and cp.stdout:
            for line in cp.stdout.splitlines():
                p = line.strip().strip('"')
                if p and os.path.isfile(p):
                    return os.path.normpath(p)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _resolve_via_system32(base_exe: str, tried: List[str]) -> str:
    if not base_exe:
        return ""
    _record(tried, "system32")
    windir = os.environ.get("WINDIR") or r"C:\Windows"
    for sub in ("System32", "SysWOW64"):
        p = os.path.join(windir, sub, base_exe)
        if os.path.isfile(p):
            return os.path.normpath(p)
    return ""


def _registry_app_path(hive: Any, subkey: str, exe_name: str) -> str:
    import winreg

    try:
        with winreg.OpenKey(hive, rf"{subkey}\{exe_name}") as key:
            val, _ = winreg.QueryValueEx(key, "")
            if val:
                val = val.strip().strip('"')
                if os.path.isfile(val):
                    return os.path.normpath(val)
                if val.lower().endswith(".exe") and os.path.isfile(val.split()[0]):
                    return os.path.normpath(val.split()[0])
    except OSError:
        pass
    return ""


def _resolve_via_app_paths(base_exe: str, tried: List[str]) -> str:
    if not base_exe:
        return ""
    _record(tried, "registry_app_paths")
    import winreg

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in _APP_PATHS_SUBKEYS:
            p = _registry_app_path(hive, sub, base_exe)
            if p:
                return p
    return ""


def _clean_display_icon(raw: str) -> str:
    s = (raw or "").strip().strip('"')
    if not s:
        return ""
    if "," in s:
        s = s.split(",", 1)[0].strip()
    return s


def _resolve_via_uninstall(stem: str, base_exe: str, tried: List[str]) -> str:
    if not stem and not base_exe:
        return ""
    _record(tried, "registry_uninstall")
    import winreg

    stem_l = stem.lower()
    exe_l = base_exe.lower()
    best = ""

    def _score(name: str) -> int:
        n = (name or "").lower()
        if not n:
            return 0
        if n == stem_l or n == exe_l.replace(".exe", ""):
            return 100
        if stem_l and stem_l in n:
            return 80
        if exe_l and exe_l in n:
            return 70
        return 0

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in _UNINSTALL_SUBKEYS:
            try:
                with winreg.OpenKey(hive, sub) as root:
                    count = winreg.QueryInfoKey(root)[0]
                    for i in range(min(count, 800)):
                        try:
                            sk = winreg.EnumKey(root, i)
                            with winreg.OpenKey(root, sk) as appkey:
                                disp = ""
                                loc = ""
                                icon = ""
                                try:
                                    disp, _ = winreg.QueryValueEx(appkey, "DisplayName")
                                except OSError:
                                    pass
                                try:
                                    loc, _ = winreg.QueryValueEx(appkey, "InstallLocation")
                                except OSError:
                                    pass
                                try:
                                    icon, _ = winreg.QueryValueEx(appkey, "DisplayIcon")
                                except OSError:
                                    pass
                                sc = _score(str(disp))
                                if sc < 70:
                                    continue
                                for cand in (
                                    _clean_display_icon(str(icon)),
                                    os.path.join((loc or "").strip(), base_exe)
                                    if loc and base_exe
                                    else "",
                                ):
                                    if cand and os.path.isfile(cand):
                                        if sc >= 80:
                                            return os.path.normpath(cand)
                                        best = best or os.path.normpath(cand)
                                if loc and os.path.isdir(loc) and base_exe:
                                    direct = os.path.join(loc, base_exe)
                                    if os.path.isfile(direct):
                                        if sc >= 80:
                                            return os.path.normpath(direct)
                                        best = best or os.path.normpath(direct)
                        except OSError:
                            continue
            except OSError:
                continue
    return best


def _refresh_start_menu_index() -> Dict[str, str]:
    """构建 开始菜单 .lnk → exe 索引（exe 名 / 快捷方式名为键）。"""
    global _START_MENU_CACHE, _START_MENU_CACHE_AT
    now = time.time()
    with _START_MENU_LOCK:
        if _START_MENU_CACHE and (now - _START_MENU_CACHE_AT) < _START_MENU_TTL:
            return _START_MENU_CACHE

    index: Dict[str, str] = {}
    if not _DISCOVERY_AVAILABLE:
        return index

    ps = r"""
$map = @{}
$roots = @(
  [Environment]::GetFolderPath('CommonPrograms'),
  [Environment]::GetFolderPath('Programs')
)
foreach ($root in $roots) {
  if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
  Get-ChildItem -LiteralPath $root -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
    try {
      $w = New-Object -ComObject WScript.Shell
      $t = $w.CreateShortcut($_.FullName).TargetPath
      if ($t -and ($t -like '*.exe') -and (Test-Path -LiteralPath $t)) {
        $exeKey = [IO.Path]::GetFileName($t).ToLowerInvariant()
        if (-not $map.ContainsKey($exeKey)) { $map[$exeKey] = $t }
        $nameKey = $_.BaseName.ToLowerInvariant()
        if ($nameKey -and -not $map.ContainsKey($nameKey)) { $map[$nameKey] = $t }
      }
    } catch {}
  }
}
$map | ConvertTo-Json -Compress
"""
    try:
        cp = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps,
            ],
            capture_output=True,
            text=True,
            timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if cp.returncode == 0 and cp.stdout.strip():
            data = json.loads(cp.stdout.strip())
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, str) and os.path.isfile(v):
                        index[k.lower()] = os.path.normpath(v)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass

    with _START_MENU_LOCK:
        _START_MENU_CACHE = index
        _START_MENU_CACHE_AT = now
    return index


def _resolve_via_start_menu(stem: str, base_exe: str, tried: List[str]) -> str:
    _record(tried, "start_menu")
    index = _refresh_start_menu_index()
    for key in (
        base_exe.lower() if base_exe else "",
        stem.lower() if stem else "",
    ):
        if key and key in index and os.path.isfile(index[key]):
            return index[key]
    return ""


def _resolve_via_app_catalog(q: str, base: str, base_exe: str, stem: str, tried: List[str]) -> str:
    """.env 别名 + data/desktop_app_catalog.json（含安装包长文件名前缀匹配）。"""
    _record(tried, "app_catalog")
    try:
        from desktop_env_config import load_app_aliases
        from desktop_app_catalog import find_catalog_app
    except ImportError:
        return ""
    aliases = load_app_aliases()
    for key in (q.lower(), base.lower(), base_exe.lower(), stem.lower()):
        if key and key in aliases:
            p = aliases[key]
            if p and os.path.isfile(p):
                return os.path.normpath(p)
    for candidate in (q, base_exe, stem, base):
        app = find_catalog_app(candidate)
        if app:
            p = (app.get("path") or "").strip()
            if p and os.path.isfile(p):
                return os.path.normpath(p)
    return ""


def _resolve_via_deep_search(base_exe: str, tried: List[str]) -> str:
    if not _deep_search_enabled() or not base_exe:
        return ""
    _record(tried, "program_files_scan")
    roots: List[str] = []
    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        v = os.environ.get(name)
        if v:
            roots.append(v)
            if name == "LOCALAPPDATA":
                roots.append(os.path.join(v, "Programs"))
    seen = set()
    for root in roots:
        root = os.path.normpath(root)
        if root in seen or not os.path.isdir(root):
            continue
        seen.add(root)
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                depth = dirpath[len(root) :].count(os.sep)
                if depth > 5:
                    dirnames[:] = []
                    continue
                for fn in filenames:
                    if fn.lower() == base_exe.lower():
                        p = os.path.join(dirpath, fn)
                        if os.path.isfile(p):
                            return os.path.normpath(p)
        except OSError:
            continue
    return ""


def _default_suggestions() -> List[str]:
    return [
        "在步骤「输入值」填写完整路径，如 C:\\Program Files\\App\\client.exe",
        "或先手动打开应用，使用「附着窗口」→「选择当前窗口」（无需找 exe）",
        "在 .env 的 DESKTOP_APP_ALIASES 中为常用客户端配置别名",
    ]


def resolve_executable_with_meta(query: str) -> ResolveResult:
    """
    多策略解析 Windows 可执行程序路径。
    返回 ResolveResult（path 为空表示未找到）。
    """
    q, base, base_exe, stem = _normalize_query(query)
    result = ResolveResult(query=q or (query or ""))
    if not q:
        result.suggestions = _default_suggestions()
        return result

    strategies = (
        lambda: _resolve_direct_file(q, result.tried),
        lambda: _resolve_via_app_catalog(q, base, base_exe, stem, result.tried),
        lambda: _resolve_via_path_env(q, base, base_exe, result.tried),
        lambda: _resolve_via_where(base_exe, result.tried),
        lambda: _resolve_via_system32(base_exe, result.tried),
        lambda: _resolve_via_app_paths(base_exe, result.tried),
        lambda: _resolve_via_uninstall(stem, base_exe, result.tried),
        lambda: _resolve_via_start_menu(stem, base_exe, result.tried),
        lambda: _resolve_via_deep_search(base_exe, result.tried),
    )
    for fn in strategies:
        path = fn()
        if path and os.path.isfile(path):
            result.path = path
            result.method = result.tried[-1] if result.tried else "unknown"
            return result

    result.suggestions = _default_suggestions()
    if not _deep_search_enabled():
        result.suggestions.append(
            "可在 .env 设置 DESKTOP_DEEP_SEARCH=1 启用 Program Files 浅层扫描（较慢）"
        )
    return result


def resolve_executable(query: str) -> str:
    """将程序名解析为可启动路径；未找到返回空字符串。"""
    return resolve_executable_with_meta(query).path


def format_resolve_error(meta: ResolveResult) -> str:
    """生成用户可读的错误说明。"""
    q = meta.query or "?"
    tried = "、".join(meta.tried) if meta.tried else "无"
    lines = [
        f"找不到可执行程序「{q}」。",
        f"已尝试：{tried}。",
    ]
    lines.extend(meta.suggestions[:4])
    return " ".join(lines)


# ---------------------------------------------------------------------------
# 窗口枚举 / 附着 spec（保持不变）
# ---------------------------------------------------------------------------


def _enum_visible_windows_win32() -> List[Dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    results: List[Dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = (buff.value or "").strip()
        if not title or title in _SKIP_WINDOW_TITLES:
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 80 or h < 50:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val = int(pid.value)
        exe_path = process_image_path(pid_val) if pid_val else ""
        exe_name = os.path.basename(exe_path) if exe_path else ""
        results.append({
            "hwnd": int(hwnd),
            "title": title,
            "pid": pid_val,
            "process": exe_name,
            "process_path": exe_path,
            "width": w,
            "height": h,
        })
        return True

    user32.EnumWindows(_callback, 0)
    results.sort(key=lambda x: (x.get("title") or "").lower())
    return results


def list_visible_windows() -> List[Dict[str, Any]]:
    if not _DISCOVERY_AVAILABLE:
        return []
    try:
        return _enum_visible_windows_win32()
    except Exception:
        return []


def list_running_processes() -> List[Dict[str, Any]]:
    """枚举当前运行进程（去重，含 exe 路径）。"""
    if not _DISCOVERY_AVAILABLE:
        return []
    try:
        import psutil
    except ImportError:
        return []

    skip_names = frozenset({
        "system idle process",
        "system",
        "_total",
    })
    by_key: Dict[tuple, Dict[str, Any]] = {}
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            info = proc.info or {}
            pid = int(info.get("pid") or 0)
            name = (info.get("name") or "").strip()
            exe_path = (info.get("exe") or "").strip()
            if not pid or not name:
                continue
            if name.lower() in skip_names:
                continue
            key = (pid, exe_path or name.lower())
            if key in by_key:
                continue
            by_key[key] = {
                "pid": pid,
                "name": name,
                "exe": os.path.basename(exe_path) if exe_path else name,
                "exe_path": exe_path,
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    rows = list(by_key.values())
    rows.sort(key=lambda x: ((x.get("name") or "").lower(), x.get("pid", 0)))
    return rows


def find_windows_for_process(
    exe_path: str = "",
    exe_name: str = "",
    pid: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """按进程匹配当前可见顶层窗口。"""
    exe_path_n = os.path.normcase((exe_path or "").strip())
    exe_name_l = (exe_name or "").strip().lower()
    if not exe_name_l and exe_path_n:
        exe_name_l = os.path.basename(exe_path_n).lower()
    out: List[Dict[str, Any]] = []
    for win in list_visible_windows():
        if pid is not None and int(win.get("pid") or 0) != int(pid):
            continue
        wpath = os.path.normcase((win.get("process_path") or "").strip())
        wexe = (win.get("process") or "").strip().lower()
        if exe_path_n and wpath and wpath == exe_path_n:
            out.append(win)
        elif exe_name_l and wexe == exe_name_l:
            out.append(win)
    return out


def desktop_runtime_snapshot(*, include_catalog: bool = True) -> Dict[str, Any]:
    """运行中窗口 + 进程 +（可选）应用目录摘要。"""
    snap: Dict[str, Any] = {
        "windows": list_visible_windows(),
        "processes": list_running_processes(),
    }
    if include_catalog:
        try:
            from desktop_app_catalog import catalog_meta, list_catalog_apps

            snap["catalog"] = catalog_meta()
            snap["catalog_apps"] = list_catalog_apps()
        except ImportError:
            snap["catalog"] = {}
            snap["catalog_apps"] = []
    return snap


def process_image_path(pid: int) -> str:
    if not pid:
        return ""
    try:
        import psutil

        return (psutil.Process(pid).exe() or "").strip()
    except Exception:
        return ""


def attachment_spec_for_window(hwnd: int) -> tuple[Dict[str, Any], str]:
    """根据窗口句柄生成附着 spec 与窗口标题（写入步骤 desktop_spec，执行时不依赖 .env）。"""
    if not _DISCOVERY_AVAILABLE:
        raise RuntimeError("窗口发现仅支持 Windows")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    h = int(hwnd)
    if not user32.IsWindow(h):
        raise ValueError("窗口已关闭或句柄无效")

    length = user32.GetWindowTextLengthW(h)
    buff = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(h, buff, length + 1)
    title = (buff.value or "").strip()
    if not title:
        raise ValueError("无法读取窗口标题")

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    pid_val = int(pid.value)
    exe_path = process_image_path(pid_val)
    spec: Dict[str, Any] = {
        "hwnd": int(h),
        "window_title": title,
        "window_title_re": f".*{re.escape(title)}.*",
    }
    if exe_path:
        spec["process"] = os.path.basename(exe_path)
        spec["path"] = exe_path
    spec["pid"] = pid_val
    return spec, title


def smart_resolve_launch_path(raw: str) -> str:
    """launch_app 用：别名/路径/程序名 → 可执行路径。"""
    v = (raw or "").strip()
    if not v:
        return ""
    if os.path.isfile(v):
        return os.path.normpath(v)
    meta = resolve_executable_with_meta(v)
    return meta.path if meta.found else v


def invalidate_discovery_cache() -> None:
    """清除开始菜单索引缓存（配置变更后可调用）。"""
    global _START_MENU_CACHE, _START_MENU_CACHE_AT
    with _START_MENU_LOCK:
        _START_MENU_CACHE = {}
        _START_MENU_CACHE_AT = 0.0
