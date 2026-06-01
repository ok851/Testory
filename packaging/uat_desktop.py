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
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from desktop_user_data import ensure_user_data_dirs

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
    """优先使用安装目录内的 .venv，保证用户机器无需单独装 Python。"""
    if sys.platform == "win32":
        for name in ("pythonw.exe", "python.exe"):
            cand = root / ".venv" / "Scripts" / name
            if cand.is_file():
                return cand
    venv_py = root / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return venv_py
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
    env = os.environ.copy()
    apply_local_env(root, port)
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
    python = resolve_python(root)

    if not (root / "app.py").is_file():
        _show_error(f"未找到程序文件：{root / 'app.py'}\n请重新安装。")
        return 1

    proc: Optional[subprocess.Popen] = None
    try:
        proc = start_backend(root, python, port, user_data)
        if not wait_until_ready(port, proc):
            _show_error(_startup_failed_message(root, user_data, port, proc))
            return 1

        try:
            import webview
        except ImportError:
            _show_error(
                "缺少桌面界面组件 pywebview。\n"
                "请重新执行完整安装包制作流程并安装。"
            )
            return 1

        url = f"http://127.0.0.1:{port}/"

        def on_closed() -> None:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()

        window = webview.create_window(
            APP_TITLE,
            url,
            width=1360,
            height=860,
            min_size=(1024, 640),
            text_select=True,
        )
        webview.start(on_closed, gui="edgechromium" if sys.platform == "win32" else None)
        return 0
    except Exception as e:
        _log_error(root, user_data, e)
        _show_error(f"启动失败：{e}\n\n详见 {user_data / 'logs' / 'desktop_launch.log'}")
        return 1
    finally:
        if proc and proc.poll() is None:
            proc.terminate()


def main() -> None:
    port = int(os.environ.get("FLASK_RUN_PORT", DEFAULT_PORT))
    raise SystemExit(run_desktop(port))


if __name__ == "__main__":
    main()
