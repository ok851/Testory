# -*- coding: utf-8 -*-
"""
零配置自动初始化模块

提供功能：
1. 首次启动自动配置
2. 环境检测与依赖安装
3. 常用应用自动扫描与别名生成
4. 用户引导流程
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class AutoSetupManager:
    """
    自动配置管理器。

    在首次启动时自动完成所有必要的初始化配置。
    """

    SETUP_VERSION = "1.0.0"
    SETUP_FLAG_FILE = "data/.setup_completed"

    def __init__(self):
        self.root_dir = Path(__file__).resolve().parents[2]
        self.data_dir = self.root_dir / "data"
        self.data_dir.mkdir(exist_ok=True)

        self.setup_flag_path = self.data_dir / ".setup_completed"
        self.config_path = self.data_dir / "auto_config.json"

        self._config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """加载自动配置。"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except Exception:
                self._config = {}

    def _save_config(self) -> None:
        """保存自动配置。"""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def is_first_run(self) -> bool:
        """检查是否是首次运行。"""
        return not self.setup_flag_path.exists()

    def mark_setup_completed(self) -> None:
        """标记初始化已完成。"""
        try:
            self.setup_flag_path.write_text(f"setup_version={self.SETUP_VERSION}\n", encoding="utf-8")
        except Exception:
            pass

    def run_auto_setup(self, progress_callback: Optional[callable] = None) -> Dict[str, Any]:
        """
        运行自动初始化流程。

        Returns:
            初始化结果报告
        """
        def report(step: str, status: str = "ok", message: str = ""):
            if progress_callback:
                progress_callback({"step": step, "status": status, "message": message})

        results = {
            "success": True,
            "steps": [],
            "detected_apps": [],
            "warnings": [],
            "errors": [],
        }

        # 步骤1: 检测操作系统
        report("os_check", "running")
        if sys.platform != "win32":
            results["warnings"].append("当前平台不是Windows，桌面自动化功能可能受限")
        results["steps"].append({"name": "os_check", "status": "ok"})
        report("os_check", "ok")

        # 步骤2: 检测Python版本
        report("python_check", "running")
        if sys.version_info < (3, 8):
            results["errors"].append("需要Python 3.8或更高版本")
            results["success"] = False
        results["steps"].append({"name": "python_check", "status": "ok"})
        report("python_check", "ok")

        # 步骤3: 检测关键依赖
        report("dependency_check", "running")
        missing_deps = self._check_dependencies()
        if missing_deps:
            results["warnings"].append(f"缺少可选依赖: {', '.join(missing_deps)}")
            results["steps"].append({"name": "dependency_check", "status": "warning", "missing": missing_deps})
        else:
            results["steps"].append({"name": "dependency_check", "status": "ok"})
        report("dependency_check", "ok" if not missing_deps else "warning")

        # 步骤4: 扫描常用应用
        report("app_scan", "running")
        try:
            detected_apps = self._scan_common_apps()
            results["detected_apps"] = detected_apps
            results["steps"].append({"name": "app_scan", "status": "ok", "count": len(detected_apps)})
            report("app_scan", "ok", f"发现 {len(detected_apps)} 个应用")
        except Exception as e:
            results["warnings"].append(f"应用扫描失败: {e}")
            results["steps"].append({"name": "app_scan", "status": "error", "error": str(e)})
            report("app_scan", "error", str(e))

        # 步骤5: 生成推荐配置
        report("config_generation", "running")
        try:
            config = self._generate_recommended_config(results["detected_apps"])
            self._config = config
            self._save_config()
            results["steps"].append({"name": "config_generation", "status": "ok"})
            report("config_generation", "ok")
        except Exception as e:
            results["warnings"].append(f"配置生成失败: {e}")
            results["steps"].append({"name": "config_generation", "status": "error"})
            report("config_generation", "error", str(e))

        # 步骤6: 构建应用目录
        report("catalog_build", "running")
        try:
            from modules.desktop.desktop_app_catalog import ensure_catalog_built
            catalog = ensure_catalog_built()
            results["steps"].append({"name": "catalog_build", "status": "ok", "app_count": catalog.get("app_count", 0)})
            report("catalog_build", "ok", f"目录包含 {catalog.get('app_count', 0)} 个应用")
        except Exception as e:
            results["warnings"].append(f"应用目录构建失败: {e}")
            results["steps"].append({"name": "catalog_build", "status": "error"})
            report("catalog_build", "error", str(e))

        # 标记完成
        if not results["errors"]:
            self.mark_setup_completed()

        return results

    def _check_dependencies(self) -> List[str]:
        """检查依赖项。"""
        required = [
            ("pywinauto", "pywinauto"),
            ("psutil", "psutil"),
        ]
        optional = [
            ("win32gui", "pywin32"),
            ("comtypes", "comtypes"),
            ("six", "six"),
        ]

        missing = []
        for module_name, package_name in required + optional:
            try:
                __import__(module_name)
            except ImportError:
                if (module_name, package_name) in required:
                    missing.append(package_name)

        return missing

    def _scan_common_apps(self) -> List[Dict[str, Any]]:
        """扫描常用应用。"""
        detected = []

        # 常用应用及其典型路径
        common_apps = [
            {"name": "记事本", "exe": "notepad.exe", "paths": []},
            {"name": "计算器", "exe": "calc.exe", "paths": []},
            {"name": "画图", "exe": "mspaint.exe", "paths": []},
            {"name": "命令提示符", "exe": "cmd.exe", "paths": []},
            {"name": "PowerShell", "exe": "powershell.exe", "paths": []},
            {"name": "资源管理器", "exe": "explorer.exe", "paths": []},
            {"name": "Edge浏览器", "exe": "msedge.exe", "paths": ["C:\\Program Files (x86)\\Microsoft\\Edge\\Application"]},
            {"name": "Chrome浏览器", "exe": "chrome.exe", "paths": [
                "C:\\Program Files\\Google\\Chrome\\Application",
                "C:\\Program Files (x86)\\Google\\Chrome\\Application",
            ]},
            {"name": "VS Code", "exe": "code.exe", "paths": [
                "C:\\Program Files\\Microsoft VS Code",
                "C:\\Users\\%USERNAME%\\AppData\\Local\\Programs\\Microsoft VS Code",
            ]},
            {"name": "企业微信", "exe": "WXWork.exe", "paths": [
                "C:\\Program Files (x86)\\Tencent\\WXWork",
            ]},
            {"name": "微信", "exe": "WeChat.exe", "paths": [
                "C:\\Program Files (x86)\\Tencent\\WeChat",
            ]},
            {"name": "钉钉", "exe": "DingTalk.exe", "paths": [
                "C:\\Program Files (x86)\\DingDing",
            ]},
        ]

        from modules.desktop.desktop_discovery import resolve_executable

        for app in common_apps:
            exe_path = resolve_executable(app["exe"])
            if exe_path:
                detected.append({
                    "name": app["name"],
                    "exe": app["exe"],
                    "path": exe_path,
                    "source": "auto_detect",
                })

        return detected

    def _generate_recommended_config(self, detected_apps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成推荐配置。"""
        aliases = {}

        # 基于检测到的应用生成别名
        for app in detected_apps:
            name = app["name"]
            exe = app["exe"]
            path = app["path"]

            # 中文名作为别名
            if name and path:
                aliases[name] = path

            # exe名作为别名
            if exe and path:
                aliases[exe] = path
                stem = exe.replace(".exe", "")
                if stem:
                    aliases[stem] = path

        return {
            "version": self.SETUP_VERSION,
            "detected_at": str(Path.home()),
            "recommended_aliases": aliases,
            "user_editable": True,
        }

    def get_recommended_aliases(self) -> Dict[str, str]:
        """获取推荐的别名配置。"""
        return self._config.get("recommended_aliases", {})

    def apply_recommended_aliases_to_env(self, env_file: str = ".env") -> bool:
        """将推荐的别名应用到.env文件。"""
        try:
            aliases = self.get_recommended_aliases()
            if not aliases:
                return False

            env_path = self.root_dir / env_file

            # 读取现有内容
            existing_lines = []
            if env_path.exists():
                with open(env_path, "r", encoding="utf-8") as f:
                    existing_lines = f.readlines()

            # 查找或添加 DESKTOP_APP_ALIASES
            aliases_json = json.dumps(aliases, ensure_ascii=False, indent=2)
            aliases_line = f"DESKTOP_APP_ALIASES={aliases_json}\n"

            found = False
            new_lines = []
            for line in existing_lines:
                if line.strip().startswith("DESKTOP_APP_ALIASES="):
                    new_lines.append(aliases_line)
                    found = True
                else:
                    new_lines.append(line)

            if not found:
                new_lines.append("\n# 桌面自动化应用别名（自动生成）\n")
                new_lines.append(aliases_line)

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            return True

        except Exception as e:
            print(f"应用别名到.env失败: {e}")
            return False

    def get_setup_summary(self) -> str:
        """获取初始化摘要信息。"""
        if self.is_first_run():
            return "首次运行，尚未完成初始化"

        try:
            aliases = self.get_recommended_aliases()
            return f"初始化完成，已配置 {len(aliases)} 个应用别名"
        except Exception:
            return "初始化状态未知"


# 便捷函数
_auto_setup: Optional[AutoSetupManager] = None


def get_auto_setup() -> AutoSetupManager:
    """获取自动配置管理器实例。"""
    global _auto_setup
    if _auto_setup is None:
        _auto_setup = AutoSetupManager()
    return _auto_setup


def ensure_initialized(progress_callback: Optional[callable] = None) -> Dict[str, Any]:
    """
    确保系统已初始化。

    如果是首次运行，自动执行初始化流程。

    Args:
        progress_callback: 进度回调函数，接收 {"step", "status", "message"}

    Returns:
        初始化结果
    """
    setup = get_auto_setup()

    if not setup.is_first_run():
        return {"success": True, "first_run": False, "message": "系统已初始化"}

    return setup.run_auto_setup(progress_callback)


def get_quick_start_guide() -> str:
    """获取快速入门指南。"""
    guide = """
╔══════════════════════════════════════════════════════════════╗
║               桌面自动化平台 - 快速入门指南                    ║
╚══════════════════════════════════════════════════════════════╝

【零配置启动】
1. 无需手动配置，系统会自动扫描您电脑上已安装的应用
2. 输入程序名（如 notepad、chrome）即可启动应用
3. 支持中文别名（如"记事本"、"浏览器"）

【基本操作】
• 启动应用: 输入 "打开 记事本" 或 "启动 chrome"
• 附着窗口: 点击「选择当前窗口」或使用 "附着到 xxx"
• 点击控件: "点击 确定按钮" 或 "点击提交"
• 输入文本: "输入 [内容]" 或指定输入框后填写

【高级功能】
• 使用技能: 输入自然语言指令，系统自动匹配技能
• 文件整理: "整理下载文件夹" 自动按日期归档
• 窗口管理: "最大化窗口"、"置顶当前窗口"

【自定义配置】（可选）
如需自定义，可编辑 .env 文件:
  DESKTOP_APP_ALIASES={"erp":"C:\\\\ERP\\\\client.exe"}

更多帮助请查看文档或输入 "帮助"。
"""
    return guide


# 如果作为主模块运行，执行初始化
if __name__ == "__main__":
    def print_progress(info):
        print(f"[{info['status'].upper()}] {info['step']}: {info.get('message', '')}")

    result = ensure_initialized(print_progress)
    print("\n初始化结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
