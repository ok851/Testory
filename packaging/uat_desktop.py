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

import os
import sys
from pathlib import Path

# 安装包通过 pythonw packaging\uat_desktop.py 启动时，需把安装根目录加入 sys.path
_install_root = Path(__file__).resolve().parent.parent
_root_str = str(_install_root)
if _root_str not in sys.path:
    sys.path.insert(0, _root_str)

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional

from desktop_user_data import ensure_user_data_dirs, resolve_env_file
from packaging.desktop_shell import run_native_shell
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
    os.environ.setdefault("UAT_DESKTOP_MODE", "1")
    os.environ.setdefault("DEPLOYMENT_MODE", "client")
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
    }
    try:
        text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    except OSError:
        return
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
        "PLATFORM_ADMIN_URL=http://127.0.0.1:5100\n"
        "EMBEDDED_BROWSER_GATEWAY_URL=http://127.0.0.1:8765\n"
        "EMBEDDED_BROWSER_GATEWAY_SECRET=hufirst-desktop-local\n"
        "EMBEDDED_BROWSER_PUBLIC_WS_BASE=ws://127.0.0.1:8765\n"
        "EMBEDDED_BROWSER_AUTO_START_GATEWAY=1\n"
        "LOCAL_LLM_BASE_URL=http://127.0.0.1:11434\n",
        encoding="utf-8",
    )
    return env_path


def _no_window_flags() -> int:
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]


def _backend_command(root: Path, python: Path) -> list:
    exe = _backend_exe_path(root)
    if exe is not None:
        return [str(exe)]
    legacy = root / "app.py"
    if legacy.is_file():
        return [str(python), str(legacy)]
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


def wait_until_ready(port: int, proc: subprocess.Popen, timeout_sec: float = 120.0) -> bool:
    """等待 Flask 进程可响应（/api/health 即可，不依赖 DB 就绪探针）。"""
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(0.5)
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
    lines.append("")
    lines.append("可在「开始菜单 → Testory → 打开安装目录」查看程序文件。")
    return "\n".join(lines)


def run_desktop(port: int = DEFAULT_PORT) -> int:
    root = install_root()
    if sys.platform == "win32":
        from packaging.win_app_icon import set_process_app_user_model_id

        set_process_app_user_model_id()
    os.chdir(root)

    pre_errors, pre_warn = run_launch_preflight(root, port=port)
    if pre_errors:
        _show_error(
            "Testory 安装不完整，无法启动：\n\n"
            + "\n\n".join(f"• {e}" for e in pre_errors)
            + f"\n\n安装目录：{root}"
        )
        return 1

    user_data = apply_local_env(root, port)
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
    try:
        proc = start_backend(root, python, port, user_data)

        def _backend_ready() -> bool:
            if proc is None:
                return False
            if proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=2
                ) as resp:
                    return resp.status == 200
            except (urllib.error.URLError, OSError, TimeoutError):
                return False

        def _on_closed() -> None:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()

        return run_native_shell(
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
        return 1
    finally:
        if proc and proc.poll() is None:
            proc.terminate()


def main() -> None:
    if "--probe" in sys.argv:
        root = install_root()
        os.chdir(root)
        apply_local_env(root, DEFAULT_PORT)
        errs, warns = run_launch_preflight(root)
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
