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
if (_install_root / "app.py").is_file():
    _root_str = str(_install_root)
    if _root_str not in sys.path:
        sys.path.insert(0, _root_str)

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional

from desktop_user_data import ensure_user_data_dirs
from packaging.desktop_shell import run_native_shell
from packaging.testory_runtime import resolve_bundled_python, verify_bundled_python

APP_TITLE = "Testory"
DEFAULT_PORT = 5000


def install_root() -> Path:
    """安装目录或开发时的项目根目录。"""
    here = Path(__file__).resolve().parent
    root = here.parent
    if (root / "app.py").is_file():
        return root
    return Path.cwd()


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
    return user_data


def ensure_dotenv(root: Path) -> None:
    env_path = root / ".env"
    example = root / ".env.example"
    if env_path.is_file():
        return
    if example.is_file():
        shutil.copy(example, env_path)
        return
    env_path.write_text(
        "# Auto-created by uat_desktop\n"
        "DEPLOYMENT_PROFILE=local\n"
        "DESKTOP_EXECUTION_MODE=inprocess\n"
        "PLAYWRIGHT_HEADLESS=0\n",
        encoding="utf-8",
    )


def _no_window_flags() -> int:
    if sys.platform != "win32":
        return 0
    return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]


def start_backend(root: Path, python: Path, port: int, user_data: Path) -> subprocess.Popen:
    apply_local_env(root, port)
    env = os.environ.copy()
    log_dir = user_data / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "backend_startup.log"
    log_fp = open(log_path, "a", encoding="utf-8", buffering=1)
    log_fp.write(f"\n--- backend start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_fp.flush()
    return subprocess.Popen(
        [str(python), str(root / "app.py")],
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
    os.chdir(root)
    ensure_dotenv(root)
    user_data = apply_local_env(root, port)
    python, runtime_err = verify_bundled_python(root)
    if runtime_err:
        if resolve_bundled_python(root) is None:
            python = Path(sys.executable)
        else:
            _show_error(runtime_err)
            return 1
    elif python is None:
        python = resolve_python(root)

    if not (root / "app.py").is_file():
        _show_error(f"未找到程序文件：{root / 'app.py'}\n请重新安装。")
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
        # 供 Testory.exe 捕获启动失败原因（仅做导入自检，不真正启动 UI）
        root = install_root()
        os.chdir(root)
        ensure_dotenv(root)
        apply_local_env(root, DEFAULT_PORT)
        print("probe ok")
        raise SystemExit(0)
    port = int(os.environ.get("FLASK_RUN_PORT", DEFAULT_PORT))
    raise SystemExit(run_desktop(port))


if __name__ == "__main__":
    main()
