# -*- coding: utf-8 -*-
"""
桌面自动化内置技能

提供常用的原子化技能实现：
1. LaunchAppSkill - 启动应用
2. AttachWindowSkill - 附着窗口
3. ClickControlSkill - 点击控件
4. TypeTextSkill - 输入文本
5. FileOrganizeSkill - 文件整理
6. WindowManagerSkill - 窗口管理
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 导入技能框架
from desktop_skill_framework import DesktopSkill, SkillContext, SkillResult, SkillStatus, register_skill

# 尝试导入桌面自动化模块
if sys.platform == "win32":
    try:
        from desktop_discovery import resolve_executable, resolve_executable_with_meta, format_resolve_error
        from desktop_locator import attach_application, resolve_control, parse_desktop_spec
        from desktop_fuzzy_search import find_apps_by_query, parse_user_intent
        from desktop_runtime_snapshot import capture_window_snapshot, find_control_by_fuzzy_match
    except ImportError:
        resolve_executable = None
        attach_application = None
        resolve_control = None


class LaunchAppSkill(DesktopSkill):
    """
    启动应用程序技能。

    支持：
    - 程序名/路径自动解析
    - 模糊搜索匹配
    - 自然语言意图解析
    """

    skill_id = "launch_app"
    skill_name = "启动应用"
    skill_description = "根据程序名、路径或自然语言描述启动应用程序"
    skill_version = "1.0.0"
    skill_tags = ["core", "launch", "app"]

    intent_patterns = [
        "启动 *",
        "打开 *",
        "运行 *",
        "开启 *",
        "使用 *",
    ]

    required_params = ["app_query"]
    optional_params = {
        "backend": "uia",
        "timeout": 30,
        "wait_for_window": True,
    }

    def can_handle(self, context: SkillContext) -> bool:
        """检查是否能处理该上下文。"""
        intent = context.parsed_intent
        if intent.get("action") == "launch":
            return True
        user_input = context.user_input.lower()
        launch_verbs = ["打开", "启动", "运行", "开启", "使用"]
        return any(verb in user_input for verb in launch_verbs)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行应用启动。"""
        if not resolve_executable:
            return SkillResult.failure("桌面自动化模块不可用")

        # 获取应用查询字符串
        app_query = self.get_param(context, "app_query")
        if not app_query:
            # 尝试从意图中解析
            intent = parse_user_intent(context.user_input)
            app_query = intent.get("target_app", "")

        if not app_query:
            return SkillResult.failure("未指定要启动的应用程序")

        # 尝试解析为可执行路径
        resolved_path = resolve_executable(app_query)

        if not resolved_path:
            # 尝试模糊搜索
            try:
                from desktop_app_catalog import list_catalog_apps
                apps = list_catalog_apps()
                matches = find_apps_by_query(app_query, apps, top_k=3)
                if matches:
                    best_match, score = matches[0]
                    resolved_path = best_match.get("path", "")
                    context.set_variable("matched_app", best_match)
                    context.set_variable("match_score", score)
            except Exception:
                pass

        if not resolved_path:
            meta = resolve_executable_with_meta(app_query)
            return SkillResult.failure(format_resolve_error(meta))

        # 启动应用
        try:
            backend = self.get_param(context, "backend", "uia")
            timeout = self.get_param(context, "timeout", 30)

            desktop_spec = {
                "path": resolved_path,
                "backend": backend,
                "timeout": timeout,
            }

            app, window = attach_application(desktop_spec)

            # 保存到上下文
            context.app = app
            context.window = window
            context.set_variable("app_path", resolved_path)
            context.set_variable("app_name", os.path.basename(resolved_path))

            context.record_action("launch_app", {"path": resolved_path, "success": True})

            return SkillResult.success(
                message=f"成功启动应用: {os.path.basename(resolved_path)}",
                data={
                    "app_path": resolved_path,
                    "window_title": getattr(window, "window_text", lambda: "")(),
                }
            )

        except Exception as e:
            return SkillResult.failure(f"启动应用失败: {str(e)}", error=e)


