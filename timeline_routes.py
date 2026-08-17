# -*- coding: utf-8 -*-
"""Frontend timeline visualization routes.

Provides API endpoints for timeline visualization and SSE event streaming.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


def register_timeline_routes(app: Any) -> None:
    """Register timeline visualization routes with Flask app."""
    
    @app.route('/api/timeline/formatter', methods=['GET'])
    def get_timeline_formatter_info():
        """Get timeline formatter information."""
        try:
            from timeline_formatter import (
                SEVERITY_INFO, SEVERITY_SUCCESS, SEVERITY_WARNING, SEVERITY_ERROR,
                CATEGORY_ROUTE, CATEGORY_TOOL, CATEGORY_OBSERVATION, 
                CATEGORY_ASSERTION, CATEGORY_HEAL, CATEGORY_RISK, CATEGORY_SYSTEM,
            )
            return {
                "ok": True,
                "severities": [SEVERITY_INFO, SEVERITY_SUCCESS, SEVERITY_WARNING, SEVERITY_ERROR],
                "categories": [CATEGORY_ROUTE, CATEGORY_TOOL, CATEGORY_OBSERVATION, 
                              CATEGORY_ASSERTION, CATEGORY_HEAL, CATEGORY_RISK, CATEGORY_SYSTEM],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.route('/api/timeline/icons', methods=['GET'])
    def get_timeline_icons():
        """Get available timeline icons."""
        return {
            "ok": True,
            "icons": {
                "route": "🚀",
                "agent": "🤖",
                "desktop": "🖥️",
                "mouse": "🖱️",
                "keyboard": "⌨️",
                "window": "🪟",
                "eye": "👁️",
                "camera": "📸",
                "api": "🌐",
                "document": "📝",
                "tool": "🔧",
                "search": "🔍",
                "success": "✅",
                "error": "❌",
                "warning": "⚠️",
                "info": "ℹ️",
                "alert": "🚨",
                "heal": "🔧",
            }
        }

    @app.route('/api/timeline/colors', methods=['GET'])
    def get_timeline_colors():
        """Get available timeline colors."""
        return {
            "ok": True,
            "colors": {
                "success": "#4CAF50",
                "error": "#F44336",
                "warning": "#FF9800",
                "info": "#2196F3",
                "debug": "#607D8B",
                "agent": "#9C27B0",
                "desktop": "#FF5722",
                "api": "#00BCD4",
            }
        }

    @app.route('/api/timeline/mock', methods=['GET'])
    def get_mock_timeline():
        """Get mock timeline data for testing."""
        return {
            "ok": True,
            "events": [
                {
                    "event_type": "route_decided",
                    "category": "route",
                    "severity": "info",
                    "title": "桌面快路径激活",
                    "description": "平台: desktop, 外层工具直接调用",
                    "timestamp": time.time() - 10,
                    "icon": "🖥️",
                    "color": "#4CAF50",
                },
                {
                    "event_type": "tool_call_start",
                    "category": "tool",
                    "severity": "info",
                    "title": "桌面操作: windows_click_element",
                    "description": "目标: 搜索按钮",
                    "timestamp": time.time() - 8,
                    "icon": "🖱️",
                    "color": "#FF5722",
                },
                {
                    "event_type": "tool_call_end",
                    "category": "tool",
                    "severity": "success",
                    "title": "工具完成: windows_click_element",
                    "description": "点击成功",
                    "timestamp": time.time() - 6,
                    "icon": "✅",
                    "color": "#4CAF50",
                },
                {
                    "event_type": "assertion_start",
                    "category": "assertion",
                    "severity": "info",
                    "title": "开始断言: db_scalar",
                    "description": "检查用户状态",
                    "timestamp": time.time() - 4,
                    "icon": "🔍",
                    "color": "#9C27B0",
                },
                {
                    "event_type": "assertion_end",
                    "category": "assertion",
                    "severity": "success",
                    "title": "断言通过: db_scalar",
                    "description": "用户状态正确",
                    "timestamp": time.time() - 2,
                    "icon": "✅",
                    "color": "#4CAF50",
                },
                {
                    "event_type": "done",
                    "category": "system",
                    "severity": "success",
                    "title": "执行完成",
                    "description": "使用了 3 个工具",
                    "timestamp": time.time(),
                    "icon": "✅",
                    "color": "#4CAF50",
                },
            ]
        }

    @app.route('/api/timeline/css', methods=['GET'])
    def get_timeline_css():
        """Get CSS for timeline visualization."""
        css = """
.timeline-container {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
}

.timeline-event {
    display: flex;
    align-items: flex-start;
    padding: 12px 0;
    border-left: 3px solid #e0e0e0;
    margin-left: 20px;
    position: relative;
}

.timeline-event::before {
    content: '';
    position: absolute;
    left: -8px;
    top: 16px;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--event-color, #2196F3);
}

.timeline-event:last-child {
    border-left-color: transparent;
}

.timeline-icon {
    font-size: 20px;
    margin-right: 12px;
    min-width: 30px;
    text-align: center;
}

.timeline-content {
    flex: 1;
}

.timeline-title {
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 4px;
    color: #333;
}

.timeline-description {
    font-size: 13px;
    color: #666;
    line-height: 1.4;
}

.timeline-time {
    font-size: 12px;
    color: #999;
    margin-top: 4px;
}

.severity-success .timeline-title { color: #2E7D32; }
.severity-error .timeline-title { color: #C62828; }
.severity-warning .timeline-title { color: #EF6C00; }
.severity-info .timeline-title { color: #1565C0; }
"""
        return css, 200, {'Content-Type': 'text/css'}
