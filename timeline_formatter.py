# -*- coding: utf-8 -*-
"""Timeline event formatter for SSE frontend rendering.

Converts standardized execution events into SSE-friendly payloads
with rich metadata for timeline visualization.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Timeline event severity levels
SEVERITY_INFO = "info"
SEVERITY_SUCCESS = "success"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"
SEVERITY_DEBUG = "debug"

# Timeline event categories
CATEGORY_ROUTE = "route"
CATEGORY_TOOL = "tool"
CATEGORY_OBSERVATION = "observation"
CATEGORY_ASSERTION = "assertion"
CATEGORY_HEAL = "heal"
CATEGORY_RISK = "risk"
CATEGORY_SYSTEM = "system"


@dataclass
class TimelineEvent:
    """Formatted timeline event for frontend rendering."""
    event_type: str
    category: str
    severity: str
    title: str
    description: str
    timestamp: float
    data: Dict[str, Any]
    icon: str = ""
    color: str = ""
    
    def to_sse_dict(self) -> Dict[str, Any]:
        """Convert to SSE-friendly dictionary."""
        return {
            "event_type": self.event_type,
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "timestamp": self.timestamp,
            "icon": self.icon,
            "color": self.color,
            "data": self.data,
        }


def format_route_decided(data: Dict[str, Any]) -> TimelineEvent:
    """Format route_decided event for timeline."""
    platform = data.get("platform", "unknown")
    allow_agent = data.get("allow_agent", False)
    use_outer_desktop = data.get("use_outer_desktop", False)
    
    if use_outer_desktop:
        title = f"桌面快路径激活"
        description = f"平台: {platform}, 外层工具直接调用"
        icon = "🖥️"
        color = "#4CAF50"
    elif allow_agent:
        title = f"智能体执行路径"
        description = f"平台: {platform}, Hermes 智能体就绪"
        icon = "🤖"
        color = "#2196F3"
    else:
        title = f"标准执行路径"
        description = f"平台: {platform}"
        icon = "🚀"
        color = "#FF9800"
    
    return TimelineEvent(
        event_type="route_decided",
        category=CATEGORY_ROUTE,
        severity=SEVERITY_INFO,
        title=title,
        description=description,
        timestamp=time.time(),
        data=data,
        icon=icon,
        color=color,
    )


def format_tool_call_start(data: Dict[str, Any]) -> TimelineEvent:
    """Format tool_call_start event for timeline."""
    tool = data.get("tool", "unknown")
    args_summary = data.get("args_summary", "")
    
    # Tool-specific formatting
    tool_icons = {
        "hermes_execute": "🤖",
        "windows_click_element": "🖱️",
        "windows_type_text": "⌨️",
        "windows_focus_app": "🪟",
        "get_screen_text": "👁️",
        "get_screen_description": "📸",
        "api_call": "🌐",
        "refine_test_plan": "📝",
    }
    
    tool_colors = {
        "hermes_execute": "#9C27B0",
        "windows_click_element": "#FF5722",
        "windows_type_text": "#FF9800",
        "windows_focus_app": "#4CAF50",
        "get_screen_text": "#00BCD4",
        "get_screen_description": "#00BCD4",
        "api_call": "#2196F3",
        "refine_test_plan": "#607D8B",
    }
    
    icon = tool_icons.get(tool, "🔧")
    color = tool_colors.get(tool, "#607D8B")
    
    # Build title
    if tool == "hermes_execute":
        title = "Hermes 智能体执行"
        description = f"目标: {args_summary[:80]}"
    elif tool.startswith("windows_"):
        title = f"桌面操作: {tool}"
        description = f"目标: {args_summary[:80]}"
    elif tool.startswith("mobile_"):
        title = f"手机操作: {tool}"
        description = f"目标: {args_summary[:80]}"
    elif tool == "api_call":
        title = "API 调用"
        description = f"请求: {args_summary[:80]}"
    elif tool == "refine_test_plan":
        title = "优化测试用例"
        description = "AI 正在优化测试用例"
    else:
        title = f"工具调用: {tool}"
        description = args_summary[:100]
    
    return TimelineEvent(
        event_type="tool_call_start",
        category=CATEGORY_TOOL,
        severity=SEVERITY_INFO,
        title=title,
        description=description,
        timestamp=time.time(),
        data=data,
        icon=icon,
        color=color,
    )


def format_tool_call_end(data: Dict[str, Any]) -> TimelineEvent:
    """Format tool_call_end event for timeline."""
    tool = data.get("tool", "unknown")
    result_preview = data.get("result_preview", "")
    
    # Determine success/failure
    is_success = True
    if '"success": false' in result_preview.lower() or '"ok": false' in result_preview.lower():
        is_success = False
    if "失败" in result_preview or "错误" in result_preview or "error" in result_preview.lower():
        is_success = False
    
    severity = SEVERITY_SUCCESS if is_success else SEVERITY_ERROR
    icon = "✅" if is_success else "❌"
    color = "#4CAF50" if is_success else "#F44336"
    
    title = f"工具完成: {tool}"
    if not is_success:
        title = f"工具失败: {tool}"
    
    # Truncate result for display
    description = result_preview[:120]
    if len(result_preview) > 120:
        description += "..."
    
    return TimelineEvent(
        event_type="tool_call_end",
        category=CATEGORY_TOOL,
        severity=severity,
        title=title,
        description=description,
        timestamp=time.time(),
        data=data,
        icon=icon,
        color=color,
    )


def format_done(data: Dict[str, Any]) -> TimelineEvent:
    """Format done event for timeline."""
    failed = data.get("failed", False)
    tools_used = data.get("tools_used", [])
    
    if failed:
        title = "执行失败"
        description = "任务执行失败，请检查错误信息"
        severity = SEVERITY_ERROR
        icon = "❌"
        color = "#F44336"
    else:
        title = "执行完成"
        description = f"使用了 {len(tools_used)} 个工具"
        severity = SEVERITY_SUCCESS
        icon = "✅"
        color = "#4CAF50"
    
    return TimelineEvent(
        event_type="done",
        category=CATEGORY_SYSTEM,
        severity=severity,
        title=title,
        description=description,
        timestamp=time.time(),
        data=data,
        icon=icon,
        color=color,
    )


def format_assertion_start(data: Dict[str, Any]) -> TimelineEvent:
    """Format assertion_start event for timeline."""
    assertion_type = data.get("assertion_type", "unknown")
    
    return TimelineEvent(
        event_type="assertion_start",
        category=CATEGORY_ASSERTION,
        severity=SEVERITY_INFO,
        title=f"开始断言: {assertion_type}",
        description=data.get("description", ""),
        timestamp=time.time(),
        data=data,
        icon="🔍",
        color="#9C27B0",
    )


def format_assertion_end(data: Dict[str, Any]) -> TimelineEvent:
    """Format assertion_end event for timeline."""
    assertion_type = data.get("assertion_type", "unknown")
    ok = data.get("ok", False)
    
    if ok:
        title = f"断言通过: {assertion_type}"
        severity = SEVERITY_SUCCESS
        icon = "✅"
        color = "#4CAF50"
    else:
        title = f"断言失败: {assertion_type}"
        severity = SEVERITY_ERROR
        icon = "❌"
        color = "#F44336"
    
    return TimelineEvent(
        event_type="assertion_end",
        category=CATEGORY_ASSERTION,
        severity=severity,
        title=title,
        description=data.get("message", ""),
        timestamp=time.time(),
        data=data,
        icon=icon,
        color=color,
    )


def format_heal_attempt(data: Dict[str, Any]) -> TimelineEvent:
    """Format heal_attempt event for timeline."""
    strategy = data.get("strategy", "unknown")
    platform = data.get("platform", "unknown")
    
    return TimelineEvent(
        event_type="heal_attempt",
        category=CATEGORY_HEAL,
        severity=SEVERITY_WARNING,
        title=f"自愈尝试: {strategy}",
        description=f"平台: {platform}, 策略: {strategy}",
        timestamp=time.time(),
        data=data,
        icon="🔧",
        color="#FF9800",
    )


def format_risk_decision(data: Dict[str, Any]) -> TimelineEvent:
    """Format risk_decision event for timeline."""
    risk_level = data.get("risk_level", "unknown")
    decision = data.get("decision", "unknown")
    
    if risk_level == "high":
        severity = SEVERITY_ERROR
        color = "#F44336"
        icon = "🚨"
    elif risk_level == "medium":
        severity = SEVERITY_WARNING
        color = "#FF9800"
        icon = "⚠️"
    else:
        severity = SEVERITY_INFO
        color = "#2196F3"
        icon = "ℹ️"
    
    return TimelineEvent(
        event_type="risk_decision",
        category=CATEGORY_RISK,
        severity=severity,
        title=f"风险决策: {risk_level}",
        description=f"决策: {decision}",
        timestamp=time.time(),
        data=data,
        icon=icon,
        color=color,
    )


def format_timeline_event(event_type: str, data: Dict[str, Any]) -> Optional[TimelineEvent]:
    """Format any execution event into a timeline event."""
    formatters = {
        "route_decided": format_route_decided,
        "tool_call_start": format_tool_call_start,
        "tool_call_end": format_tool_call_end,
        "done": format_done,
        "assertion_start": format_assertion_start,
        "assertion_end": format_assertion_end,
        "heal_attempt": format_heal_attempt,
        "risk_decision": format_risk_decision,
    }
    
    formatter = formatters.get(event_type)
    if formatter:
        return formatter(data)
    return None

