# -*- coding: utf-8 -*-
"""
模糊搜索匹配 - 自然语言到应用的智能匹配

提供功能：
1. 自然语言到应用程序的匹配（"记事本" → notepad.exe）
2. 语义化查询理解（中文关键词映射）
3. 用户习惯学习与智能排序
"""

from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

# 常见应用中文别名映射
_APP_NAME_ALIASES: Dict[str, List[str]] = {
    "notepad.exe": ["记事本", "文本编辑器", "文本文档", "note"],
    "notepad++.exe": ["notepad++", "npp", "增强记事本"],
    "calc.exe": ["计算器", "calc", "计算"],
    "mspaint.exe": ["画图", "画图工具", "paint", "绘图"],
    "explorer.exe": ["资源管理器", "文件管理器", "文件夹", "explorer"],
    "cmd.exe": ["命令提示符", "cmd", "终端", "命令行"],
    "powershell.exe": ["powershell", "ps", "脚本终端"],
    "msedge.exe": ["edge", "微软浏览器", "浏览器"],
    "chrome.exe": ["chrome", "谷歌浏览器", "谷歌"],
    "firefox.exe": ["firefox", "火狐", "火狐浏览器"],
    "wechat.exe": ["微信", "wechat"],
    "dingtalk.exe": ["钉钉", "dingtalk"],
    "wxwork.exe": ["企业微信", "企微", "work.weixin"],
    "winword.exe": ["word", "word文档", "微软word"],
    "excel.exe": ["excel", "表格", "电子表格"],
    "powerpnt.exe": ["powerpoint", "ppt", "演示文稿"],
    "outlook.exe": ["outlook", "邮箱", "邮件客户端"],
    "iexplore.exe": ["ie", "ie浏览器", "internet explorer"],
    "devenv.exe": ["visual studio", "vs", "ide"],
    "code.exe": ["vscode", "vs code", "code", "编辑器"],
    "pycharm64.exe": ["pycharm", "python ide"],
    "idea64.exe": ["idea", "intellij", "java ide"],
    "postman.exe": ["postman", "接口测试工具", "api工具"],
    "navicat.exe": ["navicat", "数据库工具"],
    "ssms.exe": ["sql server", "ssms", "sql管理工具"],
    "plsqldev.exe": ["plsql", "pl/sql", "oracle客户端"],
    "toad.exe": ["toad", "数据库开发工具"],
    "sqldeveloper.exe": ["sql developer", "oracle sql开发工具"],
    "eclipse.exe": ["eclipse", "java ide"],
    "studio64.exe": ["android studio", "as", "安卓开发工具"],
    "xcode.app": ["xcode", "mac开发工具", "ios开发"],
    "sublime_text.exe": ["sublime", "sublime text", "文本编辑器"],
    "atom.exe": ["atom", "atom编辑器"],
    "slack.exe": ["slack", "团队沟通工具"],
    "teams.exe": ["teams", "microsoft teams", "微软团队"],
    "zoom.exe": ["zoom", "视频会议", "在线会议"],
    "discord.exe": ["discord", "语音聊天"],
    "telegram.exe": ["telegram", "tg", "电报"],
    "skype.exe": ["skype", "视频通话"],
    "obs64.exe": ["obs", "录屏工具", "直播工具"],
    "steam.exe": ["steam", "游戏平台"],
    "epicgameslauncher.exe": ["epic", "epic games", "epic游戏平台"],
}

# 应用类别映射
_APP_CATEGORIES: Dict[str, List[str]] = {
    "浏览器": ["chrome.exe", "msedge.exe", "firefox.exe", "iexplore.exe"],
    "编辑器": ["notepad.exe", "notepad++.exe", "code.exe", "sublime_text.exe"],
    "办公软件": ["winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe"],
    "开发工具": ["devenv.exe", "code.exe", "pycharm64.exe", "idea64.exe"],
    "数据库工具": ["navicat.exe", "ssms.exe", "plsqldev.exe", "toad.exe"],
    "沟通工具": ["wechat.exe", "dingtalk.exe", "wxwork.exe", "teams.exe"],
    "终端工具": ["cmd.exe", "powershell.exe"],
}


