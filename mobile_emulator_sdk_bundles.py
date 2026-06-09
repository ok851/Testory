# -*- coding: utf-8 -*-
"""插件市场：Android 模拟器 SDK（命令行 tools + sdkmanager + 默认 AVD）。"""

from __future__ import annotations

import hashlib
import json
import os
import glob
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ProgressCallback = Optional[Callable[[int, str], None]]
import urllib.request
from urllib.error import URLError
from urllib.request import Request

_ROOT = Path(__file__).resolve().parent
_MANIFEST_PATH = _ROOT / "config" / "plugin_bundles" / "android_emulator_sdk.json"
_PLUGIN_ID = "mobile-android-emulator-sdk"


def _manifest() -> Dict[str, Any]:
    if not _MANIFEST_PATH.is_file():
        return {}
    try:
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _platform_key() -> str:
    s = sys.platform.lower()
    if s.startswith("win"):
        return "windows"
    if s.startswith("darwin"):
        return "darwin"
    return "linux"


def _platform_spec() -> Dict[str, Any]:
    manifest = _manifest()
    platforms = manifest.get("platforms") or {}
    spec = platforms.get(_platform_key()) or {}
    if not spec and _platform_key() != "windows":
        spec = platforms.get("windows") or {}
    return spec if isinstance(spec, dict) else {}


def _sanitize_env_value(raw: Optional[str]) -> str:
    s = (raw or "").strip()
    if not s or s.startswith("#"):
        return ""
    if " #" in s:
        s = s.split(" #", 1)[0].strip()
    if s.startswith("#"):
        return ""
    return s


def _is_http_url(value: str) -> bool:
    low = (value or "").strip().lower()
    return low.startswith("http://") or low.startswith("https://")


def android_sdk_install_dir() -> Path:
    from web_capture.plugin_market import software_extensions_root

    dest = software_extensions_root() / "android" / "sdk"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def get_installed_emulator_sdk_home() -> Optional[str]:
    """已安装且含 emulator 的 SDK 根目录。"""
    root = android_sdk_install_dir()
    if _sdk_has_emulator(root):
        return str(root.resolve())
    return None


def default_avd_name() -> str:
    return str((_manifest().get("default_avd") or {}).get("name") or "Testory_Pixel7").strip()


def emulator_sdk_setup_status() -> Dict[str, Any]:
    """区分「SDK 组件在磁盘」与「默认可启动的虚拟手机已创建」。"""
    sdk_root = android_sdk_install_dir()
    sdk_home = get_installed_emulator_sdk_home()
    avd = default_avd_name()
    sdk_ready = bool(sdk_home)
    img_ready = _system_image_ready(sdk_root) if sdk_ready else False
    avd_ready = _avd_exists(avd) if avd else False
    return {
        "sdk_ready": sdk_ready,
        "system_image_ready": img_ready,
        "avd_ready": avd_ready,
        "default_avd": avd,
        "setup_complete": bool(sdk_ready and img_ready and avd_ready),
    }


def resolve_adb_in_sdk(sdk_root: Optional[Path] = None) -> Optional[str]:
    root = sdk_root or android_sdk_install_dir()
    if not root.is_dir():
        return None
    binary = "adb.exe" if _platform_key() == "windows" else "adb"
    direct = root / "platform-tools" / binary
    if direct.is_file():
        return str(direct.resolve())
    for hit in root.rglob(binary):
        if hit.is_file() and "platform-tools" in hit.parts:
            return str(hit.resolve())
    return None


def _sdk_has_emulator(sdk_root: Path) -> bool:
    if not sdk_root.is_dir():
        return False
    name = "emulator.exe" if _platform_key() == "windows" else "emulator"
    return (sdk_root / "emulator" / name).is_file()


def _sdkmanager_path(sdk_root: Path) -> Optional[Path]:
    for sub in (
        "cmdline-tools/latest/bin/sdkmanager.bat",
        "cmdline-tools/latest/bin/sdkmanager",
    ):
        cand = sdk_root / sub
        if cand.is_file():
            return cand
    for hit in sdk_root.rglob("sdkmanager.bat" if _platform_key() == "windows" else "sdkmanager"):
        if hit.is_file() and "cmdline-tools" in hit.parts:
            return hit
    return None