class AttachWindowSkill(DesktopSkill):
    """
    附着到已有窗口技能。

    支持通过多种方式附着：
    - 窗口标题
    - 进程名
    - PID
    - 窗口句柄
    """

    skill_id = "attach_window"
    skill_name = "附着窗口"
    skill_description = "附着到已运行的应用程序窗口"
    skill_tags = ["core", "attach", "window"]

    intent_patterns = [
        "附着 *",
        "连接到 *",
        "切换到 *",
        "选中 * 窗口",
    ]

    required_params = []
    optional_params = {
        "window_title": "",
        "window_title_re": "",
        "process": "",
        "pid": None,
        "hwnd": None,
        "backend": "uia",
        "timeout": 25,
    }

    def can_handle(self, context: SkillContext) -> bool:
        """检查是否能处理该上下文。"""
        intent = context.parsed_intent
        if intent.get("action") == "attach":
            return True
        user_input = context.user_input.lower()
        attach_verbs = ["附着", "连接", "切换到", "转到"]
        return any(verb in user_input for verb in attach_verbs)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行窗口附着。"""
        if not attach_application:
            return SkillResult.failure("桌面自动化模块不可用")

        # 构建 desktop_spec
        desktop_spec = {}

        for key in ["window_title", "window_title_re", "process", "pid", "hwnd", "backend", "timeout"]:
            value = self.get_param(context, key)
            if value:
                desktop_spec[key] = value

        # 如果没有指定任何附着条件，尝试使用用户输入
        if not desktop_spec:
            intent = parse_user_intent(context.user_input)
            target = intent.get("target_app", "")
            if target:
                desktop_spec["window_title_re"] = target

        if not desktop_spec:
            return SkillResult.failure("未指定窗口附着条件（标题、进程名、PID等）")

        try:
            app, window = attach_application(desktop_spec)

            context.app = app
            context.window = window

            context.record_action("attach_window", {"spec": desktop_spec, "success": True})

            return SkillResult.success(
                message="成功附着到窗口",
                data={
                    "window_title": getattr(window, "window_text", lambda: "")(),
                    "spec": desktop_spec,
                }
            )

        except Exception as e:
            return SkillResult.failure(f"附着窗口失败: {str(e)}", error=e)


class ClickControlSkill(DesktopSkill):
    """
    点击控件技能。

    支持多种定位方式：
    - 控件名称
    - 自动化ID
    - 控件类型
    - 坐标
    - 模糊匹配
    """

    skill_id = "click_control"
    skill_name = "点击控件"
    skill_description = "点击窗口中的指定控件"
    skill_tags = ["core", "click", "control"]

    intent_patterns = [
        "点击 *",
        "单击 *",
        "按下 * 按钮",
        "选择 *",
    ]

    required_params = []
    optional_params = {
        "control_name": "",
        "automation_id": "",
        "control_type": "",
        "coordinates": None,  # (x, y) tuple
        "fuzzy_query": "",
        "double_click": False,
    }

    def can_handle(self, context: SkillContext) -> bool:
        """检查是否能处理该上下文。"""
        intent = context.parsed_intent
        if intent.get("action") == "click":
            return True
        user_input = context.user_input.lower()
        click_verbs = ["点击", "单击", "按下", "选择"]
        return any(verb in user_input for verb in click_verbs)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行控件点击。"""
        if not resolve_control:
            return SkillResult.failure("桌面自动化模块不可用")

        window = context.window
        if not window:
            return SkillResult.failure("未附着窗口，请先执行附着窗口或启动应用")

        try:
            control = None

            # 1. 尝试精确匹配
            automation_id = self.get_param(context, "automation_id")
            control_name = self.get_param(context, "control_name")
            control_type = self.get_param(context, "control_type")

            if automation_id:
                control = resolve_control(window, "automation_id", automation_id)
            elif control_name:
                control = resolve_control(window, "name", control_name)
            elif control_type:
                control = resolve_control(window, "control_type", control_type)

            # 2. 尝试模糊匹配
            if not control:
                fuzzy_query = self.get_param(context, "fuzzy_query")
                if fuzzy_query:
                    # 捕获快照并模糊查找
                    snapshot = capture_window_snapshot(window)
                    from desktop_runtime_snapshot import find_control_by_fuzzy_match, ControlNode
                    matches = find_control_by_fuzzy_match(snapshot, fuzzy_query)
                    if matches:
                        best_match, score = matches[0]
                        # 使用找到的控件属性进行定位
                        if best_match.automation_id:
                            control = resolve_control(window, "automation_id", best_match.automation_id)
                        elif best_match.name:
                            control = resolve_control(window, "name", best_match.name)

            # 3. 尝试坐标点击
            if not control:
                coordinates = self.get_param(context, "coordinates")
                if coordinates:
                    x, y = coordinates
                    window.click_input(coords=(x, y))
                    context.record_action("click_control", {"coordinates": coordinates})
                    return SkillResult.success(message=f"在坐标 ({x}, {y}) 执行点击")

            if not control:
                return SkillResult.failure("未找到要点击的控件")

            # 执行点击
            double_click = self.get_param(context, "double_click", False)
            if double_click:
                control.double_click_input()
            else:
                control.click_input()

            context.record_action("click_control", {
                "control_name": getattr(control, "window_text", lambda: "")(),
                "double_click": double_click,
            })

            return SkillResult.success(message="成功点击控件")

        except Exception as e:
            return SkillResult.failure(f"点击控件失败: {str(e)}", error=e)


