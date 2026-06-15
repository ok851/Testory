# -*- coding: utf-8 -*-
"""插件市场：Testory 移动端助手 APK 安装与检测。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_PLUGIN_ID = "mobile-testory-assistant"
_PACKAGE = "com.testory.assistant"
_ROOT = Path(__file__).resolve().parent
_MANIFEST_PATH = _ROOT / "config" / "plugin_bundles" / "testory_mobile_assistant.json"


def _manifest() -> Dict[str, Any]:
    if not _MANIFEST_PATH.is_file():
        return {}
    try:
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_assistant_apk_path() -> Optional[Path]:
    manifest = _manifest()
    filename = manifest.get("apk_filename") or "testory-assistant.apk"
    patterns = manifest.get("local_bundle_search") or [
        "plugin_bundles/{filename}",
        "config/plugin_bundles/{filename}",
        "mobile_assistant_apk/app/build/outputs/apk/debug/{filename}",
    ]
    for pattern in patterns:
        rel = str(pattern).format(filename=filename)
        hit = _ROOT / rel
        if hit.is_file():
            return hit.resolve()
    return None


def _adb_cmd(udid: str = "") -> list:
    from mobile_env_config import adb_path

    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    return cmd


def assistant_installed_on_device(udid: str = "") -> bool:
    try:
        proc = subprocess.run(
            _adb_cmd(udid) + ["shell", "pm", "path", _PACKAGE],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0 and "package:" in (proc.stdout or "")
    except Exception:
        return False


def setup_adb_forward(udid: str = "", device_port: int = 0) -> Tuple[bool, str]:
    """建立 adb forward 到插件 HTTP 端口（由 Agent 调用）。"""
    if device_port <= 0:
        return False, "缺少设备端口"
    try:
        from mobile_automation_gateway.plugin_rpc import ensure_plugin_tunnel

        ok, msg = ensure_plugin_tunnel(udid)
        return ok, msg
    except ImportError:
        return False, "mobile_automation_gateway 未安装"
    except Exception as exc:
        return False, str(exc)


def install_testory_assistant(
    udid: str = "",
    *,
    progress_cb=None,
) -> Dict[str, Any]:
    apk = resolve_assistant_apk_path()
    if apk is None:
        return {
            "success": False,
            "error": "未找到助手 APK。请从插件包目录放置 testory-assistant.apk，或编译 mobile_assistant_apk 工程。",
            "plugin_id": _PLUGIN_ID,
        }
    if progress_cb:
        progress_cb("正在安装 Testory 助手…")
    try:
        proc = subprocess.run(
            _adb_cmd(udid) + ["install", "-r", str(apk)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            return {"success": False, "error": err or "adb install 失败", "plugin_id": _PLUGIN_ID}
    except Exception as exc:
        return {"success": False, "error": str(exc), "plugin_id": _PLUGIN_ID}

    rev_ok, rev_msg = setup_adb_forward(udid, 17123)
    try:
        subprocess.run(
            _adb_cmd(udid) + [
                "shell",
                "am",
                "start",
                "-n",
                f"{_PACKAGE}/.MainActivity",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:
        pass
    return {
        "success": True,
        "plugin_id": _PLUGIN_ID,
        "package": _PACKAGE,
        "apk_path": str(apk),
        "adb_forward": rev_ok,
        "adb_forward_message": rev_msg,
        "message": (
            "Testory 助手已安装。请在设备上打开「Testory Assistant」并点击「开启无障碍服务」，"
            "在系统设置中启用 Testory Assistant（仅需一次）。"
        ),
    }


def get_testory_assistant_catalog_entry() -> Dict[str, Any]:
    manifest = _manifest()
    return {
        "id": _PLUGIN_ID,
        "name": manifest.get("name") or "Testory 移动端助手",
        "icon": "fa-mobile-screen-button",
        "icon_color": "#059669",
        "version": manifest.get("version") or "1.0.0",
        "type": "runtime_bundle",
        "description": manifest.get("description")
        or "安装到模拟器/真机：手机端物理操作录制，经 Agent JSON-RPC 回传步骤与关键帧截图。",
        "features": manifest.get("features")
        or ["无障碍录制", "JSON-RPC", "adb forward", "关键帧截图"],
        "local_bundle_ready": resolve_assistant_apk_path() is not None,
    }
