# -*- coding: utf-8 -*-
"""
桌面自动化智能API - 统一入口

整合所有功能模块，提供简洁的高层API：
1. 零配置应用启动
2. 自然语言控制
3. 技能自动路由
4. 运行时状态感知

使用示例:
    from desktop_intelligent_api import DesktopAgent

    agent = DesktopAgent()
    agent.initialize()

    # 自然语言指令
    result = agent.execute("打开记事本")
    result = agent.execute("点击新建按钮")
    result = agent.execute("输入 Hello World")
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, List, Optional

# 导入所有模块
from desktop_skill_framework import SkillContext, SkillResult, get_global_registry
from desktop_fuzzy_search import find_apps_by_query, parse_user_intent, suggest_queries
from desktop_runtime_snapshot import capture_window_snapshot, get_snapshot_summary
from desktop_auto_setup import ensure_initialized, get_quick_start_guide

# 确保内置技能已注册
import desktop_builtin_skills


class DesktopAgent:
    """
    桌面自动化智能代理。

    提供简洁的高层接口，整合所有自动化功能。
    """

    def __init__(self):
        self._context = SkillContext()
        self._registry = get_global_registry()
        self._initialized = False
        self._on_progress: Optional[Callable[[str], None]] = None

    def set_progress_callback(self, callback: Callable[[str], None]) -> None:
        """设置进度回调函数。"""
        self._on_progress = callback

    def initialize(self, auto_setup: bool = True) -> Dict[str, Any]:
        """
        初始化代理。

        Args:
            auto_setup: 是否自动执行首次配置

        Returns:
            初始化结果
        """
        result = {"success": True, "steps": []}

        # 1. 环境检查
        if sys.platform != "win32":
            result["steps"].append({"name": "platform_check", "status": "warning", "message": "非Windows平台，功能受限"})
        else:
            result["steps"].append({"name": "platform_check", "status": "ok"})

        # 2. 自动配置
        if auto_setup:
            setup_result = ensure_initialized(
                progress_callback=lambda info: self._notify(f"初始化: {info['step']}")
            )
            result["setup"] = setup_result
            result["steps"].append({"name": "auto_setup", "status": "ok" if setup_result["success"] else "error"})

        # 3. 加载技能
        skill_count = len(self._registry.list_skills())
        result["steps"].append({"name": "load_skills", "status": "ok", "skill_count": skill_count})

        self._initialized = True
        result["success"] = all(step.get("status") != "error" for step in result["steps"])
        return result

    def _notify(self, message: str) -> None:
        """发送进度通知。"""
        if self._on_progress:
            self._on_progress(message)

    def execute(self, command: str, **kwargs) -> SkillResult:
        """
        执行自然语言指令。

        Args:
            command: 自然语言指令（如"打开记事本"）
            **kwargs: 额外的上下文参数

        Returns:
            SkillResult 执行结果
        """
        if not self._initialized:
            return SkillResult.failure("代理未初始化，请先调用 initialize()")

        self._notify(f"解析指令: {command}")

        # 更新上下文
        for key, value in kwargs.items():
            self._context.set_variable(key, value)

        # 解析意图
        intent = parse_user_intent(command)
        self._context.parsed_intent = intent

        self._notify(f"识别意图: {intent.get('action', 'unknown')}")

        # 自动路由执行
        result = self._registry.auto_route_and_execute(
            command,
            self._context,
            on_progress=self._on_progress,
        )

        return result

    def find_app(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        查找应用程序。

        Args:
            query: 应用名称或描述
            top_k: 返回结果数量

        Returns:
            匹配的应用列表
        """
        try:
            from desktop_app_catalog import list_catalog_apps
            apps = list_catalog_apps()
            matches = find_apps_by_query(query, apps, top_k)
            return [
                {
                    "name": app.get("display_name", ""),
                    "exe": app.get("exe_name", ""),
                    "path": app.get("path", ""),
                    "confidence": score,
                    "aliases": app.get("aliases", []),
                }
                for app, score in matches
            ]
        except Exception as e:
            return [{"error": str(e)}]

    def get_suggestions(self, partial: str, top_k: int = 5) -> List[str]:
        """
        获取输入建议。

        Args:
            partial: 部分输入
            top_k: 建议数量

        Returns:
            建议列表
        """
        try:
            from desktop_app_catalog import list_catalog_apps
            apps = list_catalog_apps()
            return suggest_queries(partial, apps, top_k)
        except Exception:
            return []

    def capture_state(self) -> Dict[str, Any]:
        """
        捕获当前窗口状态。

        Returns:
            窗口状态摘要
        """
        if not self._context.window:
            return {"error": "未附着窗口"}

        snapshot = capture_window_snapshot(self._context.window)
        return get_snapshot_summary(snapshot)

    def attach_to_window(self, **spec) -> SkillResult:
        """
        附着到指定窗口。

        Args:
            **spec: 窗口规格（title, process, pid, hwnd等）

        Returns:
            SkillResult
        """
        for key, value in spec.items():
            self._context.set_variable(key, value)

        return self._registry.execute_skill(
            "attach_window",
            self._context,
            on_progress=self._on_progress,
        )

    def launch_app(self, app_query: str, **kwargs) -> SkillResult:
        """
        启动应用程序。

        Args:
            app_query: 应用名称/路径/别名
            **kwargs: 额外参数

        Returns:
            SkillResult
        """
        self._context.set_variable("app_query", app_query)
        for key, value in kwargs.items():
            self._context.set_variable(key, value)

        return self._registry.execute_skill(
            "launch_app",
            self._context,
            on_progress=self._on_progress,
        )

    def click(self, **kwargs) -> SkillResult:
        """
        点击控件。

        Args:
            **kwargs: 定位参数（control_name, automation_id, fuzzy_query等）

        Returns:
            SkillResult
        """
        for key, value in kwargs.items():
            self._context.set_variable(key, value)

        return self._registry.execute_skill(
            "click_control",
            self._context,
            on_progress=self._on_progress,
        )

    def type_text(self, text: str, **kwargs) -> SkillResult:
        """
        输入文本。

        Args:
            text: 要输入的文本
            **kwargs: 额外参数

        Returns:
            SkillResult
        """
        self._context.set_variable("text", text)
        for key, value in kwargs.items():
            self._context.set_variable(key, value)

        return self._registry.execute_skill(
            "type_text",
            self._context,
            on_progress=self._on_progress,
        )

    def organize_files(self, folder: str, **kwargs) -> SkillResult:
        """
        整理文件。

        Args:
            folder: 要整理的文件夹路径
            **kwargs: 额外参数

        Returns:
            SkillResult
        """
        self._context.set_variable("source_folder", folder)
        for key, value in kwargs.items():
            self._context.set_variable(key, value)

        return self._registry.execute_skill(
            "organize_files",
            self._context,
            on_progress=self._on_progress,
        )

    def wait(self, seconds: int) -> SkillResult:
        """
        等待。

        Args:
            seconds: 等待秒数

        Returns:
            SkillResult
        """
        self._context.set_variable("seconds", seconds)

        return self._registry.execute_skill(
            "wait",
            self._context,
            on_progress=self._on_progress,
        )

    def get_help(self) -> str:
        """获取帮助信息。"""
        return get_quick_start_guide()

    def list_available_skills(self) -> List[Dict[str, Any]]:
        """列出所有可用技能。"""
        return self._registry.list_skills()

    def get_status(self) -> Dict[str, Any]:
        """获取代理状态。"""
        return {
            "initialized": self._initialized,
            "window_attached": self._context.window is not None,
            "app_attached": self._context.app is not None,
            "variables": list(self._context.variables.keys()),
            "action_history_count": len(self._context.action_history),
            "available_skills": len(self._registry.list_skills()),
        }