def _avdmanager_path(sdk_root: Path) -> Optional[Path]:
    for sub in (
        "cmdline-tools/latest/bin/avdmanager.bat",
        "cmdline-tools/latest/bin/avdmanager",
    ):
        cand = sdk_root / sub
        if cand.is_file():
            return cand
    for hit in sdk_root.rglob("avdmanager.bat" if _platform_key() == "windows" else "avdmanager"):
        if hit.is_file() and "cmdline-tools" in hit.parts:
            return hit
    return None


def _bundled_java_candidates() -> List[Path]:
    """发布包内置 JRE（与主程序同目录 runtime/jre 或 jre）。"""
    roots: List[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(_ROOT)
    names = ("java.exe", "java") if _platform_key() == "windows" else ("java",)
    out: List[Path] = []
    for root in roots:
        for sub in ("runtime/jre", "jre", "runtime/java"):
            for name in names:
                cand = root / sub / "bin" / name
                if cand.is_file():
                    out.append(cand)
    return out


def _resolve_java_exe() -> Optional[str]:
    for cand in _bundled_java_candidates():
        return str(cand.resolve())
    home = _sanitize_env_value(os.environ.get("JAVA_HOME"))
    if home:
        for name in ("java.exe", "java"):
            cand = Path(home) / "bin" / name
            if cand.is_file():
                return str(cand)
    found = shutil.which("java")
    if found:
        return found
    if _platform_key() == "windows":
        for pattern in (
            r"C:\Program Files\Java\*\bin\java.exe",
            r"C:\Program Files\Eclipse Adoptium\*\bin\java.exe",
            r"C:\Program Files\Microsoft\*\bin\java.exe",
        ):
            hits = glob.glob(pattern)
            if hits:
                return hits[0]
    return None


def _java_required_message() -> str:
    return (
        "未检测到 Java 运行环境（需要 Java 11 或更高版本）。\n"
        "请安装 Java 后重新打开本软件再试；若您使用的是完整安装包仍提示此项，请联系软件供应商。"
    )


def _offline_bundle_dirs() -> List[Path]:
    """面向发布版：安装目录下的离线插件文件夹。"""
    dirs: List[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        dirs.extend([exe_dir / "offline_plugins", exe_dir / "plugin_bundles"])
    dirs.extend(
        [
            _ROOT / "plugin_bundles",
            _ROOT / "offline_plugins",
            _ROOT / "config" / "plugin_bundles",
        ]
    )
    return dirs


def _offline_bundle_hint() -> str:
    parts = [str(d) for d in _offline_bundle_dirs()[:3]]
    return "、".join(parts) if parts else "软件安装目录下的 offline_plugins 文件夹"


def _resolve_local_zip() -> Optional[Path]:
    env_local = _sanitize_env_value(os.environ.get("ANDROID_CMDLINE_TOOLS_LOCAL_ZIP"))
    if env_local:
        p = Path(env_local)
        if p.is_file():
            return p.resolve()
    spec = _platform_spec()
    filename = spec.get("filename") or f"commandlinetools-{_platform_key()}-latest.zip"
    for root in _offline_bundle_dirs():
        candidate = (root / filename).resolve()
        if candidate.is_file():
            return candidate
    manifest = _manifest()
    patterns = manifest.get("local_bundle_search") or []
    for pattern in patterns:
        rel = str(pattern).format(filename=filename)
        candidate = (_ROOT / rel).resolve()
        if candidate.is_file():
            return candidate
    return None


def _collect_download_urls() -> List[str]:
    seen: set = set()
    out: List[str] = []

    def add(raw: Optional[str]) -> None:
        u = (raw or "").strip()
        if not _is_http_url(u):
            u = _sanitize_env_value(raw)
        if not _is_http_url(u) or u in seen:
            return
        seen.add(u)
        out.append(u)

    spec = _platform_spec()
    mirrors = spec.get("mirror_urls")
    if isinstance(mirrors, list):
        for item in mirrors:
            add(str(item) if item else None)
    add(os.environ.get("ANDROID_CMDLINE_TOOLS_URL"))
    add(spec.get("url"))
    return out


def _sdk_repo_mirror_base() -> str:
    """sdkmanager 使用的仓库镜像根地址（国内默认阿里云）。"""
    custom = _sanitize_env_value(os.environ.get("ANDROID_SDK_REPO_MIRROR"))
    if custom:
        return custom.rstrip("/") + "/"
    manifest = _manifest()
    base = (manifest.get("sdk_repo_mirror") or "").strip()
    if base:
        return base.rstrip("/") + "/"
    return "https://mirrors.aliyun.com/android/repository/"


def _write_sdk_mirror_config(sdk_root: Path) -> str:
    """写入 sdkmanager 镜像配置（SDK 目录 + 用户 .android）。"""
    base = _sdk_repo_mirror_base()
    block = f"### User Sources for Android SDK Manager\n{base}\n"
    for cfg in (sdk_root / ".android" / "repositories.cfg",):
        try:
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(block, encoding="utf-8")
        except OSError:
            pass
    user_android = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    if user_android:
        user_cfg = Path(user_android) / ".android" / "repositories.cfg"
        try:
            user_cfg.parent.mkdir(parents=True, exist_ok=True)
            user_cfg.write_text(block, encoding="utf-8")
        except OSError:
            pass
    return base


def _offline_install_message(errors: Optional[List[str]] = None) -> str:
    spec = _platform_spec()
    fname = spec.get("filename") or f"commandlinetools-{_platform_key()}-latest.zip"
    hint = (
        "无法从网络下载安装包。请检查网络或代理后重试，或任选其一：\n"
        f"1) 联系管理员配置离线包：将官方 zip 重命名为 {fname}，放入 {_offline_bundle_hint()}\n"
        "2) 在已安装 Android Studio 的电脑上使用「移动端测试」（无需本插件）"
    )
    if errors:
        hint += "\n详情：" + "; ".join(_ascii_snippet(e) for e in errors[:2])
    return hint


def _ascii_snippet(text: str, limit: int = 120) -> str:
    """错误详情仅保留 ASCII，避免界面编码问题。"""
    raw = (text or "")[:limit]
    return raw.encode("ascii", errors="replace").decode("ascii")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_sha256(path: Path, expected: str) -> None:
    exp = (expected or "").strip().lower()
    if not exp:
        return
    got = _sha256_file(path)
    if got != exp:
        raise RuntimeError(f"安装包校验失败：SHA256 不匹配（期望 {exp[:12]}…，实际 {got[:12]}…）")


def _download_url(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = (url or "").strip()
    try:
        url.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RuntimeError("下载地址无效，请联系管理员检查网络或离线安装配置。") from exc
    req = Request(
        url,
        headers={"User-Agent": "Testory-Emulator-SDK-Installer/1.0", "Accept": "*/*"},
    )
    # 不使用系统代理，避免代理认证信息含中文时 urllib 用 latin-1 编 header 失败
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=900) as resp:
        data = resp.read()
    if len(data) < 4096:
        raise RuntimeError("下载内容过小，可能不是有效的 zip 包")
    dest.write_bytes(data)


def _download_with_fallback(urls: List[str], dest: Path) -> str:
    errors: List[str] = []
    for url in urls:
        try:
            _download_url(url, dest)
            return url
        except (UnicodeEncodeError, URLError, OSError, RuntimeError, TimeoutError) as exc:
            errors.append(f"{_ascii_snippet(url, 64)} -> {_ascii_snippet(str(exc))}")
    raise RuntimeError(_offline_install_message(errors))


def _subprocess_env(sdk_root: Path, java_exe: Optional[str] = None) -> Dict[str, str]:
    """精简环境变量，降低 Windows 下非 ASCII 系统变量导致子进程失败的风险。"""
    sysroot = os.environ.get("SYSTEMROOT") or os.environ.get("SystemRoot") or r"C:\Windows"
    path_parts: List[str] = []
    sys32 = os.path.join(sysroot, "System32")
    if sys32 not in path_parts:
        path_parts.append(sys32)
    for part in (os.environ.get("PATH") or "").split(os.pathsep):
        part = part.strip()
        if not part or part in path_parts:
            continue
        path_parts.append(part)
    if java_exe:
        java_bin = str(Path(java_exe).resolve().parent)
        if java_bin not in path_parts:
            path_parts.insert(0, java_bin)
    env: Dict[str, str] = {
        "SYSTEMROOT": sysroot,
        "SystemRoot": sysroot,
        "PATH": os.pathsep.join(path_parts),
        "TEMP": os.environ.get("TEMP") or tempfile.gettempdir(),
        "TMP": os.environ.get("TMP") or tempfile.gettempdir(),
        "ANDROID_SDK_ROOT": str(sdk_root),
        "ANDROID_HOME": str(sdk_root),
    }
    if java_exe:
        env["JAVA_HOME"] = str(Path(java_exe).resolve().parent.parent)
    mirror = _write_sdk_mirror_config(sdk_root)
    env["ANDROID_SDK_REPOSITORY_URL"] = mirror
    return env


def _run_subprocess(cmd: List[str], sdk_root: Path, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    java = _resolve_java_exe()
    env = _subprocess_env(sdk_root, java)
    run_kw: Dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
        "env": env,
        "cwd": str(sdk_root),
    }
    run_kw.update(kwargs)
    if _platform_key() == "windows":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, **run_kw)


def _install_cmdline_tools_zip(zip_path: Path, sdk_root: Path) -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="testory_cmdline_extract_"))
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_root)
        mgr_name = "sdkmanager.bat" if _platform_key() == "windows" else "sdkmanager"
        hits = list(tmp_root.rglob(mgr_name))
        if not hits:
            raise RuntimeError(f"zip 中未找到 {mgr_name}，请确认是官方 commandlinetools 包")
        src_tree = hits[0].parent.parent
        if src_tree.name != "cmdline-tools" and (src_tree.parent / "cmdline-tools").is_dir():
            src_tree = src_tree.parent / "cmdline-tools"
        if src_tree.name == "cmdline-tools":
            inner = src_tree
        else:
            inner = src_tree
        dest_latest = sdk_root / "cmdline-tools" / "latest"
        if dest_latest.exists():
            shutil.rmtree(dest_latest, ignore_errors=True)
        dest_latest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(inner, dest_latest)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    if not _sdkmanager_path(sdk_root):
        raise RuntimeError("命令行工具解压后未找到 sdkmanager")


