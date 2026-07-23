# -*- coding: utf-8 -*-
"""
Testory 桌面版入口（安装包 / 快捷方式应指向本文件）。

- 自动设置本地运行环境变量（用户无需配置 .env）
- 用户数据写入 %LOCALAPPDATA%\\Testory（Program Files 安装目录只读）
- 后台启动 Flask（无黑色命令行窗口）
- 用 pywebview 打开 Testory 独立软件窗口（不是系统浏览器）

依赖: pip install pywebview  （已写入 requirements-windows.txt）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 安装包通过 pythonw packaging\uat_desktop.py 启动时，需把安装根目录加入 sys.path
_install_root = Path(__file__).resolve().parent.parent
_root_str = str(_install_root)
if _root_str not in sys.path:
    sys.path.insert(0, _root_str)

import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional

from desktop_user_data import ensure_user_data_dirs, resolve_env_file
from packaging.desktop_shell import run_native_shell
from packaging.instance_lock import acquire_instance_lock, instance_lock_message
from packaging.launch_checks import run_launch_preflight
from packaging.testory_runtime import resolve_bundled_python, verify_bundled_python

APP_TITLE = "Testory"
DEFAULT_PORT = 5000

_PROTECTED_BACKEND_REL = "runtime/testory_app/TestoryBackend.exe"


def _backend_exe_path(root: Path) -> Optional[Path]:
    """保护版：runtime\\testory_app\\TestoryBackend.exe；不依赖根目录 install_paths.py。"""
    for rel in (_PROTECTED_BACKEND_REL, "runtime/testory_app/testory_backend.exe"):
        p = root / rel.replace("/", os.sep)
        if p.is_file():
            return p
    try:
        from install_paths import protected_backend_exe

        p = protected_backend_exe()
        if p is not None and p.is_file():
            return p
    except ImportError:
        pass
    return None


def install_root() -> Path:
    """安装目录或开发时的项目根目录（禁止误用 Path.cwd()）。"""
    here = Path(__file__).resolve().parent
    root = here.parent
    if (root / "app.py").is_file():
        return root
    if _backend_exe_path(root) is not None:
        return root
    if (here / "uat_desktop.py").is_file() and (root / "Testory.exe").is_file():
        return root
    try:
        from install_paths import resolve_install_root

        return resolve_install_root()
    except ImportError:
        pass
    return root


def resolve_python(root: Path) -> Path:
    """优先使用安装目录内的可移植 Python，保证用户机器无需单独装 Python。"""
    bundled = resolve_bundled_python(root)
    if bundled is not None:
        return bundled
    return Path(sys.executable)


def apply_local_env(root: Path, port: int) -> Path:
    """内置默认配置，覆盖「必须手改 .env」的场景。返回用户数据目录。"""
    os.environ.setdefault("DEPLOYMENT_PROFILE", "local")
    os.environ.setdefault("DESKTOP_EXECUTION_MODE", "inprocess")
    os.environ.setdefault("PLAYWRIGHT_HEADLESS", "0")
    os.environ.setdefault("DESKTOP_AUTO_START_GATEWAY", "0")
    os.environ.setdefault("FLASK_RUN_PORT", str(port))
    os.environ.setdefault("FLASK_RUN_HOST", "127.0.0.1")
    os.environ.setdefault("UAT_DESKTOP_MODE", "1")
    os.environ.setdefault("DEPLOYMENT_MODE", "client")
    os.environ.setdefault("DESKTOP_LAZY_GATEWAY_BOOT", "1")
    os.environ.setdefault("TESTORY_FRAMELESS_SHELL", "1")
    os.environ.setdefault("ENABLE_MOBILE", "1")
    os.environ.setdefault("MOBILE_EMULATOR_MODE", "1")
    os.environ.setdefault("MOBILE_AUTO_CONNECT", "1")
    # 启动页关键：导入期同步拉起 mobile 网关可卡数秒；用时再由 mobile_agent_client 拉起
    os.environ["MOBILE_AUTO_START_GATEWAY"] = "0"
    os.environ.setdefault("SKIP_ENV_EXAMPLE_SYNC", "1")
    os.environ["PYTHONNOUSERSITE"] = "1"
    root_str = str(root.resolve())
    existing = os.environ.get("PYTHONPATH", "").strip()
    if existing:
        parts = [p for p in existing.split(os.pathsep) if p]
        if root_str not in parts:
            os.environ["PYTHONPATH"] = root_str + os.pathsep + existing
    else:
        os.environ["PYTHONPATH"] = root_str

    user_data = ensure_user_data_dirs(root)
    os.environ["UAT_DATA_DIR"] = str(user_data)
    os.environ["DATABASE_PATH"] = str(user_data / "test_cases.db")

    bundled_browsers = root / "playwright-browsers"
    if bundled_browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)
        os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    os.environ.setdefault("EMBEDDED_BROWSER_GATEWAY_URL", "http://127.0.0.1:8765")
    os.environ.setdefault("EMBEDDED_BROWSER_GATEWAY_SECRET", "hufirst-desktop-local")
    os.environ.setdefault("EMBEDDED_BROWSER_PUBLIC_WS_BASE", "ws://127.0.0.1:8765")
    os.environ.setdefault("EMBEDDED_BROWSER_AUTO_START_GATEWAY", "1")
    os.environ.setdefault("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434")
    os.environ.setdefault("WEBSITE_URL", "https://www.hufirst.com")
    return user_data


def _patch_user_env_missing_keys(env_path: Path) -> None:
    """旧版用户 .env 可能缺少嵌入式网关等键，补全以免 AI/网页捕获不可用。"""
    defaults = {
        "EMBEDDED_BROWSER_GATEWAY_URL": "http://127.0.0.1:8765",
        "EMBEDDED_BROWSER_GATEWAY_SECRET": "hufirst-desktop-local",
        "EMBEDDED_BROWSER_PUBLIC_WS_BASE": "ws://127.0.0.1:8765",
        "EMBEDDED_BROWSER_AUTO_START_GATEWAY": "1",
        "DESKTOP_EXECUTION_MODE": "inprocess",
        "DEPLOYMENT_PROFILE": "local",
        "PLAYWRIGHT_HEADLESS": "0",
        "LOCAL_LLM_BASE_URL": "http://127.0.0.1:11434",
        "FLASK_RUN_HOST": "127.0.0.1",
        "DESKTOP_LAZY_GATEWAY_BOOT": "1",
        "ENABLE_MOBILE": "1",
        "MOBILE_EMULATOR_MODE": "1",
        "MOBILE_AUTO_CONNECT": "1",
        "WEBSITE_URL": "https://www.hufirst.com",
        "MOBILE_AUTO_START_GATEWAY": "0",
    }
    try:
        text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    except OSError:
        return
    if "MOBILE_AUTO_START_GATEWAY=1" in text or "MOBILE_AUTO_START_GATEWAY = 1" in text:
        try:
            import re

            text = re.sub(
                r"(?m)^MOBILE_AUTO_START_GATEWAY\s*=\s*.*$",
                "MOBILE_AUTO_START_GATEWAY=0",
                text,
            )
            env_path.write_text(text, encoding="utf-8")
        except OSError:
            pass
    lines: list[str] = []
    for key, val in defaults.items():
        if f"{key}=" in text or f"{key} =" in text:
            continue
        lines.append(f"{key}={val}")
    if not lines:
        return
    with env_path.open("a", encoding="utf-8") as fp:
        fp.write("\n# Testory desktop runtime defaults (auto)\n")
        fp.write("\n".join(lines))
        fp.write("\n")


def ensure_dotenv(root: Path, user_data: Path) -> Path:
    """在用户数据目录创建 .env（绝不写入只读的安装目录）。"""
    env_path = resolve_env_file(root)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["TESTORY_ENV_FILE"] = str(env_path)
    os.environ["UAT_DATA_DIR"] = str(user_data)
    if env_path.is_file():
        _patch_user_env_missing_keys(env_path)
        return env_path
    example = root / ".env.example"
    if example.is_file():
        shutil.copy(example, env_path)
        _patch_user_env_missing_keys(env_path)
        return env_path
    env_path.write_text(
        "# Auto-created by Testory desktop launcher\n"
        "DEPLOYMENT_PROFILE=local\n"
        "DESKTOP_EXECUTION_MODE=inprocess\n"
        "PLAYWRIGHT_HEADLESS=0\n"
        "DEPLOYMENT_MODE=client\n"
        "UAT_DESKTOP_MODE=1\n"
        "FLASK_RUN_HOST=127.0.0.1\n"
        "DESKTOP_LAZY_GATEWAY_BOOT=1\n"
        "ENABLE_MOBILE=1\n"
        "MOBILE_EMULATOR_MODE=1\n"
        "MOBILE_AUTO_CONNECT=1\n"
        "PLATFORM_ADMIN_URL=http://127.0.0.1:5100\n"
        "EMBEDDED_BROWSER_GATEWAY_URL=http://127.0.0.1:8765\n"
        "EMBEDDED_BROWSER_GATEWAY_SECRET=hufirst-desktop-local\n"
        "EMBEDDED_BROWSER_PUBLIC_WS_BASE=ws://127.0.0.1:8765\n"
        "EMBEDDED_BROWSER_AUTO_START_GATEWAY=1\n"
        "LOCAL_LLM_BASE_URL=http://127.0.0.1:11434\n"
        "WEBSITE_URL=https://www.hufirst.com\n",
        encoding="utf-8",
    )
    return env_path


def _no_window_flags() -> int:
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]


def _backend_command(root: Path, python: Path) -> list:
    """
    后端启动命令。
    安装目录若有 app.py，默认优先走源码（便于热更新注册/找回等 API）；
    纯保护包无 app.py 时仍用 TestoryBackend.exe。
    设置 TESTORY_PREFER_PROTECTED_BACKEND=1 可强制保护 exe。
    """
    force_protected = (os.environ.get("TESTORY_PREFER_PROTECTED_BACKEND") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    legacy = root / "app.py"
    if legacy.is_file() and not force_protected:
        return [str(python), str(legacy)]
    exe = _backend_exe_path(root)
    if exe is not None:
        return [str(exe)]
    return [str(python), str(root / "app.py")]


def start_backend(root: Path, python: Path, port: int, user_data: Path) -> subprocess.Popen:
    apply_local_env(root, port)
    os.environ["TESTORY_INSTALL_ROOT"] = str(root.resolve())
    env = os.environ.copy()
    log_dir = user_data / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "backend_startup.log"
    log_fp = open(log_path, "a", encoding="utf-8", buffering=1)
    log_fp.write(f"\n--- backend start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_fp.flush()
    cmd = _backend_command(root, python)
    return subprocess.Popen(
        cmd,
        cwd=str(root),
        env=env,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=_no_window_flags(),
    )


def _check_components_hint(root: Path, user_data: Path) -> None:
    """异步检查可选组件安装状态，写入标记文件供前端展示提示。"""
    try:
        import threading

        def _do_check():
            try:
                sys.path.insert(0, str(root))
                from components_manager import _check_chromium_installed

                if not _check_chromium_installed():
                    hint_file = user_data / "hints" / "missing_chromium.txt"
                    hint_file.parent.mkdir(parents=True, exist_ok=True)
                    hint_file.write_text(
                        "Web 自动化功能需要 Chromium 浏览器组件。\n"
                        "请前往「设置 → 可选组件」中安装。\n"
                        "或访问帮助页面了解详情：http://62.234.135.115/help/components",
                        encoding="utf-8",
                    )
            except Exception:
                pass

        threading.Thread(target=_do_check, daemon=True).start()
    except Exception:
        pass


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def _pids_listening_on_port(port: int) -> list[int]:
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    needle = f":{port} "
    pids: list[int] = []
    for line in out.splitlines():
        if "LISTENING" not in line.upper() or needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def _process_image_name(pid: int) -> str:
    if sys.platform != "win32" or pid <= 0:
        return ""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window_flags(),
        )
        # "TestoryBackend.exe","1234",...
        line = (out or "").strip().splitlines()[0] if out.strip() else ""
        if line.startswith('"'):
            return line.split('","')[0].strip('"')
        return line.split(",")[0].strip().strip('"')
    except (OSError, subprocess.CalledProcessError, IndexError):
        return ""


def _has_live_desktop_shell() -> bool:
    """是否仍有桌面壳进程（uat_desktop / pythonw 拉起的壳）。"""
    if sys.platform != "win32":
        return False
    # 带 where 过滤，避免全量 wmic 枚举进程（部分机器可达数秒）
    try:
        out = subprocess.check_output(
            [
                "wmic",
                "process",
                "where",
                "name='pythonw.exe' or name='python.exe' or name='Testory.exe'",
                "get",
                "CommandLine",
                "/FORMAT:LIST",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window_flags(),
            timeout=8,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    for line in out.splitlines():
        low = line.lower()
        if "uat_desktop.py" in low or "packaging\\uat_desktop" in low.replace("/", "\\"):
            return True
    return False


def _reclaim_orphaned_backend(port: int) -> bool:
    """
    壳进程异常退出后，TestoryBackend 常会残留并占住端口。
    若端口占用者是本产品后端且当前没有桌面壳，则自动结束后端以便重启。
    """
    if not _port_in_use("127.0.0.1", port):
        return False
    if _has_live_desktop_shell():
        return False
    reclaimed = False
    for pid in _pids_listening_on_port(port):
        name = _process_image_name(pid).lower()
        if name not in ("testorybackend.exe", "testory_backend.exe"):
            continue
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                creationflags=_no_window_flags(),
                timeout=8,
            )
            reclaimed = True
        except (OSError, subprocess.TimeoutExpired):
            continue
    if reclaimed:
        time.sleep(0.4)
    return reclaimed and not _port_in_use("127.0.0.1", port)


def wait_until_ready(port: int, proc: subprocess.Popen, timeout_sec: float = 120.0) -> bool:
    """等待 Flask 进程可响应（/api/health 即可，不依赖 DB 就绪探针）。"""
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout_sec
    # 前几秒更密地轮询，缩短启动页停留；失败后再略放慢，避免空转占 CPU
    attempt = 0
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        attempt += 1
        time.sleep(0.12 if attempt < 40 else 0.35)
    return False


def _show_error(msg: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, msg, APP_TITLE, 0x10)
            return
        except Exception:
            pass
    print(msg, file=sys.stderr)


def _log_error(root: Path, user_data: Path, exc: Exception) -> None:
    try:
        log_dir = user_data / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "desktop_launch.log"
        import traceback

        log_file.write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def _tail_backend_error(user_data: Path, max_lines: int = 40) -> str:
    log_file = user_data / "logs" / "backend_startup.log"
    if not log_file.is_file():
        return ""
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines:
        return ""
    # 优先截取最近一次启动段中的 Traceback
    start_idx = 0
    for i, line in enumerate(lines):
        if "backend start" in line.lower():
            start_idx = i
    chunk = lines[start_idx:]
    err_start = None
    for i, line in enumerate(chunk):
        if line.startswith("Traceback") or "Error]" in line or "ModuleNotFoundError" in line:
            err_start = i
            break
    if err_start is None:
        snippet = chunk[-min(max_lines, len(chunk)) :]
    else:
        snippet = chunk[err_start : err_start + max_lines]
    text = "\n".join(snippet).strip()
    if len(text) > 1800:
        text = text[:1800] + "\n…"
    return text


def _startup_failed_message(root: Path, user_data: Path, port: int, proc: Optional[subprocess.Popen]) -> str:
    lines = [
        "Testory 服务未能启动。",
        "",
        f"安装目录：{root}",
        f"用户数据：{user_data}",
        f"日志：{user_data / 'logs'}",
        "",
    ]
    if proc is not None and proc.poll() is not None:
        lines.append("后台进程已退出，请打开 backend_startup.log 查看原因。")
    else:
        lines.append(f"请检查 {port} 端口是否被占用。")
    detail = _tail_backend_error(user_data)
    if detail:
        lines.append("")
        lines.append("最近后端错误：")
        lines.append(detail)
        if "No module named 'cv2'" in detail or "ModuleNotFoundError: No module named 'cv2'" in detail:
            lines.append("")
            lines.append(
                "提示：当前为精简包且缺少 OpenCV。请重新打包时加 -WithOpenCV/-Full，"
                "或安装含本修复的新版本（已改为启动时不强制依赖 cv2）。"
            )
    lines.append("")
    lines.append("可在「开始菜单 → Testory → 打开安装目录」查看程序文件。")
    return "\n".join(lines)


def _terminate_backend(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def _shutdown_all(proc: Optional[subprocess.Popen], *, port: int = DEFAULT_PORT) -> None:
    del port
    _terminate_backend(proc)
    try:
        from desktop_startup import shutdown_all_services

        shutdown_all_services()
    except Exception:
        pass


def run_desktop(port: int = DEFAULT_PORT) -> int:
    root = install_root()
    if sys.platform == "win32":
        from packaging.win_app_icon import set_process_app_user_model_id

        set_process_app_user_model_id()
    os.chdir(root)

    lock_fp = acquire_instance_lock()
    if lock_fp is None:
        _show_error(instance_lock_message())
        return 1

    user_data = apply_local_env(root, port)
    if _port_in_use("127.0.0.1", port):
        # 上次壳卡死退出后，后端常会残留占端口；优先自动回收本产品孤儿后端
        _reclaim_orphaned_backend(port)
    if _port_in_use("127.0.0.1", port):
        _show_error(
            f"端口 {port} 已被占用，无法启动 Testory。\n\n"
            "请关闭其他 Testory 实例，或在用户数据 .env 中设置 FLASK_RUN_PORT。"
        )
        return 1

    pre_errors, pre_warn = run_launch_preflight(root, port=port)
    if pre_errors:
        _show_error(
            "Testory 安装不完整，无法启动：\n\n"
            + "\n\n".join(f"• {e}" for e in pre_errors)
            + f"\n\n安装目录：{root}"
        )
        return 1

    ensure_dotenv(root, user_data)
    python, runtime_err = verify_bundled_python(root)
    if runtime_err:
        if resolve_bundled_python(root) is None:
            python = Path(sys.executable)
        else:
            _show_error(runtime_err)
            return 1
    elif python is None:
        python = resolve_python(root)

    has_backend = (root / "app.py").is_file() or (_backend_exe_path(root) is not None)
    if not has_backend:
        _show_error(
            "未找到后端程序（app.py 或 runtime\\testory_app\\TestoryBackend.exe）。\n"
            f"安装目录：{root}\n请重新安装完整安装包（需含 runtime 目录）。"
        )
        return 1

    proc: Optional[subprocess.Popen] = None
    exit_code = 1
    try:
        proc = start_backend(root, python, port, user_data)

        def _backend_ready() -> bool:
            if proc is None or proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=2
                ) as resp:
                    if resp.status == 200:
                        # 后端就绪后，异步检查可选组件安装状态并写入标记
                        _check_components_hint(root, user_data)
                        return True
                    return False
            except (urllib.error.URLError, OSError, TimeoutError):
                return False

        def _on_closed() -> None:
            _shutdown_all(proc, port=port)

        exit_code = run_native_shell(
            root=root,
            app_url=f"http://127.0.0.1:{port}/",
            wait_until_ready=_backend_ready,
            startup_failed_message=lambda: _startup_failed_message(
                root, user_data, port, proc
            ),
            on_closed=_on_closed,
        )
    except Exception as e:
        _log_error(root, user_data, e)
        _show_error(f"启动失败：{e}\n\n详见 {user_data / 'logs' / 'desktop_launch.log'}")
        _shutdown_all(proc, port=port)
        return 1
    finally:
        try:
            lock_fp.close()
        except Exception:
            pass

    return exit_code


def main() -> None:
    if "--probe" in sys.argv:
        root = install_root()
        os.chdir(root)
        apply_local_env(root, DEFAULT_PORT)
        errs, warns = run_launch_preflight(root, force_full_probe=True)
        if errs:
            print("probe failed:", file=sys.stderr)
            for e in errs:
                print(e, file=sys.stderr)
            raise SystemExit(1)
        for w in warns:
            print("warn:", w, file=sys.stderr)
        _, verr = verify_bundled_python(root)
        if verr:
            print(verr, file=sys.stderr)
            raise SystemExit(1)
        print("probe ok")
        raise SystemExit(0)
    port = int(os.environ.get("FLASK_RUN_PORT", DEFAULT_PORT))
    raise SystemExit(run_desktop(port))


if __name__ == "__main__":
    main()