# 便捷函数
_agent: Optional[DesktopAgent] = None


def get_agent() -> DesktopAgent:
    """获取全局代理实例。"""
    global _agent
    if _agent is None:
        _agent = DesktopAgent()
    return _agent


def quick_start() -> str:
    """快速开始。"""
    agent = get_agent()
    agent.initialize()
    return agent.get_help()


def run(command: str, **kwargs) -> SkillResult:
    """
    快速执行指令。

    示例:
        run("打开记事本")
        run("点击确定")
        run("输入 Hello World")
    """
    agent = get_agent()
    if not agent.get_status()["initialized"]:
        agent.initialize()
    return agent.execute(command, **kwargs)


# 如果作为主模块运行，展示示例
if __name__ == "__main__":
    print("桌面自动化智能API - 示例运行")
    print("=" * 60)

    def on_progress(msg):
        print(f"  > {msg}")

    agent = DesktopAgent()
    agent.set_progress_callback(on_progress)

    # 初始化
    print("\n1. 初始化...")
    init_result = agent.initialize()
    print(f"初始化结果: {init_result}")

    # 显示帮助
    print("\n2. 快速入门指南:")
    print(agent.get_help())

    # 列出技能
    print("\n3. 可用技能:")
    for skill in agent.list_available_skills():
        print(f"  - {skill['skill_name']} ({skill['skill_id']})")

    # 查找应用
    print("\n4. 查找应用 'note':")
    apps = agent.find_app("note", top_k=3)
    for app in apps:
        print(f"  - {app.get('name', 'unknown')} ({app.get('confidence', 0):.2f})")

    print("\n5. 代理状态:")
    print(agent.get_status())