class FuzzyAppMatcher:
    """应用模糊匹配器。"""

    def __init__(self):
        self._aliases = _APP_NAME_ALIASES.copy()
        self._categories = _APP_CATEGORIES.copy()
        self._user_history: Dict[str, int] = {}  # 用户选择历史
        self._load_user_history()

    def _load_user_history(self) -> None:
        """加载用户历史偏好。"""
        history_file = os.environ.get("DESKTOP_USER_HISTORY_FILE", "data/desktop_user_history.json")
        try:
            if os.path.isfile(history_file):
                with open(history_file, "r", encoding="utf-8") as f:
                    self._user_history = json.load(f)
        except Exception:
            self._user_history = {}

    def _save_user_history(self) -> None:
        """保存用户历史偏好。"""
        history_file = os.environ.get("DESKTOP_USER_HISTORY_FILE", "data/desktop_user_history.json")
        try:
            os.makedirs(os.path.dirname(history_file) or ".", exist_ok=True)
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(self._user_history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def record_selection(self, query: str, selected_exe: str) -> None:
        """记录用户选择，用于后续智能排序。"""
        key = f"{query.lower()}:{selected_exe.lower()}"
        self._user_history[key] = self._user_history.get(key, 0) + 1
        self._save_user_history()

    def find_matches(
        self,
        query: str,
        available_apps: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> List[Tuple[Dict[str, Any], float]]:
        """
        查找与查询最匹配的应用。

        Args:
            query: 用户输入的查询（如"记事本"、"打开浏览器"）
            available_apps: 可用的应用列表（来自应用目录）
            top_k: 返回前k个匹配结果

        Returns:
            按匹配分数排序的 (app_info, score) 列表
        """
        if not query or not available_apps:
            return []

        query_lower = query.lower().strip()

        # 去除常见的动词前缀
        prefixes = ["打开", "启动", "运行", "使用", "用"]
        for prefix in prefixes:
            if query_lower.startswith(prefix):
                query_lower = query_lower[len(prefix):].strip()
                break

        results: List[Tuple[Dict[str, Any], float]] = []

        for app in available_apps:
            score = self._calculate_match_score(query_lower, app)
            if score > 0.2:
                results.append((app, score))

        # 考虑用户历史偏好
        for i, (app, score) in enumerate(results):
            exe_name = (app.get("exe_name") or "").lower()
            key = f"{query_lower}:{exe_name}"
            if key in self._user_history:
                # 历史选择次数越多，分数加权越高
                boost = min(self._user_history[key] * 0.1, 0.3)
                results[i] = (app, score + boost)

        # 按分数降序排序
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def _calculate_match_score(self, query: str, app: Dict[str, Any]) -> float:
        """计算查询与应用的匹配分数。"""
        exe_name = (app.get("exe_name") or "").lower()
        display_name = (app.get("display_name") or "").lower()
        aliases = [a.lower() for a in (app.get("aliases") or [])]

        scores = []

        # 1. exe_name 精确匹配
        exe_stem = exe_name.replace(".exe", "")
        if query == exe_stem or query == exe_name:
            scores.append(1.0)

        # 2. 显示名称匹配
        if query == display_name:
            scores.append(0.95)
        elif query in display_name or display_name in query:
            scores.append(0.8)

        # 3. 别名匹配
        for alias in aliases:
            if query == alias:
                scores.append(0.9)
                break
            elif query in alias or alias in query:
                scores.append(0.7)
                break

        # 4. 已知中文别名映射
        for known_exe, known_aliases in self._aliases.items():
            if known_exe.lower() == exe_name:
                for known_alias in known_aliases:
                    if query == known_alias.lower():
                        scores.append(0.95)
                        break
                    elif query in known_alias.lower() or known_alias.lower() in query:
                        scores.append(0.75)
                        break

        # 5. 模糊字符串匹配（编辑距离）
        for text in [exe_stem, display_name] + aliases[:3]:
            if text:
                similarity = SequenceMatcher(None, query, text).ratio()
                if similarity > 0.6:
                    scores.append(similarity * 0.6)

        # 6. 类别关键词匹配
        for category, category_apps in self._categories.items():
            if query == category.lower() or category.lower() in query:
                if exe_name in [a.lower() for a in category_apps]:
                    scores.append(0.85)
                    break

        return max(scores) if scores else 0.0

    def suggest_queries(self, partial: str, available_apps: List[Dict[str, Any]], top_k: int = 5) -> List[str]:
        """
        基于部分输入提供查询建议（自动补全）。

        Args:
            partial: 用户已输入的部分文字
            available_apps: 可用应用列表
            top_k: 返回建议数量

        Returns:
            建议查询字符串列表
        """
        if not partial:
            return []

        partial_lower = partial.lower()
        suggestions: List[Tuple[str, float]] = []

        # 从别名映射中生成建议
        for exe_name, aliases in self._aliases.items():
            for alias in aliases:
                if partial_lower in alias.lower():
                    score = SequenceMatcher(None, partial_lower, alias.lower()).ratio()
                    suggestions.append((alias, score))

        # 从可用应用列表中生成建议
        for app in available_apps:
            display_name = app.get("display_name", "")
            aliases = app.get("aliases", [])
            for text in [display_name] + aliases:
                if text and partial_lower in text.lower():
                    score = SequenceMatcher(None, partial_lower, text.lower()).ratio()
                    suggestions.append((text, score))

        # 去重并排序
        seen = set()
        unique_suggestions: List[Tuple[str, float]] = []
        for text, score in sorted(suggestions, key=lambda x: x[1], reverse=True):
            text_lower = text.lower()
            if text_lower not in seen:
                seen.add(text_lower)
                unique_suggestions.append((text, score))

        return [text for text, _ in unique_suggestions[:top_k]]


class SemanticIntentParser:
    """语义意图解析器 - 理解用户的自然语言意图。"""

    # 常见的操作动词映射
    ACTION_VERBS: Dict[str, List[str]] = {
        "launch": ["打开", "启动", "运行", "开启", "使用", "用", "进入"],
        "close": ["关闭", "退出", "结束", "停止"],
        "attach": ["附着", "连接", "切换到", "转到", "选中"],
        "click": ["点击", "单击", "按下"],
        "double_click": ["双击", "双点"],
        "type": ["输入", "填写", "键入", "写入", "打字"],
        "select": ["选择", "选中", "勾选"],
        "scroll": ["滚动", "滑动", "下拉", "上拉"],
        "wait": ["等待", "暂停", "延时"],
    }

    # 控件类型关键词映射
    CONTROL_TYPE_KEYWORDS: Dict[str, List[str]] = {
        "Button": ["按钮", "btn", "button", "键", "确认", "提交", "取消", "保存"],
        "Edit": ["输入框", "文本框", "编辑框", "textbox", "input", "edit", "字段"],
        "ComboBox": ["下拉框", "下拉菜单", "选择框", "combobox", "dropdown"],
        "CheckBox": ["复选框", "勾选框", "checkbox", "多选框"],
        "RadioButton": ["单选框", "单选按钮", "radiobutton"],
        "List": ["列表", "列表框", "list", "listbox"],
        "Menu": ["菜单", "menu", "导航"],
        "Tree": ["树", "树形", "tree", "目录树"],
        "Tab": ["标签页", "选项卡", "tab", "页签"],
        "Window": ["窗口", "窗体", "window", "对话框", "dialog"],
    }

    def parse_intent(self, user_input: str) -> Dict[str, Any]:
        """
        解析用户输入的自然语言意图。

        Returns:
            {
                "action": "launch|close|attach|click|type|...",
                "target_app": "应用名称或别名",
                "target_control": {
                    "type": "控件类型",
                    "name": "控件名称",
                    "keywords": ["关键词列表"],
                },
                "parameters": {...},
                "confidence": 0.0-1.0,
            }
        """
        input_lower = user_input.lower().strip()

        result = {
            "action": None,
            "target_app": None,
            "target_control": None,
            "parameters": {},
            "confidence": 0.0,
            "raw_input": user_input,
        }

        # 1. 识别操作意图
        for action, verbs in self.ACTION_VERBS.items():
            for verb in verbs:
                if verb in input_lower:
                    result["action"] = action
                    result["confidence"] += 0.3
                    break
            if result["action"]:
                break

        # 2. 识别目标应用（去除动词后的第一个名词短语）
        if result["action"]:
            # 简单的启发式：动词后面的内容作为目标
            for verb in self.ACTION_VERBS.get(result["action"], []):
                if verb in input_lower:
                    # 找到动词位置，取后面的文本
                    idx = input_lower.find(verb)
                    if idx >= 0:
                        remainder = user_input[idx + len(verb):].strip()
                        # 提取第一个作为应用名称（假设是前几个词）
                        words = remainder.split()
                        if words:
                            # 如果后面跟着"的"，说明是控件
                            if "的" in words[0] or (len(words) > 1 and words[1].startswith("的")):
                                # 这是控件，前面可能有应用名
                                result["target_control"] = {"name": remainder}
                            else:
                                result["target_app"] = words[0]
                                # 剩余部分可能是控件
                                if len(words) > 1:
                                    result["target_control"] = {"name": " ".join(words[1:])}
                    break

        # 3. 识别控件类型
        if result.get("target_control"):
            control_name = result["target_control"].get("name", "")
            for control_type, keywords in self.CONTROL_TYPE_KEYWORDS.items():
                for keyword in keywords:
                    if keyword in control_name.lower():
                        result["target_control"]["type"] = control_type
                        result["confidence"] += 0.2
                        break

        # 4. 提取额外参数（简单启发式）
        # 提取引号中的内容作为输入值
        import re
        quoted = re.findall(r'[""]([^""]+)[""]', user_input)
        if quoted:
            result["parameters"]["input_value"] = quoted[0]

        # 提取数字作为等待时间
        numbers = re.findall(r'\d+', user_input)
        if numbers and result["action"] == "wait":
            result["parameters"]["seconds"] = int(numbers[0])

        # 确保confidence在0-1范围内
        result["confidence"] = min(result["confidence"], 1.0)

        return result

    def suggest_next_action(self, current_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        基于当前上下文建议下一步操作。

        例如：
        - 刚刚启动了应用 → 建议等待或查找主窗口
        - 刚刚点击了输入框 → 建议输入内容
        """
        suggestions = []
        last_action = current_context.get("last_action")

        if last_action == "launch":
            suggestions.append({
                "action": "wait",
                "description": "等待应用启动完成",
                "parameters": {"seconds": 2},
            })
            suggestions.append({
                "action": "attach",
                "description": "附着到应用主窗口",
                "parameters": {},
            })

        elif last_action == "attach":
            suggestions.append({
                "action": "wait",
                "description": "等待窗口就绪",
                "parameters": {"seconds": 1},
            })

        elif last_action == "click" and current_context.get("target_control_type") == "Edit":
            suggestions.append({
                "action": "type",
                "description": "在输入框中输入内容",
                "parameters": {"input_value": "[请输入内容]"},
            })

        return suggestions


# 全局实例
_fuzzy_matcher = FuzzyAppMatcher()
_intent_parser = SemanticIntentParser()


def find_apps_by_query(
    query: str,
    available_apps: List[Dict[str, Any]],
    top_k: int = 5,
) -> List[Tuple[Dict[str, Any], float]]:
    """
    根据查询查找匹配的应用。
    """
    return _fuzzy_matcher.find_matches(query, available_apps, top_k)


def parse_user_intent(user_input: str) -> Dict[str, Any]:
    """
    解析用户输入的意图。
    """
    return _intent_parser.parse_intent(user_input)


def suggest_queries(partial: str, available_apps: List[Dict[str, Any]], top_k: int = 5) -> List[str]:
    """
    提供查询建议。
    """
    return _fuzzy_matcher.suggest_queries(partial, available_apps, top_k)


def suggest_next_actions(current_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    建议下一步操作。
    """
    return _intent_parser.suggest_next_action(current_context)


def record_user_selection(query: str, selected_exe: str) -> None:
    """
    记录用户选择，用于后续智能排序。
    """
    _fuzzy_matcher.record_selection(query, selected_exe)
