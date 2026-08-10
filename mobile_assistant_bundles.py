# -*- coding: utf-8 -*-
"""插件市场：Testory 移动端助手 APK 安装与检测。"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

_PLUGIN_ID = "mobile-testory-assistant"
_PACKAGE = "com.testory.assistant.v2"  # v2 APK package
_LEGACY_PACKAGE = "com.testory.assistant"  # v1 legacy fallback
_ROOT = Path(__file__).resolve().parent
_MANIFEST_PATH = _ROOT / "config" / "plugin_bundles" / "testory_mobile_assistant.json"
_STAGED_APK_NAME = "testory-assistant.apk"
_INSTALL_SCOPE_ACTIVE = False


@contextmanager
def assistant_device_install_scope() -> Iterator[None]:
    """仅「安装插件」等显式入口可调用 adb install；连接设备禁止安装。"""
    global _INSTALL_SCOPE_ACTIVE
    _INSTALL_SCOPE_ACTIVE = True
    try:
        yield
    finally:
        _INSTALL_SCOPE_ACTIVE = False


def _device_install_allowed() -> bool:
    return _INSTALL_SCOPE_ACTIVE


def _manifest() -> Dict[str, Any]:
    if not _MANIFEST_PATH.is_file():
        return {}
    try:
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_assistant_apk_path() -> Optional[Path]:
    """安装包内置 APK（发布目录 / 开发构建产物）。"""
    # 优先使用 gradle 最新构建产物（无需手动复制）
    gradle_build = _ROOT / "mobile_assistant_apk_v2" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    if gradle_build.is_file():
        return gradle_build.resolve()

    manifest = _manifest()
    filename = manifest.get("apk_filename") or _STAGED_APK_NAME
    patterns = manifest.get("local_bundle_search") or [
        "config/plugin_bundles/{filename}",
    ]
    for pattern in patterns:
        rel = str(pattern).format(filename=filename)
        hit = _ROOT / rel
        if hit.is_file():
            return hit.resolve()
    return None


def assistant_staging_dir() -> Path:
    from web_capture.plugin_market import software_extensions_root

    dest = software_extensions_root() / "mobile" / "assistant"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def get_staged_assistant_apk() -> Optional[Path]:
    hit = assistant_staging_dir() / _STAGED_APK_NAME
    return hit.resolve() if hit.is_file() else None


def is_assistant_prepared() -> bool:
    """PC 侧插件包是否已就绪（不要求已连接设备）。"""
    return get_staged_assistant_apk() is not None


def resolve_install_apk_path() -> Optional[Path]:
    """安装时优先使用仓库内置 bundle，避免 extensions 目录残留旧副本。"""
    bundled = resolve_assistant_apk_path()
    if bundled is not None:
        return bundled
    return get_staged_assistant_apk()


def _adb_cmd(udid: str = "") -> list:
    from mobile_env_config import adb_path

    cmd = [adb_path()]
    if udid:
        cmd.extend(["-s", udid])
    return cmd


def resolve_target_udid_for_push() -> str:
    """解析可用于 adb install 的设备 serial（无设备则返回空字符串）。"""
    from mobile_device_manager import get_connected_udid, list_usb_devices, pick_default_device

    udid = (get_connected_udid() or "").strip()
    if udid:
        return udid
    dev = pick_default_device()
    if dev and dev.get("state") == "device":
        return (dev.get("udid") or "").strip()
    for d in list_usb_devices():
        if d.get("state") == "device":
            return (d.get("udid") or "").strip()
    return ""


def _expected_version_code() -> int:
    try:
        return int(_manifest().get("apk_version_code") or 0)
    except (TypeError, ValueError):
        return 0


def get_device_assistant_version_code(udid: str = "") -> int:
    udid = (udid or resolve_target_udid_for_push() or "").strip()
    if not udid:
        return 0
    try:
        proc = subprocess.run(
            _adb_cmd(udid) + ["shell", "dumpsys", "package", _PACKAGE],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return 0
        import re

        m = re.search(r"versionCode=(\d+)", proc.stdout or "")
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def device_assistant_needs_upgrade(udid: str = "") -> bool:
    expected = _expected_version_code()
    if expected <= 0:
        return False
    if not assistant_installed_on_device(udid):
        return True
    return get_device_assistant_version_code(udid) < expected


def no_device_hint() -> str:
    return (
        "当前没有已连接的 Android 设备或模拟器。"
        "插件安装包已可在本机准备；请启动模拟器、USB 连接真机或完成无线调试配对后，"
        "在「移动端测试」页点击「安装插件」手动推送助手（连接设备不会自动安装）。"
    )


def assistant_installed_on_device(udid: str = "") -> bool:
    udid = (udid or resolve_target_udid_for_push() or "").strip()
    if not udid:
        return False
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


def prepare_testory_assistant(*, progress_cb=None) -> Dict[str, Any]:
    """阶段一：将 APK 准备到本机插件目录（无需连接设备）。"""
    def _step(percent: int, label: str) -> None:
        if progress_cb:
            try:
                progress_cb(int(percent), label)
            except TypeError:
                try:
                    progress_cb(label)
                except Exception:
                    pass
            except Exception:
                pass

    src = resolve_assistant_apk_path()
    if src is None:
        return {
            "success": False,
            "error": "未找到助手 APK。请从插件包目录放置 testory-assistant.apk，或编译 mobile_assistant_apk_v2 工程。",
            "plugin_id": _PLUGIN_ID,
        }
    dest_dir = assistant_staging_dir()
    dest = dest_dir / _STAGED_APK_NAME
    _step(20, "正在准备助手安装包…")
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        return {"success": False, "error": f"复制 APK 失败：{exc}", "plugin_id": _PLUGIN_ID}

    _step(100, "助手安装包已就绪")
    udid = resolve_target_udid_for_push()
    on_device = assistant_installed_on_device(udid) if udid else False
    needs_upgrade = device_assistant_needs_upgrade(udid) if udid else False
    return {
        "success": True,
        "plugin_id": _PLUGIN_ID,
        "package": _PACKAGE,
        "apk_path": str(dest),
        "staged_only": True,
        "device_push_pending": bool(udid and (not on_device or needs_upgrade)),
        "assistant_on_device": on_device,
        "assistant_needs_upgrade": needs_upgrade,
        "connected_udid": udid or None,
        "message": (
            "助手安装包已准备完成。"
            + (
                "检测到已连接设备，请在「移动端测试」页点击「安装插件」手动推送。"
                if udid and (not on_device or needs_upgrade)
                else no_device_hint()
                if not udid
                else "设备上已是最新助手，无需重复安装。"
            )
        ),
    }


def push_testory_assistant_to_device(
    udid: str = "",
    *,
    progress_cb=None,
    force_reinstall: bool = False,
    launch_app: bool = False,
    _from_authorized_install: bool = False,
) -> Dict[str, Any]:
    """阶段二：将已准备的 APK 通过 adb 安装到设备。"""
    if not _from_authorized_install and not _device_install_allowed():
        logger.warning("blocked unauthorized assistant adb install (udid=%s)", udid or "")
        return {
            "success": False,
            "error": "连接设备不会自动安装助手。请在 PC 端点击「安装插件」。",
            "plugin_id": _PLUGIN_ID,
            "blocked_unauthorized_install": True,
        }
    def _step(percent: int, label: str) -> None:
        if progress_cb:
            try:
                progress_cb(int(percent), label)
            except TypeError:
                try:
                    progress_cb(label)
                except Exception:
                    pass
            except Exception:
                pass

    udid = (udid or resolve_target_udid_for_push() or "").strip()
    if not udid:
        return {
            "success": False,
            "error": no_device_hint(),
            "plugin_id": _PLUGIN_ID,
            "need_device": True,
        }

    apk = resolve_install_apk_path()
    if apk is None:
        return {
            "success": False,
            "error": "未找到助手 APK。请先在插件市场点击「安装」准备安装包。",
            "plugin_id": _PLUGIN_ID,
        }

    already = assistant_installed_on_device(udid)
    if already and not force_reinstall:
        _step(100, "设备上已安装助手")
        return {
            "success": True,
            "plugin_id": _PLUGIN_ID,
            "package": _PACKAGE,
            "udid": udid,
            "already_on_device": True,
            "message": "助手已在该设备上安装。请在手机开启无障碍服务。",
        }

    label = "正在升级助手…" if already else f"正在安装到设备 {udid}…"
    _step(15, label)
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
            if "no devices/emulators found" in err.lower():
                err = no_device_hint()
            return {
                "success": False,
                "error": err or "adb install 失败",
                "plugin_id": _PLUGIN_ID,
                "need_device": "no devices" in (err or "").lower(),
            }
    except Exception as exc:
        return {"success": False, "error": str(exc), "plugin_id": _PLUGIN_ID}

    _step(70, "配置 adb forward 隧道…")
    rev_ok, rev_msg = setup_adb_forward(udid, 17123)
    if launch_app:
        try:
            _step(85, "打开助手引导页…")
            subprocess.run(
                _adb_cmd(udid) + ["shell", "am", "start", "-n", f"{_PACKAGE}/com.testory.assistant.v2.MainActivity"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception:
            pass
    else:
        # 部分模拟器 adb install -r 后会自动拉起 App，强制回到后台
        try:
            subprocess.run(
                _adb_cmd(udid) + ["shell", "am", "force-stop", _PACKAGE],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            pass
    _step(100, "助手已安装到设备")
    manifest = _manifest()
    expected_ver = manifest.get("apk_version_name") or manifest.get("version") or "1.1.3"
    device_code = get_device_assistant_version_code(udid)
    return {
        "success": True,
        "plugin_id": _PLUGIN_ID,
        "package": _PACKAGE,
        "apk_path": str(apk),
        "udid": udid,
        "adb_forward": rev_ok,
        "adb_forward_message": rev_msg,
        "device_push_pending": False,
        "assistant_on_device": True,
        "reinstalled": bool(already),
        "expected_version": expected_ver,
        "assistant_version_on_device": device_code,
        "assistant_version_expected": _expected_version_code(),
        "message": (
            f"Testory 助手已{'升级' if already else '安装'}到设备（v{expected_ver}，versionCode={device_code}）。"
            + ("请打开 App 确认版本号，" if not launch_app else "")
            + "并在系统设置中重新开启无障碍服务。"
        ),
    }


def get_assistant_device_status(udid: str = "") -> Dict[str, Any]:
    """连接设备时仅检测版本，不触发 adb install。"""
    udid = (udid or resolve_target_udid_for_push() or "").strip()
    manifest = _manifest()
    expected_code = _expected_version_code()
    expected_name = str(manifest.get("apk_version_name") or manifest.get("version") or "")
    on_device = assistant_installed_on_device(udid) if udid else False
    device_code = get_device_assistant_version_code(udid) if on_device else 0
    bundled = resolve_assistant_apk_path()
    needs = (not on_device) or (expected_code > 0 and device_code < expected_code)
    return {
        "assistant_installed": on_device,
        "assistant_version_on_device": device_code,
        "assistant_version_expected": expected_code,
        "assistant_version_name_expected": expected_name,
        "assistant_needs_install": needs,
        "assistant_bundled_apk": str(bundled) if bundled else None,
    }


def maybe_auto_push_assistant(udid: str = "") -> Optional[Dict[str, Any]]:
    """已禁用：连接设备时不自动安装/升级助手，请用户在 Web 端点击「安装插件」。"""
    return None


def install_testory_assistant(
    udid: str = "",
    *,
    progress_cb=None,
    force_reinstall: bool = True,
    launch_app: bool = False,
) -> Dict[str, Any]:
    """
    插件市场 / 移动端「安装助手」统一入口：
    1. 确保本机 APK 已准备（从 config/plugin_bundles 复制最新包）
    2. 有设备则推送到手机（默认强制 adb install -r 覆盖旧版）
    """
    prep = prepare_testory_assistant(progress_cb=progress_cb)
    if not prep.get("success"):
        return prep

    target = (udid or resolve_target_udid_for_push() or "").strip()
    if not target:
        return prep

    with assistant_device_install_scope():
        pushed = push_testory_assistant_to_device(
            target,
            progress_cb=progress_cb,
            force_reinstall=force_reinstall,
            launch_app=launch_app,
            _from_authorized_install=True,
        )
    if pushed.get("success"):
        return pushed

    prep["push_error"] = pushed.get("error")
    prep["device_push_pending"] = True
    prep["message"] = (
        f"安装包已准备完成，但推送到设备失败：{pushed.get('error') or '未知错误'}。"
        "请连接设备后在移动端测试页重试「安装插件」。"
    )
    return prep


def get_testory_assistant_catalog_entry() -> Dict[str, Any]:
    manifest = _manifest()
    return {
        "id": _PLUGIN_ID,
        "name": manifest.get("name") or "Testory 移动端助手",
        "icon": "fas fa-mobile-screen-button",
        "icon_color": "#059669",
        "version": manifest.get("version") or "1.0.0",
        "type": "runtime_bundle",
        "description": manifest.get("description")
        or "准备录制插件 APK；连接手机/模拟器后自动或手动推送到设备。",
        "features": manifest.get("features")
        or ["本机免设备准备", "连接后自动推送", "无障碍录制", "关键帧截图"],
        "local_bundle_ready": resolve_assistant_apk_path() is not None,
        "requires_device_for_full_install": False,
    }

