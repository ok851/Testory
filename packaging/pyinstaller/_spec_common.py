# -*- coding: utf-8 -*-
"""PyInstaller spec 共用：数据文件与 hiddenimports（由 .spec 传入项目根目录）。"""
from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


def project_root_from_spec_dir(spec_dir: Path) -> Path:
    """packaging/pyinstaller -> 项目根目录。"""
    return spec_dir.resolve().parent.parent


def _only_module_names(items) -> list:
    """collect_all / collect_submodules 结果中只保留模块名字符串。"""
    out: list = []
    for item in items:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def _collect_all_hidden(pkg: str) -> list:
    try:
        _datas, _binaries, _hidden = collect_all(pkg)
        return _only_module_names(_hidden)
    except Exception:
        return []


def _collect_all_datas(pkg: str) -> list:
    try:
        _datas, _binaries, _hidden = collect_all(pkg)
        return list(_datas) if _datas else []
    except Exception:
        return []


def _collect_all_binaries(pkg: str) -> list:
    try:
        _datas, _binaries, _hidden = collect_all(pkg)
        return list(_binaries) if _binaries else []
    except Exception:
        return []


def project_analysis_bundle(root: Path) -> tuple[list, list, list]:
    """PyInstaller Analysis 用的 (datas, binaries, hiddenimports) 完整集合。"""
    datas = project_datas(root)
    binaries: list = []
    hidden = project_hiddenimports(root)
    for pkg in (
        "playwright",
        "cv2",
        "numpy",
        "greenlet",
        "mss",
        "PIL",
        "certifi",
    ):
        datas.extend(_collect_all_datas(pkg))
        binaries.extend(_collect_all_binaries(pkg))
        hidden.extend(_collect_all_hidden(pkg))
    return datas, binaries, list(dict.fromkeys(_only_module_names(hidden)))


def gateway_analysis_bundle(root: Path) -> tuple[list, list, list]:
    """嵌入式 / 桌面网关 onedir 依赖（含 Playwright 驱动二进制）。"""
    datas: list = []
    binaries: list = []
    hidden = gateway_hiddenimports(root)
    for pkg in ("playwright", "greenlet", "uvicorn", "fastapi", "starlette", "anyio"):
        datas.extend(_collect_all_datas(pkg))
        binaries.extend(_collect_all_binaries(pkg))
        hidden.extend(_collect_all_hidden(pkg))
    try:
        datas += copy_metadata("playwright")
    except Exception:
        pass
    return datas, binaries, list(dict.fromkeys(_only_module_names(hidden)))


def top_level_hiddenimports(root: Path) -> list:
    """项目根目录下的业务 .py 模块（供 Analysis 未静态追踪到的 import）。"""
    skip = {
        "tests",
        "scripts",
        "packaging",
        "website",
        "verify_installation",
        "generate_license",
        "conftest",
    }
    names: list = []
    for py in root.glob("*.py"):
        stem = py.stem
        if stem in skip or stem.startswith("test_"):
            continue
        names.append(stem)
    return names


def project_datas(root: Path) -> list:
    out: list = []
    for rel in ("templates", "static", "config", "plugin_bundles"):
        p = root / rel
        if p.is_dir():
            out.append((str(p), rel))
    example = root / ".env.example"
    if example.is_file():
        out.append((str(example), "."))
    for name in ("ai_provider_catalog.json", "ai_model_registry.json"):
        data_file = root / name
        if data_file.is_file():
            out.append((str(data_file), "."))
            out.append((str(data_file), "config"))

    for pkg in ("playwright", "flask"):
        out.extend(_collect_all_datas(pkg))

    for meta_pkg in ("playwright", "flask"):
        try:
            out += copy_metadata(meta_pkg)
        except Exception:
            pass
    return out


def project_hiddenimports(root: Path) -> list:
    hidden: list = []

    for pkg in (
        "ai_modules",
        "web_capture",
        "browser_runtime",
        "embedded_browser_gateway",
        "desktop_automation_gateway",
        "mobile_automation_gateway",
    ):
        try:
            hidden += _only_module_names(collect_submodules(pkg))
        except Exception:
            pass

    hidden += top_level_hiddenimports(root)

    for pkg in (
        "flask",
        "flask_login",
        "flask_cors",
        "werkzeug",
        "jinja2",
        "dotenv",
        "playwright",
        "requests",
        "apscheduler",
        "openpyxl",
        "reportlab",
        "dukpy",
        "pypdf",
        "docx",
        "cv2",
        "numpy",
        "PIL",
        "websockets",
        "fastapi",
        "uvicorn",
        "starlette",
        "pydantic",
        "anyio",
        "httptools",
        "watchfiles",
        "pywinauto",
        "pywin32_system32",
    ):
        hidden += _collect_all_hidden(pkg)

    if sys_platform_win32():
        for pkg in ("win32com", "win32com.client", "pythoncom", "pywintypes"):
            try:
                hidden += _only_module_names(collect_submodules(pkg))
            except Exception:
                pass

    hidden += [
        "ai_config_paths",
        "license_manager",
        "database",
        "playwright_automation",
        "install_paths",
        "env_example_sync",
        "embedded_browser_client",
        "embedded_browser_service_bootstrap",
        "desktop_service_bootstrap",
        "deployment_hooks",
        "deployment_config",
        "subprocess_win",
        "embedded_browser_service_bootstrap",
        "desktop_user_data",
        "desktop_startup",
        "playwright._impl._api_structures",
        "playwright._impl._driver",
        "greenlet",
    ]
    return list(dict.fromkeys(_only_module_names(hidden)))


def gateway_hiddenimports(root: Path) -> list:
    """网关 onedir：依赖少于完整后端。"""
    hidden: list = [
        "install_paths",
        "embedded_browser_client",
        "subprocess_win",
        "dotenv",
        "uvicorn",
        "fastapi",
        "starlette",
        "pydantic",
        "anyio",
    ]
    hidden += _collect_all_hidden("uvicorn")
    hidden += _collect_all_hidden("fastapi")
    return list(dict.fromkeys(_only_module_names(hidden)))


def sys_platform_win32() -> bool:
    import sys

    return sys.platform == "win32"