def _avdmanager_cmd(avd_mgr: Path, *args: str) -> List[str]:
    """创建 AVD：依赖 ANDROID_SDK_ROOT 环境变量，勿传 --sdk_root（新版 avdmanager 已不支持）。"""
    return [str(avd_mgr), "create", "avd", *args]


def _run_sdk_tool(
    exe: Path,
    sdk_root: Path,
    args: List[str],
    *,
    timeout: int = 3600,
    input_text: str = "",
) -> Tuple[int, str, str]:
    cmd = [str(exe), f"--sdk_root={sdk_root}"] + args
    proc = _run_subprocess(
        cmd,
        sdk_root,
        timeout=timeout,
        input=input_text or None,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out, proc.stderr or ""


def _accept_licenses(sdk_root: Path, sdkmanager: Path) -> None:
    yes = "\n".join(["y"] * 40) + "\n"
    code, out, _ = _run_sdk_tool(
        sdkmanager,
        sdk_root,
        ["--licenses"],
        timeout=300,
        input_text=yes,
    )
    if code != 0 and "license" not in out.lower():
        raise RuntimeError(f"接受 SDK 许可失败：{out[-800:]}")


def _parse_sdkmanager_percent(output: str) -> Optional[int]:
    hits = re.findall(r"(\d+)%", output or "")
    if not hits:
        return None
    try:
        return max(0, min(100, int(hits[-1])))
    except ValueError:
        return None


def _sdk_download_failed_message(package: str) -> str:
    return (
        f"下载组件「{package}」失败（网络不稳定、被防火墙拦截或无法访问 Google 服务器）。\n"
        "请检查网络或关闭代理后重试；若在内网环境，请联系管理员提供离线 Android SDK 安装包。"
    )


def _install_sdk_packages(
    sdk_root: Path,
    sdkmanager: Path,
    packages: List[str],
    *,
    progress_cb: ProgressCallback = None,
) -> None:
    if not packages:
        return
    base_pct, span = 45, 38
    n = len(packages)
    for idx, pkg in enumerate(packages):
        label = f"正在安装 {pkg}（{idx + 1}/{n}）…"
        if progress_cb:
            progress_cb(base_pct + int(span * idx / max(1, n)), label)
        last_out = ""
        for attempt in range(3):
            code, out, _ = _run_sdk_tool(
                sdkmanager,
                sdk_root,
                ["--verbose", "--install", pkg],
                timeout=7200,
            )
            last_out = out
            sub_pct = _parse_sdkmanager_percent(out)
            if progress_cb and sub_pct is not None:
                slot = span / max(1, n)
                progress_cb(
                    int(base_pct + slot * idx + slot * sub_pct / 100),
                    f"{label} {sub_pct}%",
                )
            if code == 0:
                break
            if attempt < 2:
                if progress_cb:
                    progress_cb(
                        int(base_pct + span * idx / max(1, n)),
                        f"{label} 重试 ({attempt + 2}/3)…",
                    )
                continue
            if "Failed to download" in out or "failed to download" in out.lower():
                raise RuntimeError(_sdk_download_failed_message(pkg))
            raise RuntimeError(
                f"安装组件「{pkg}」失败。{_ascii_snippet(last_out, 400)}"
            )


def _default_system_image_package() -> str:
    avd_cfg = _manifest().get("default_avd") or {}
    return (avd_cfg.get("system_image") or "system-images;android-34;google_apis;x86_64").strip()


def _system_image_dir(sdk_root: Path, image_pkg: Optional[str] = None) -> Path:
    pkg = (image_pkg or _default_system_image_package()).strip()
    rel = pkg.replace("system-images;", "").split(";")
    if len(rel) >= 3:
        return sdk_root / "system-images" / rel[0] / rel[1] / rel[2]
    return sdk_root / "system-images"


def _system_image_ready(sdk_root: Path, image_pkg: Optional[str] = None) -> bool:
    """系统镜像是否已完整下载（仅有 .installer 目录视为未完成）。"""
    img_dir = _system_image_dir(sdk_root, image_pkg)
    if not img_dir.is_dir():
        return False
    markers = ("package.xml", "build.prop", "kernel-ranchu", "system.img", "encryptionkey.img")
    for name in markers:
        if (img_dir / name).is_file():
            return True
        if any(img_dir.rglob(name)):
            return True
    usable = [p for p in img_dir.iterdir() if p.name != ".installer"]
    return len(usable) > 0 and any(p.is_file() for p in img_dir.rglob("*") if p.name != ".installData")


def _missing_sdk_packages(sdk_root: Path) -> List[str]:
    """返回仍需 sdkmanager 安装的组件 ID 列表。"""
    manifest = _manifest()
    required = manifest.get("sdk_packages") or [
        "platform-tools",
        "platforms;android-34",
        "system-images;android-34;google_apis;x86_64",
        "emulator",
    ]
    missing: List[str] = []
    if not resolve_adb_in_sdk(sdk_root):
        if "platform-tools" in required:
            missing.append("platform-tools")
    if not _sdk_has_emulator(sdk_root) and "emulator" in required:
        missing.append("emulator")
    if not _system_image_ready(sdk_root):
        img = _default_system_image_package()
        if img in required:
            missing.append(img)
        elif img not in missing:
            missing.append(img)
    if _platform_key() == "windows":
        hv_pkg = "extras;google;Android_Emulator_Hypervisor_Driver"
        hv_dir = sdk_root / "extras" / "google" / "Android_Emulator_Hypervisor_Driver"
        if hv_pkg in required or hv_pkg in (manifest.get("sdk_packages") or []):
            if not (hv_dir / "silent_install.bat").is_file() and hv_pkg not in missing:
                missing.append(hv_pkg)
    plat_dir = sdk_root / "platforms" / "android-34"
    if "platforms;android-34" in required and not plat_dir.is_dir():
        missing.append("platforms;android-34")
    return missing


def _list_avd_names() -> List[str]:
    from mobile_emulator_manager import list_avds

    return [a.get("name") or "" for a in list_avds() if a.get("name")]


def ensure_emulator_sdk_ready(
    *,
    progress_cb: ProgressCallback = None,
) -> Dict[str, Any]:
    """
    补全 SDK 组件并创建默认 AVD（处理「仅有 emulator、无镜像/无 AVD」的半成品安装）。
    """
    sdk_root = android_sdk_install_dir()
    progress: List[Dict[str, Any]] = []

    def _step(percent: int, label: str) -> None:
        progress.append({"label": label, "percent": percent})
        if progress_cb:
            progress_cb(percent, label)

    if not _resolve_java_exe():
        return {"success": False, "error": _java_required_message(), "progress": progress}

    if not _sdkmanager_path(sdk_root) and not _sdk_has_emulator(sdk_root):
        return install_android_emulator_sdk(progress_cb=progress_cb)

    missing = _missing_sdk_packages(sdk_root)
    avd_cfg = _manifest().get("default_avd") or {}
    avd_name = (avd_cfg.get("name") or "Testory_Pixel7").strip()

    try:
        if missing:
            sdkmanager = _sdkmanager_path(sdk_root)
            if not sdkmanager:
                return {
                    "success": False,
                    "error": "未找到 sdkmanager，请在插件市场重新安装「Android 模拟器 SDK」。",
                    "progress": progress,
                }
            _step(20, "正在安装 SDK 组件（系统镜像等）…")
            _accept_licenses(sdk_root, sdkmanager)
            _install_sdk_packages(sdk_root, sdkmanager, missing, progress_cb=progress_cb)

        if not _system_image_ready(sdk_root):
            return {
                "success": False,
                "error": (
                    "系统镜像仍未就绪（可能下载失败或被网络拦截）。"
                    "请检查网络/代理后，在插件市场重新点「安装」，或联系管理员提供离线 SDK。"
                ),
                "progress": progress,
                "missing_packages": missing,
            }

        names = _list_avd_names()
        if avd_name not in names:
            _step(88, "正在创建默认虚拟手机…")
            avd_name = _create_default_avd(sdk_root, avd_cfg if isinstance(avd_cfg, dict) else {})

        adb_exe = resolve_adb_in_sdk(sdk_root) or ""
        if adb_exe:
            _apply_sdk_config(sdk_root, adb_exe, avd_name)
        _register_plugin_installed(sdk_root, adb_exe, avd_name, source="repair")

        names = _list_avd_names()
        _step(100, "环境就绪")

        msg = f"已就绪。默认虚拟手机：{avd_name}。"
        if missing:
            msg = f"已安装 SDK 组件并创建虚拟手机「{avd_name}」。"
        return {
            "success": True,
            "plugin_id": _PLUGIN_ID,
            "installed": True,
            "install_dir": str(sdk_root),
            "android_sdk_home": str(sdk_root),
            "adb_path": adb_exe,
            "default_avd": avd_name,
            "avds": names,
            "repaired_packages": missing,
            "message": msg + " 请选择 AVD 后点击「启动模拟器」。",
            "progress": progress or [{"label": "完成", "percent": 100}],
        }
    except Exception as exc:
        msg = str(exc)
        if "Failed to download" in msg or "failed to download" in msg.lower():
            msg = _sdk_download_failed_message(_default_system_image_package())
        return {"success": False, "error": msg, "progress": progress}


def _avd_exists(avd_name: str) -> bool:
    home = os.environ.get("ANDROID_AVD_HOME") or os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if not home:
        return False
    ini = Path(home) / ".android" / "avd" / f"{avd_name}.ini"
    return ini.is_file()


def _tool_error_snippet(output: str, *, max_len: int = 400) -> str:
    text = (output or "").strip()
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("error:"):
            return s[:max_len]
    return _ascii_snippet(text, max_len)


def create_avd_for_preset(preset: Dict[str, Any]) -> str:
    """按 EMULATOR_AVD_PRESETS 条目创建 AVD（已存在则跳过）。"""
    avd_cfg = {
        "name": (preset.get("avd_name_hint") or "Testory_Device").strip(),
        "system_image": (preset.get("system_image") or "system-images;android-34;google_apis;x86_64").strip(),
        "device_id": (preset.get("device_id") or "pixel_6").strip(),
    }
    sdk_root = android_sdk_install_dir()
    if not _sdk_has_emulator(sdk_root):
        raise RuntimeError("Android 模拟器 SDK 未安装，请先在插件市场安装。")
    if not _system_image_ready(sdk_root):
        raise RuntimeError("系统镜像未就绪，请在插件市场点击「创建虚拟手机」或重新安装 SDK。")
    return _create_default_avd(sdk_root, avd_cfg)


def _create_default_avd(sdk_root: Path, avd_cfg: Dict[str, Any]) -> str:
    name = (avd_cfg.get("name") or "Testory_Pixel7").strip()
    if _avd_exists(name):
        return name
    avd_mgr = _avdmanager_path(sdk_root)
    if not avd_mgr:
        raise RuntimeError("未找到 avdmanager，无法创建默认 AVD")
    img = (avd_cfg.get("system_image") or "system-images;android-34;google_apis;x86_64").strip()
    dev = (avd_cfg.get("device_id") or "pixel_7").strip()
    cmd = _avdmanager_cmd(
        avd_mgr,
        "-n",
        name,
        "-k",
        img,
        "-d",
        dev,
        "--force",
    )
    proc = _run_subprocess(cmd, sdk_root, timeout=120, input="no\n")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 and not _avd_exists(name):
        raise RuntimeError(
            f"创建 AVD「{name}」失败：{_tool_error_snippet(out)}"
        )
    return name


def _register_plugin_installed(
    sdk_root: Path,
    adb_exe: str,
    avd_name: str,
    *,
    source: str = "install",
) -> None:
    """将模拟器 SDK 登记到插件市场状态（与磁盘检测一致）。"""
    from web_capture.plugin_market import _load_state, _save_state

    state = _load_state()
    plugins = state.setdefault("plugins", {})
    now = datetime.now(timezone.utc).isoformat()
    manifest = _manifest()
    plugins[_PLUGIN_ID] = {
        "plugin_id": _PLUGIN_ID,
        "type": "runtime_bundle",
        "version": manifest.get("version") or "1.0.0",
        "install_dir": str(sdk_root),
        "android_sdk_home": str(sdk_root.resolve()),
        "adb_path": adb_exe,
        "default_avd": avd_name,
        "installed_at": now,
        "source": source,
    }
    _save_state(state)


def _apply_sdk_config(sdk_root: Path, adb_exe: str, avd_name: str) -> None:
    sdk_str = str(sdk_root.resolve())
    os.environ["ANDROID_HOME"] = sdk_str
    os.environ["ANDROID_SDK_ROOT"] = sdk_str
    os.environ["ADB_PATH"] = adb_exe
    try:
        from mobile_env_config import save_mobile_defaults

        save_mobile_defaults(
            {
                "android_sdk_home": sdk_str,
                "adb_path": adb_exe,
                "emulator_avd": avd_name,
            }
        )
    except Exception:
        pass


def get_android_emulator_sdk_catalog_entry() -> Dict[str, Any]:
    manifest = _manifest()
    spec = _platform_spec()
    local = _resolve_local_zip()
    urls = _collect_download_urls()
    installed = get_installed_emulator_sdk_home()
    java = _resolve_java_exe()
    bundled_java = bool(_bundled_java_candidates())
    return {
        "id": _PLUGIN_ID,
        "category": "mobile",
        "name": manifest.get("name") or "Android 模拟器 SDK（命令行）",
        "browser": "any",
        "browser_label": "移动端",
        "icon": "fas fa-mobile-alt",
        "icon_color": "#3DDC84",
        "version": manifest.get("version") or "1.0.0",
        "type": "runtime_bundle",
        "description": manifest.get("description")
        or "一键安装 Android 模拟器与默认虚拟手机，安装后可直接在「移动端测试」中启动。",
        "features": [
            "Android 模拟器",
            "默认虚拟手机 Testory_Pixel7",
            "自动环境配置",
            "含手机调试连接",
        ],
        "download_source": (
            "local" if local else ("installed" if installed else ("url" if urls else "none"))
        ),
        "local_bundle_ready": bool(local),
        "download_url_configured": bool(urls),
        "java_ready": bool(java),
        "java_bundled": bundled_java,
        "java_path": java or "",
        "emulator_sdk_installed": bool(installed),
        "android_sdk_home": installed or "",
        "size_mb_hint": spec.get("size_mb_hint"),
        "license": manifest.get("license"),
    }


def install_android_emulator_sdk(
    *,
    progress_cb: ProgressCallback = None,
) -> Dict[str, Any]:
    """下载 cmdline-tools，经 sdkmanager 安装 emulator 组件并创建默认 AVD。"""
    sdk_root = android_sdk_install_dir()
    progress: List[Dict[str, Any]] = []
    tmp_zip: Optional[Path] = None

    def _step(percent: int, label: str) -> None:
        progress.append({"label": label, "percent": percent})
        if progress_cb:
            progress_cb(percent, label)

    if not _resolve_java_exe():
        return {"success": False, "error": _java_required_message()}

    if _sdk_has_emulator(sdk_root):
        return ensure_emulator_sdk_ready(progress_cb=progress_cb)

    try:
        local = _resolve_local_zip()
        source_kind = "download"
        manifest = _manifest()
        packages = manifest.get("sdk_packages") or [
            "platform-tools",
            "emulator",
            "platforms;android-34",
            "system-images;android-34;google_apis;x86_64",
        ]
        avd_cfg = manifest.get("default_avd") or {}

        if not _sdkmanager_path(sdk_root):
            if local:
                _step(10, "使用本地安装包…")
                zip_path = local
                _verify_sha256(zip_path, str(_platform_spec().get("sha256") or ""))
            else:
                urls = _collect_download_urls()
                if not urls:
                    return {"success": False, "error": _offline_install_message()}
                _step(5, "正在下载命令行工具…")
                fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="testory_cmdline_")
                os.close(fd)
                tmp_zip = Path(tmp_name)
                used = _download_with_fallback(urls, tmp_zip)
                _step(15, "命令行工具下载完成")
                zip_path = tmp_zip
                _verify_sha256(zip_path, str(_platform_spec().get("sha256") or ""))
                source_kind = "download"
            _step(25, "解压命令行工具…")
            _install_cmdline_tools_zip(zip_path, sdk_root)

        sdkmanager = _sdkmanager_path(sdk_root)
        if not sdkmanager:
            return {"success": False, "error": "未找到 sdkmanager"}

        _step(35, "接受许可协议…")
        _accept_licenses(sdk_root, sdkmanager)

        _step(45, "正在下载并安装模拟器组件（较久，请勿关闭软件）…")
        _install_sdk_packages(sdk_root, sdkmanager, list(packages), progress_cb=progress_cb)

        if not _sdk_has_emulator(sdk_root):
            return {
                "success": False,
                "error": "组件安装完成但未找到 emulator，请检查网络或重试安装。",
            }

        _step(85, "创建默认虚拟手机…")
        avd_name = _create_default_avd(sdk_root, avd_cfg if isinstance(avd_cfg, dict) else {})

        adb_exe = resolve_adb_in_sdk(sdk_root)
        if not adb_exe:
            return {"success": False, "error": "未找到 adb，platform-tools 安装可能失败。"}

        _apply_sdk_config(sdk_root, adb_exe, avd_name)
        _step(95, "保存环境配置…")
        _register_plugin_installed(
            sdk_root,
            adb_exe,
            avd_name,
            source=source_kind if not local else "local",
        )
        _step(100, "安装完成")

        setup = emulator_sdk_setup_status()
        hypervisor_hint = ""
        try:
            from mobile_emulator_manager import emulator_status

            st = emulator_status()
            if st.get("hypervisor_ok") is False and st.get("setup_hint"):
                hypervisor_hint = str(st.get("setup_hint") or "")
        except Exception:
            pass
        msg = (
            f"安装完成。默认虚拟手机：{avd_name}。"
            "请完全退出并重新打开本软件，进入「移动端测试」选择设备型号并启动。"
        )
        if hypervisor_hint:
            msg += f"\n\n注意：{hypervisor_hint}"

        return {
            "success": True,
            "plugin_id": _PLUGIN_ID,
            "installed": True,
            "install_dir": str(sdk_root),
            "android_sdk_home": str(sdk_root),
            "adb_path": adb_exe,
            "default_avd": avd_name,
            "avd_ready": bool(setup.get("avd_ready")),
            "hypervisor_ok": not bool(hypervisor_hint),
            "hypervisor_hint": hypervisor_hint or None,
            "progress": progress,
            "message": msg,
        }
    except UnicodeEncodeError:
        return {
            "success": False,
            "error": (
                "安装过程编码异常（常见于系统代理或用户名含特殊字符）。"
                "请关闭含中文地址的 HTTP 代理后重试，或联系管理员使用离线安装包。"
            ),
        }
    except Exception as exc:
        msg = str(exc)
        if "getaddrinfo failed" in msg or "urlopen error" in msg.lower():
            msg = _offline_install_message([msg])
        if "latin-1" in msg and "codec can't encode" in msg:
            msg = (
                "网络组件编码失败（多为系统代理配置导致）。"
                "请关闭代理后重试，或联系管理员配置离线安装包。"
            )
        return {"success": False, "error": msg}
    finally:
        if tmp_zip and tmp_zip.is_file():
            try:
                tmp_zip.unlink()
            except OSError:
                pass