class TypeTextSkill(DesktopSkill):
    """
    输入文本技能。
    """

    skill_id = "type_text"
    skill_name = "输入文本"
    skill_description = "在输入框中输入文本内容"
    skill_tags = ["core", "type", "input"]

    intent_patterns = [
        "输入 *",
        "填写 *",
        "键入 *",
        "写入 *",
    ]

    required_params = ["text"]
    optional_params = {
        "control_name": "",
        "automation_id": "",
        "clear_first": True,
        "interval": 0.01,
    }

    def can_handle(self, context: SkillContext) -> bool:
        """检查是否能处理该上下文。"""
        intent = context.parsed_intent
        if intent.get("action") == "type":
            return True
        user_input = context.user_input.lower()
        type_verbs = ["输入", "填写", "键入", "写入"]
        return any(verb in user_input for verb in type_verbs)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行文本输入。"""
        window = context.window
        if not window:
            return SkillResult.failure("未附着窗口")

        text = self.get_param(context, "text")
        if not text:
            return SkillResult.failure("未指定要输入的文本")

        try:
            control = window

            # 如果指定了控件，先定位
            control_name = self.get_param(context, "control_name")
            automation_id = self.get_param(context, "automation_id")

            if control_name or automation_id:
                if not resolve_control:
                    return SkillResult.failure("控件定位模块不可用")

                if automation_id:
                    control = resolve_control(window, "automation_id", automation_id)
                elif control_name:
                    control = resolve_control(window, "name", control_name)

            # 清空已有内容（如果需要）
            clear_first = self.get_param(context, "clear_first", True)
            if clear_first and hasattr(control, "set_text"):
                control.set_text("")

            # 输入文本
            interval = self.get_param(context, "interval", 0.01)
            if hasattr(control, "type_keys"):
                control.type_keys(text, with_spaces=True, pause=interval)
            elif hasattr(control, "set_text"):
                control.set_text(text)

            context.record_action("type_text", {"text_length": len(text)})

            return SkillResult.success(
                message=f"成功输入 {len(text)} 个字符",
                data={"text_length": len(text)}
            )

        except Exception as e:
            return SkillResult.failure(f"输入文本失败: {str(e)}", error=e)


class FileOrganizeSkill(DesktopSkill):
    """
    文件整理技能。

    自动整理指定文件夹中的文件，按日期或类型分类。
    """

    skill_id = "organize_files"
    skill_name = "整理文件"
    skill_description = "自动整理文件夹中的文件，按日期或类型分类存放"
    skill_version = "1.0.0"
    skill_tags = ["file", "organize", "automation"]

    intent_patterns = [
        "整理 * 文件夹",
        "整理 * 目录",
        "清理 * 文件",
        "归档 * 文件",
        "组织 * 文件",
    ]

    required_params = ["source_folder"]
    optional_params = {
        "organize_by": "date",  # date, type, both
        "date_format": "%Y-%m",  # 按年月分组
        "dry_run": True,  # 默认试运行，不实际移动文件
        "include_patterns": ["*"],
        "exclude_patterns": ["*.tmp", "~$*"],  # 排除临时文件
    }

    def can_handle(self, context: SkillContext) -> bool:
        """检查是否能处理该上下文。"""
        intent = context.parsed_intent
        if intent.get("action") in ["organize", "cleanup"]:
            return True
        user_input = context.user_input.lower()
        return any(keyword in user_input for keyword in ["整理", "清理", "归档", "组织"])

    def execute(self, context: SkillContext) -> SkillResult:
        """执行文件整理。"""
        source_folder = self.get_param(context, "source_folder")
        if not source_folder or not os.path.isdir(source_folder):
            return SkillResult.failure(f"无效的源文件夹: {source_folder}")

        organize_by = self.get_param(context, "organize_by", "date")
        date_format = self.get_param(context, "date_format", "%Y-%m")
        dry_run = self.get_param(context, "dry_run", True)
        include_patterns = self.get_param(context, "include_patterns", ["*"])
        exclude_patterns = self.get_param(context, "exclude_patterns", ["*.tmp", "~$*"])

        try:
            source_path = Path(source_folder)
            operations = []
            stats = {"scanned": 0, "to_move": 0, "errors": 0}

            # 扫描文件
            for pattern in include_patterns:
                for file_path in source_path.glob(pattern):
                    if not file_path.is_file():
                        continue

                    # 检查排除模式
                    excluded = False
                    for exclude in exclude_patterns:
                        if file_path.match(exclude):
                            excluded = True
                            break
                    if excluded:
                        continue

                    stats["scanned"] += 1

                    # 确定目标文件夹
                    if organize_by == "date":
                        mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                        target_folder = source_path / mtime.strftime(date_format)
                    elif organize_by == "type":
                        ext = file_path.suffix.lower()
                        if ext:
                            target_folder = source_path / ext[1:].upper()
                        else:
                            target_folder = source_path / "NO_EXTENSION"
                    else:
                        target_folder = source_path / "Organized"

                    target_path = target_folder / file_path.name

                    # 检查是否已存在
                    if target_path.exists():
                        # 添加时间戳后缀
                        stem = target_path.stem
                        suffix = target_path.suffix
                        timestamp = datetime.now().strftime("%H%M%S")
                        target_path = target_folder / f"{stem}_{timestamp}{suffix}"

                    operations.append({
                        "source": str(file_path),
                        "target": str(target_path),
                        "target_folder": str(target_folder),
                    })
                    stats["to_move"] += 1

            # 执行移动（如果不是试运行）
            if not dry_run:
                for op in operations:
                    try:
                        os.makedirs(op["target_folder"], exist_ok=True)
                        shutil.move(op["source"], op["target"])
                    except Exception as e:
                        stats["errors"] += 1
                        op["error"] = str(e)

            context.record_action("organize_files", {
                "scanned": stats["scanned"],
                "moved": stats["to_move"],
                "dry_run": dry_run,
            })

            message = f"扫描了 {stats['scanned']} 个文件，计划移动 {stats['to_move']} 个文件"
            if dry_run:
                message += "（试运行模式，未实际移动）"
            elif stats["errors"] > 0:
                message += f"，{stats['errors']} 个错误"

            return SkillResult.success(
                message=message,
                data={
                    "operations": operations[:50],  # 只返回前50个操作
                    "stats": stats,
                    "dry_run": dry_run,
                }
            )

        except Exception as e:
            return SkillResult.failure(f"整理文件失败: {str(e)}", error=e)


class WindowManagerSkill(DesktopSkill):
    """
    窗口管理技能。

    提供窗口操作：
    - 最大化/最小化/还原
    - 调整大小
    - 移动位置
    - 置顶/取消置顶
    """

    skill_id = "manage_window"
    skill_name = "窗口管理"
    skill_description = "管理窗口状态（最大化、最小化、移动、调整大小等）"
    skill_tags = ["window", "management"]

    intent_patterns = [
        "最大化 *",
        "最小化 *",
        "还原 *",
        "移动 * 窗口",
        "调整 * 大小",
    ]

    required_params = ["operation"]
    optional_params = {
        "window_title": "",
        "width": None,
        "height": None,
        "x": None,
        "y": None,
    }

    def can_handle(self, context: SkillContext) -> bool:
        """检查是否能处理该上下文。"""
        user_input = context.user_input.lower()
        window_ops = ["最大化", "最小化", "还原", "置顶", "移动", "调整"]
        return any(op in user_input for op in window_ops)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行窗口管理操作。"""
        window = context.window
        if not window:
            return SkillResult.failure("未附着窗口")

        operation = self.get_param(context, "operation")
        if not operation:
            # 从用户输入解析
            user_input = context.user_input.lower()
            if "最大化" in user_input:
                operation = "maximize"
            elif "最小化" in user_input:
                operation = "minimize"
            elif "还原" in user_input or "恢复" in user_input:
                operation = "restore"
            elif "置顶" in user_input:
                operation = "topmost"
            else:
                return SkillResult.failure("未指定窗口操作")

        try:
            if operation == "maximize":
                if hasattr(window, "maximize"):
                    window.maximize()
                return SkillResult.success("窗口已最大化")

            elif operation == "minimize":
                if hasattr(window, "minimize"):
                    window.minimize()
                return SkillResult.success("窗口已最小化")

            elif operation == "restore":
                if hasattr(window, "restore"):
                    window.restore()
                return SkillResult.success("窗口已还原")

            elif operation == "topmost":
                # 置顶窗口
                if sys.platform == "win32":
                    import ctypes
                    HWND_TOPMOST = -1
                    HWND_NOTOPMOST = -2
                    SWP_SHOWWINDOW = 0x40
                    hwnd = window.handle if hasattr(window, "handle") else 0
                    if hwnd:
                        ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_SHOWWINDOW)
                return SkillResult.success("窗口已置顶")

            elif operation == "resize":
                width = self.get_param(context, "width")
                height = self.get_param(context, "height")
                if width and height and hasattr(window, "move_window"):
                    x, y = window.rectangle().left, window.rectangle().top
                    window.move_window(x=x, y=y, width=width, height=height)
                    return SkillResult.success(f"窗口已调整为 {width}x{height}")

            elif operation == "move":
                x = self.get_param(context, "x")
                y = self.get_param(context, "y")
                if x is not None and y is not None and hasattr(window, "move_window"):
                    rect = window.rectangle()
                    window.move_window(x=x, y=y, width=rect.width(), height=rect.height())
                    return SkillResult.success(f"窗口已移动到 ({x}, {y})")

            return SkillResult.failure(f"不支持的操作: {operation}")

        except Exception as e:
            return SkillResult.failure(f"窗口操作失败: {str(e)}", error=e)


