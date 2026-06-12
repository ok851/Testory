# -*- coding: utf-8 -*-
"""
Android SDK 自动管理器
负责检测、下载、安装 Android SDK 命令行工具及相关组件
支持 PyQt6 异步进度反馈，避免阻塞主线程
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QThread, pyqtSignal


class AndroidSDKSignals(QThread):
    """SDK 管理器的信号定义，用于向 UI 传递进度和状态"""

    # 进度信号：(当前步骤描述, 百分比 0-100)
    progress = pyqtSignal(str, int)
    # 日志信号：(日志消息)
    log_message = pyqtSignal(str)
    # 完成信号：(是否成功, 消息)
    finished = pyqtSignal(bool, str)
    # 错误信号：(错误消息)
    error = pyqtSignal(str)


class AndroidSDKManager(QThread):
    """
    Android SDK 自动化管理器

    功能：
    - 检查系统中是否存在有效的 Android SDK
    - 自动下载并解压 commandlinetools
    - 接受所有 licenses
    - 安装必要的 SDK 包（platform-tools, emulator, system-images）

    使用示例：
        manager = AndroidSDKManager(sdk_path="C:\\MyApp\\AndroidSDK")
        manager.signals.progress.connect(lambda msg, pct: print(f"{msg}: {pct}%"))
        manager.signals.finished.connect(lambda ok, msg: print("完成:", msg))
        manager.start()  # 在后台线程运行
    """

    # Google 官方下载地址（Windows commandlinetools）
    CMDTOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"

    def __init__(
        self,
        sdk_path: Optional[str] = None,
        packages: Optional[List[str]] = None,
        parent=None,
    ):
        """
        初始化 SDK 管理器

        Args:
            sdk_path: SDK 安装路径。如果为 None，则检查 ANDROID_HOME 环境变量
            packages: 需要安装的 SDK 包列表，默认为 ["platform-tools", "emulator"]
            parent: PyQt 父对象
        """
        super().__init__(parent)
        self.signals = AndroidSDKSignals()

        # 确定 SDK 路径
        if sdk_path:
            self.sdk_path = Path(sdk_path)
        else:
            android_home = os.environ.get("ANDROID_HOME")
            if android_home:
                self.sdk_path = Path(android_home)
            else:
                # 默认路径：软件目录下的 AndroidSDK 文件夹
                self.sdk_path = Path(__file__).parent / "AndroidSDK"

        # 验证路径合法性（不能包含中文和空格）
        self._validate_path(self.sdk_path)

        # 要安装的包列表
        self.packages = packages or [
            "platform-tools",
            "emulator",
            "system-images;android-33;google_apis;x86_64",
        ]

        # cmdline-tools 内部路径结构
        self.cmdline_tools_dir = self.sdk_path / "cmdline-tools" / "latest"
        self.sdkmanager_exe = self.cmdline_tools_dir / "bin" / "sdkmanager.bat"

    def _validate_path(self, path: Path) -> None:
        """
        验证 SDK 路径是否合法

        Args:
            path: 待验证的路径

        Raises:
            ValueError: 路径包含中文或空格时抛出异常
        """
        path_str = str(path)

        # 检查是否包含空格
        if " " in path_str:
            raise ValueError(
                f"SDK 安装路径不能包含空格：{path_str}\n"
                f"请选择一个不包含空格的路径，例如：C:\\AndroidSDK"
            )

        # 检查是否包含中文字符
        if re.search(r"[\u4e00-\u9fff]", path_str):
            raise ValueError(
                f"SDK 安装路径不能包含中文：{path_str}\n"
                f"请选择一个纯英文路径，例如：C:\\AndroidSDK"
            )

    def check_sdk(self) -> Tuple[bool, str]:
        """
        检查系统中是否存在有效的 Android SDK

        Returns:
            (是否有效, 说明消息)
        """
        # 检查 SDK 根目录是否存在
        if not self.sdk_path.exists():
            return False, f"SDK 目录不存在：{self.sdk_path}"

        # 检查 sdkmanager 是否存在
        if not self.sdkmanager_exe.exists():
            return False, f"sdkmanager 未找到：{self.sdkmanager_exe}"

        # 尝试执行 sdkmanager --version 验证可用性
        try:
            result = subprocess.run(
                [str(self.sdkmanager_exe), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                return True, f"SDK 已安装，版本：{version}"
            else:
                return False, f"sdkmanager 执行失败：{result.stderr}"
        except FileNotFoundError:
            return False, "找不到 sdkmanager 可执行文件"
        except subprocess.TimeoutExpired:
            return False, "sdkmanager 命令超时"
        except Exception as e:
            return False, f"SDK 检查出错：{str(e)}"

    def download_cmdtools(self) -> bool:
        """
        从 Google 官方下载最新的 Windows commandlinetools 并解压

        Returns:
            是否成功
        """
        import urllib.request

        temp_zip = None
        try:
            self.signals.progress.emit("正在下载 commandlinetools...", 10)
            self.signals.log_message.emit(f"下载地址：{self.CMDTOOLS_URL}")

            # 创建临时文件
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                temp_zip = tmp.name

            # 下载文件（带进度）
            def report_hook(block_num, block_size, total_size):
                downloaded = block_num * block_size
                if total_size > 0:
                    percent = min(int(downloaded * 100 / total_size), 90)
                    self.signals.progress.emit(
                        f"下载中... {downloaded // 1024}KB / {total_size // 1024}KB",
                        10 + percent // 10,
                    )

            urllib.request.urlretrieve(self.CMDTOOLS_URL, temp_zip, report_hook)
            self.signals.progress.emit("下载完成，正在解压...", 90)
            self.signals.log_message.emit(f"解压到：{self.sdk_path}")

            # 解压到 SDK 目录
            with zipfile.ZipFile(temp_zip, "r") as zip_ref:
                zip_ref.extractall(self.sdk_path)

            # 清理临时文件
            os.unlink(temp_zip)
            temp_zip = None

            self.signals.progress.emit("解压完成", 100)
            self.signals.log_message.emit("commandlinetools 解压成功")

            return True

        except Exception as e:
            self.signals.error.emit(f"下载或解压失败：{str(e)}")
            # 清理临时文件
            if temp_zip and os.path.exists(temp_zip):
                try:
                    os.unlink(temp_zip)
                except:
                    pass
            return False

    def accept_licenses(self) -> bool:
        """
        自动接受所有 SDK licenses

        通过 subprocess 与 sdkmanager 交互，自动输入 'y'

        Returns:
            是否成功
        """
        if not self.sdkmanager_exe.exists():
            self.signals.error.emit("sdkmanager 未找到，请先下载 commandlinetools")
            return False

        try:
            self.signals.progress.emit("正在接受 licenses...", 50)
            self.signals.log_message.emit("执行 sdkmanager --licenses")

            # 启动 sdkmanager --licenses 进程
            process = subprocess.Popen(
                [str(self.sdkmanager_exe), "--licenses"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            # 持续读取输出并自动回复 'y'
            output_lines = []
            while True:
                line = process.stdout.readline()
                if not line:
                    break

                line = line.rstrip()
                output_lines.append(line)
                self.signals.log_message.emit(f"[licenses] {line}")

                # 检测到询问时自动回复 'y'
                if "Do you accept the license" in line or "[y/N]" in line:
                    process.stdin.write("y\n")
                    process.stdin.flush()
                    self.signals.log_message.emit("[自动回复] y")

            # 等待进程结束
            process.wait(timeout=120)

            if process.returncode == 0:
                self.signals.progress.emit("Licenses 已接受", 60)
                self.signals.log_message.emit("所有 licenses 已成功接受")
                return True
            else:
                self.signals.error.emit(
                    f"接受 licenses 失败，返回码：{process.returncode}"
                )
                return False

        except subprocess.TimeoutExpired:
            self.signals.error.emit("接受 licenses 超时")
            process.kill()
            return False
        except Exception as e:
            self.signals.error.emit(f"接受 licenses 出错：{str(e)}")
            return False

    def install_packages(self, packages: Optional[List[str]] = None) -> bool:
        """
        安装指定的 SDK 包

        Args:
            packages: 要安装的包列表，默认为初始化时设置的包

        Returns:
            是否成功
        """
        if not self.sdkmanager_exe.exists():
            self.signals.error.emit("sdkmanager 未找到")
            return False

        pkgs = packages or self.packages
        total = len(pkgs)

        for idx, pkg in enumerate(pkgs, 1):
            try:
                progress_pct = 60 + int((idx - 1) * 35 / total)
                self.signals.progress.emit(f"正在安装 {pkg}...", progress_pct)
                self.signals.log_message.emit(f"安装第 {idx}/{total} 个包：{pkg}")

                # 执行安装命令（自动接受 licenses）
                process = subprocess.Popen(
                    [str(self.sdkmanager_exe), pkg, "--accept-license"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                # 实时读取输出
                while True:
                    line = process.stdout.readline()
                    if not line:
                        break
                    line = line.rstrip()
                    if line:
                        self.signals.log_message.emit(f"[{pkg}] {line}")

                process.wait(timeout=300)

                if process.returncode != 0:
                    self.signals.error.emit(f"安装 {pkg} 失败，返回码：{process.returncode}")
                    return False

                self.signals.log_message.emit(f"成功安装：{pkg}")

            except subprocess.TimeoutExpired:
                self.signals.error.emit(f"安装 {pkg} 超时")
                process.kill()
                return False
            except Exception as e:
                self.signals.error.emit(f"安装 {pkg} 出错：{str(e)}")
                return False

        self.signals.progress.emit("所有包安装完成", 100)
        return True

    def run(self):
        """
        线程主函数：按顺序执行 SDK 检测和安装流程

        此方法在后台线程中运行，不会阻塞 UI
        """
        try:
            # 步骤 1：检查 SDK 是否已存在
            self.signals.progress.emit("检查 SDK 环境...", 0)
            sdk_valid, sdk_msg = self.check_sdk()

            if sdk_valid:
                self.signals.log_message.emit(f"SDK 已存在：{sdk_msg}")
                self.signals.progress.emit("SDK 已就绪", 100)
                self.signals.finished.emit(True, sdk_msg)
                return

            self.signals.log_message.emit(f"SDK 未找到：{sdk_msg}")

            # 步骤 2：创建 SDK 目录
            self.signals.progress.emit("创建 SDK 目录...", 5)
            self.sdk_path.mkdir(parents=True, exist_ok=True)
            self.signals.log_message.emit(f"SDK 目录：{self.sdk_path}")

            # 步骤 3：下载 commandlinetools
            if not self.download_cmdtools():
                self.signals.finished.emit(False, "下载 commandlinetools 失败")
                return

            # 步骤 4：接受 licenses
            if not self.accept_licenses():
                self.signals.finished.emit(False, "接受 licenses 失败")
                return

            # 步骤 5：安装 SDK 包
            if not self.install_packages():
                self.signals.finished.emit(False, "安装 SDK 包失败")
                return

            # 全部成功
            self.signals.finished.emit(
                True,
                f"Android SDK 已成功安装到：{self.sdk_path}",
            )

        except ValueError as e:
            # 路径验证失败
            self.signals.error.emit(str(e))
            self.signals.finished.emit(False, str(e))
        except Exception as e:
            self.signals.error.emit(f"SDK 安装过程出错：{str(e)}")
            self.signals.finished.emit(False, f"SDK 安装失败：{str(e)}")

    def get_adb_path(self) -> Optional[Path]:
        """
        获取 adb 可执行文件路径

        Returns:
            adb 路径，如果不存在则返回 None
        """
        adb_path = self.sdk_path / "platform-tools" / "adb.exe"
        if adb_path.exists():
            return adb_path
        return None

    def get_emulator_path(self) -> Optional[Path]:
        """
        获取 emulator 可执行文件路径

        Returns:
            emulator 路径，如果不存在则返回 None
        """
        emulator_path = self.sdk_path / "emulator" / "emulator.exe"
        if emulator_path.exists():
            return emulator_path
        return None


def setup_android_sdk(
    sdk_path: Optional[str] = None,
    on_progress: Optional[Callable[[str, int], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    on_finished: Optional[Callable[[bool, str], None]] = None,
) -> AndroidSDKManager:
    """
    便捷函数：创建并启动 Android SDK 管理器

    Args:
        sdk_path: SDK 安装路径
        on_progress: 进度回调函数 callback(message, percentage)
        on_log: 日志回调函数 callback(message)
        on_finished: 完成回调函数 callback(success, message)

    Returns:
        AndroidSDKManager 实例（已启动）
    """
    manager = AndroidSDKManager(sdk_path=sdk_path)

    if on_progress:
        manager.signals.progress.connect(on_progress)
    if on_log:
        manager.signals.log_message.connect(on_log)
    if on_finished:
        manager.signals.finished.connect(on_finished)

    manager.start()
    return manager


if __name__ == "__main__":
    # 测试代码
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow, QTextEdit, QVBoxLayout, QWidget

    class TestWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Android SDK Manager 测试")
            self.setGeometry(100, 100, 800, 600)

            central = QWidget()
            layout = QVBoxLayout(central)

            self.log_view = QTextEdit()
            self.log_view.setReadOnly(True)
            layout.addWidget(self.log_view)

            self.setCentralWidget(central)

            # 启动 SDK 管理器
            sdk_path = "C:\\AndroidSDK"  # 修改为你的测试路径
            self.manager = AndroidSDKManager(sdk_path=sdk_path)
            self.manager.signals.progress.connect(self.on_progress)
            self.manager.signals.log_message.connect(self.on_log)
            self.manager.signals.finished.connect(self.on_finished)
            self.manager.signals.error.connect(self.on_error)

            self.log_view.append(f"开始安装 SDK 到：{sdk_path}\n")
            self.manager.start()

        def on_progress(self, message: str, percentage: int):
            self.log_view.append(f"[进度 {percentage}%] {message}")

        def on_log(self, message: str):
            self.log_view.append(f"[日志] {message}")

        def on_finished(self, success: bool, message: str):
            status = "成功" if success else "失败"
            self.log_view.append(f"\n[完成 {status}] {message}")

        def on_error(self, message: str):
            self.log_view.append(f"\n[错误] {message}")

    app = QApplication(sys.argv)
    window = TestWindow()
    window.show()
    sys.exit(app.exec())
