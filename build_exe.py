# -*- coding: utf-8 -*-
"""
PyInstaller 打包脚本

将桌面自动化平台打包为独立可执行文件(.exe)。

使用方法:
    python build_exe.py

打包结果:
    dist/NewUITestPlatform.exe - 单文件可执行程序
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def check_pyinstaller():
    """检查 PyInstaller 是否已安装。"""
    try:
        import PyInstaller
        return True
    except ImportError:
        return False


def install_pyinstaller():
    """安装 PyInstaller。"""
    import subprocess
    print("正在安装 PyInstaller...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
    print("PyInstaller 安装完成")


def clean_build_dirs():
    """清理构建目录。"""
    dirs_to_clean = ["build", "dist"]
    for dir_name in dirs_to_clean:
        if os.path.isdir(dir_name):
            print(f"清理 {dir_name}/ 目录...")
            shutil.rmtree(dir_name)

    # 清理 .spec 文件
    for spec_file in Path(".").glob("*.spec"):
        print(f"删除 {spec_file}...")
        spec_file.unlink()


def get_hidden_imports() -> list:
    """获取需要显式导入的隐藏依赖。"""
    return [
        # pywinauto 依赖
        "pywinauto",
        "pywinauto.application",
        "pywinauto.findwindows",
        "pywinauto.controls.uiawrapper",
        "pywinauto.controls.hwndwrapper",
        # Windows COM 依赖
        "comtypes",
        "comtypes.client",
        # 平台检测
        "ctypes",
        "ctypes.wintypes",
        # 数据处理
        "json",
        "re",
        "pathlib",
        # Flask 相关
        "flask",
        "werkzeug",
        "jinja2",
        "markupsafe",
        "itsdangerous",
        "click",
        # 其他依赖
        "psutil",
    ]


def get_data_files() -> list:
    """获取需要打包的数据文件。"""
    data_files = []

    # 模板文件
    if os.path.isdir("templates"):
        data_files.append(("templates", "templates"))

    # 静态文件
    if os.path.isdir("static"):
        data_files.append(("static", "static"))

    # 数据目录
    if os.path.isdir("data"):
        data_files.append(("data", "data"))

    return data_files


def build_exe():
    """执行打包。"""
    import PyInstaller.__main__

    # 入口脚本
    entry_script = "app.py"
    if not os.path.isfile(entry_script):
        print(f"错误: 找不到入口脚本 {entry_script}")
        print("请确保 app.py 在当前目录中")
        return False

    # 构建命令参数
    args = [
        entry_script,
        "--onefile",  # 单文件
        "--windowed",  # Windows 窗口应用（无控制台）
        "--name", "NewUITestPlatform",
        "--clean",  # 清理临时文件
    ]

    # 添加图标（如果有）
    if os.path.isfile("icon.ico"):
        args.extend(["--icon", "icon.ico"])

    # 添加隐藏导入
    for imp in get_hidden_imports():
        args.extend(["--hidden-import", imp])

    # 添加数据文件
    for src, dst in get_data_files():
        if os.path.exists(src):
            args.extend(["--add-data", f"{src}{os.pathsep}{dst}"])

    # 排除不必要的模块以减小体积
    excludes = [
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "tkinter",
        "test",
        "unittest",
        "pydoc",
        "email",
    ]
    for ex in excludes:
        args.extend(["--exclude-module", ex])

    print("=" * 60)
    print("开始打包 NewUITestPlatform...")
    print("=" * 60)
    print(f"命令参数: {' '.join(args)}")
    print()

    try:
        PyInstaller.__main__.run(args)
        return True
    except Exception as e:
        print(f"打包失败: {e}")
        return False


def create_startup_script():
    """创建启动脚本。"""
    script_content = '''@echo off
chcp 65001 >nul
echo 正在启动 NewUITestPlatform...
echo.

:: 检查是否首次运行
if not exist "data\\.setup_completed" (
    echo 首次运行，正在初始化...
    echo.
)

:: 启动程序
start "" "%~dp0NewUITestPlatform.exe"

:: 等待程序启动
timeout /t 2 /nobreak >nul

:: 打开浏览器
echo 正在打开浏览器访问平台...
start http://127.0.0.1:5000

echo.
echo 平台已在浏览器中打开，地址: http://127.0.0.1:5000
echo 按任意键关闭此窗口...
pause >nul
'''

    with open("dist/启动平台.bat", "w", encoding="utf-8") as f:
        f.write(script_content)

    print("已创建启动脚本: dist/启动平台.bat")


def create_readme():
    """创建发行说明。"""
    readme = '''# NewUITestPlatform 桌面自动化平台

## 快速开始

### 方法一：使用启动脚本（推荐）
1. 双击运行 `启动平台.bat`
2. 等待浏览器自动打开
3. 开始使用！

### 方法二：直接运行
1. 双击运行 `NewUITestPlatform.exe`
2. 打开浏览器访问 http://127.0.0.1:5000

## 零配置使用

本平台采用零配置设计：
- 自动扫描已安装的应用程序
- 支持自然语言指令（如"打开记事本"）
- 首次启动自动完成初始化

## 常用指令示例

```
打开 记事本
启动 chrome
整理 下载 文件夹
最大化 当前窗口
```

## 系统要求

- Windows 10/11 (64位)
- 无需 Python 环境
- 无需额外安装依赖

## 文件说明

- `NewUITestPlatform.exe` - 主程序
- `启动平台.bat` - 一键启动脚本
- `data/` - 数据目录（自动创建）

## 技术支持

如有问题，请查看日志文件：
- `logs/uat_platform_*.log`
- `logs/errors_*.log`
'''

    with open("dist/README.md", "w", encoding="utf-8") as f:
        f.write(readme)

    print("已创建发行说明: dist/README.md")


def main():
    """主函数。"""
    print("NewUITestPlatform 打包工具")
    print("=" * 60)

    # 检查 PyInstaller
    if not check_pyinstaller():
        print("PyInstaller 未安装")
        install = input("是否自动安装 PyInstaller? (y/n): ").strip().lower()
        if install == "y":
            install_pyinstaller()
        else:
            print("请手动安装: pip install pyinstaller")
            return

    # 询问是否清理
    clean = input("是否清理之前的构建? (y/n): ").strip().lower()
    if clean == "y":
        clean_build_dirs()

    # 执行打包
    if build_exe():
        print()
        print("=" * 60)
        print("打包成功！")
        print("=" * 60)

        # 创建辅助文件
        create_startup_script()
        create_readme()

        print()
        print("输出文件:")
        print(f"  - dist/NewUITestPlatform.exe ({os.path.getsize('dist/NewUITestPlatform.exe') / 1024 / 1024:.1f} MB)")
        print("  - dist/启动平台.bat")
        print("  - dist/README.md")
        print()
        print("分发建议:")
        print("  1. 将整个 dist/ 目录压缩为 zip 文件")
        print("  2. 用户解压后双击 启动平台.bat 即可使用")
        print()
    else:
        print("打包失败，请检查错误信息")


if __name__ == "__main__":
    main()