class WaitSkill(DesktopSkill):
    """
    等待技能。
    """

    skill_id = "wait"
    skill_name = "等待"
    skill_description = "等待指定的时间或等待某个条件满足"
    skill_tags = ["core", "wait"]

    intent_patterns = [
        "等待 * 秒",
        "暂停 *",
        "延时 *",
    ]

    required_params = ["seconds"]
    optional_params = {
        "condition": None,  # 等待条件（函数）
        "timeout": 30,
    }

    def can_handle(self, context: SkillContext) -> bool:
        """检查是否能处理该上下文。"""
        intent = context.parsed_intent
        if intent.get("action") == "wait":
            return True
        user_input = context.user_input.lower()
        return any(keyword in user_input for keyword in ["等待", "暂停", "延时", "sleep"])

    def execute(self, context: SkillContext) -> SkillResult:
        """执行等待。"""
        seconds = self.get_param(context, "seconds")
        if seconds is None:
            # 从用户输入解析数字
            import re
            numbers = re.findall(r'\d+', context.user_input)
            if numbers:
                seconds = int(numbers[0])
            else:
                seconds = 1

        try:
            time.sleep(seconds)
            context.record_action("wait", {"seconds": seconds})
            return SkillResult.success(f"等待了 {seconds} 秒")
        except Exception as e:
            return SkillResult.failure(f"等待失败: {str(e)}", error=e)


# 自动注册所有内置技能
def register_builtin_skills():
    """注册所有内置技能到全局注册表。"""
    skills = [
        LaunchAppSkill,
        AttachWindowSkill,
        ClickControlSkill,
        TypeTextSkill,
        FileOrganizeSkill,
        WindowManagerSkill,
        WaitSkill,
    ]

    for skill_class in skills:
        register_skill(skill_class)

    return len(skills)


# 模块加载时自动注册
if __name__ != "__main__":
    try:
        count = register_builtin_skills()
        print(f"已注册 {count} 个内置技能")
    except Exception as e:
        print(f"注册内置技能失败: {e}")
