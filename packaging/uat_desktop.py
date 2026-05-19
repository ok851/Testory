# -*- coding: utf-8 -*-
"""
HuFirst UAT 桌面版入口（安装包 / 快捷方式应指向本文件）。

- 自动设置本地运行环境变量（用户无需配置 .env）
- 后台启动 Flask（无黑色命令行窗口）
- 用 pywebview 打开独立软件窗口（不是系统浏览器）

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

APP_TITLE = "HuFirst UAT 测试平台"
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


def apply_local_env(root: Path, port: int) -> None:
    """内置默认配置，覆盖「必须手改 .env」的场景。"""
    data = root / "data"
    logs = root / "logs"
    data.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DEPLOYMENT_PROFILE", "local")
    os.environ.setdefault("DESKTOP_EXECUTION_MODE", "inprocess")
    os.environ.setdefault("PLAYWRIGHT_HEADLESS", "0")
    os.environ.setdefault("DESKTOP_AUTO_START_GATEWAY", "0")
    os.environ.setdefault("FLASK_RUN_PORT", str(port))
    os.environ.setdefault("UAT_DATA_DIR", str(data))
    os.environ.setdefault("DATABASE_PATH", str(data / "test_cases.db"))
    os.environ.setdefault("UAT_DESKTOP_MODE", "1")
    os.environ.setdefault("SKIP_ENV_EXAMPLE_SYNC", "1")
    # 安装包内自带的 Chromium，禁止首次启动再联网下载
    bundled_browsers = root / "playwright-browsers"
    if bundled_browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)
        os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"


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


def start_backend(root: Path, python: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    apply_local_env(root, port)
    return subprocess.Popen(
        [str(python), str(root / "app.py")],
        cwd=str(root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_no_window_flags(),
    )


def wait_until_ready(port: int, timeout_sec: float = 90.0) -> bool:
    url = f"http://127.0.0.1:{port}/api/health/ready"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(0.4)
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


def run_desktop(port: int = DEFAULT_PORT) -> int:
    root = install_root()
    os.chdir(root)
    ensure_dotenv(root)
    apply_local_env(root, port)
    python = resolve_python(root)

    if not (root / "app.py").is_file():
        _show_error(f"未找到程序文件：{root / 'app.py'}\n请重新安装。")
        return 1

    proc: Optional[subprocess.Popen] = None
    try:
        proc = start_backend(root, python, port)
        if not wait_until_ready(port):
            _show_error(
                "服务启动超时。\n"
                "请检查 5000 端口是否被占用，或查看 logs 目录下日志。\n"
                "也可联系管理员重新安装。"
            )
            return 1

        try:
            import webview
        except ImportError:
            _show_error(
                "缺少桌面界面组件 pywebview。\n"
                "请运行安装目录下 .venv 中的 pip install pywebview，或重新执行完整安装包制作流程。"
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
        _show_error(f"启动失败：{e}")
        return 1
    finally:
        if proc and proc.poll() is None:
            proc.terminate()


def main() -> None:
    port = int(os.environ.get("FLASK_RUN_PORT", DEFAULT_PORT))
    raise SystemExit(run_desktop(port))


if __name__ == "__main__":
    main()
