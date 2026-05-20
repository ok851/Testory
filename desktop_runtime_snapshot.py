# -*- coding: utf-8 -*-
"""
运行时窗口状态快照 - 动态感知层

提供功能：
1. 窗口控件树实时快照（缓存控件层次结构）
2. 相似控件智能匹配（基于控件的多个属性）
3. 运行时上下文感知（记录当前活跃窗口状态）
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Callable

_DESKTOP_AVAILABLE = sys.platform == "win32"
if _DESKTOP_AVAILABLE:
    try:
        from pywinauto import Application  # type: ignore
        from pywinauto.controls.uiawrapper import UIAWrapper  # type: ignore
    except ImportError:
        _DESKTOP_AVAILABLE = False
        Application = None  # type: ignore
        UIAWrapper = None  # type: ignore

# 全局快照缓存
_snapshot_cache: Dict[str, Any] = {}
_snapshot_lock = threading.RLock()
_snapshot_ttl_sec = float(os.environ.get("DESKTOP_SNAPSHOT_TTL_SEC", "30") or "30")


@dataclass
class ControlNode:
    """控件节点信息。"""
    automation_id: str = ""
    name: str = ""
    control_type: str = ""
    class_name: str = ""
    rect: Tuple[int, int, int, int] = field(default_factory=lambda: (0, 0, 0, 0))
    children: List["ControlNode"] = field(default_factory=list)
    depth: int = 0
    index: int = 0  # 在同层级中的索引

    def to_dict(self) -> Dict[str, Any]:
        return {
            "automation_id": self.automation_id,
            "name": self.name,
            "control_type": self.control_type,
            "class_name": self.class_name,
            "rect": self.rect,
            "depth": self.depth,
            "index": self.index,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ControlNode":
        node = cls(
            automation_id=data.get("automation_id", ""),
            name=data.get("name", ""),
            control_type=data.get("control_type", ""),
            class_name=data.get("class_name", ""),
            rect=tuple(data.get("rect", [0, 0, 0, 0])),
            depth=data.get("depth", 0),
            index=data.get("index", 0),
        )
        node.children = [cls.from_dict(c) for c in data.get("children", [])]
        return node

    def flatten(self) -> List["ControlNode"]:
        """将树扁平化为列表。"""
        result = [self]
        for child in self.children:
            result.extend(child.flatten())
        return result


@dataclass
class WindowSnapshot:
    """窗口快照信息。"""
    hwnd: int = 0
    title: str = ""
    process_name: str = ""
    process_path: str = ""
    pid: int = 0
    captured_at: float = 0.0
    root: Optional[ControlNode] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "process_name": self.process_name,
            "process_path": self.process_path,
            "pid": self.pid,
            "captured_at": self.captured_at,
            "root": self.root.to_dict() if self.root else None,
        }


class RuntimeSnapshotManager:
    """运行时快照管理器。"""

    def __init__(self):
        self._snapshots: Dict[str, WindowSnapshot] = {}
        self._lock = threading.RLock()

    def capture_window(
        self,
        window: Any,
        max_depth: int = 5,
        max_children: int = 50,
    ) -> WindowSnapshot:
        """
        捕获窗口控件树快照。

        Args:
            window: pywinauto窗口对象或wrapper
            max_depth: 最大遍历深度
            max_children: 每个节点最大子节点数
        """
        if not _DESKTOP_AVAILABLE or window is None:
            return WindowSnapshot()

        try:
            wrapper = window.wrapper_object() if hasattr(window, "wrapper_object") else window

            # 获取窗口基本信息
            hwnd = wrapper.handle if hasattr(wrapper, "handle") else 0
            title = wrapper.window_text() if hasattr(wrapper, "window_text") else ""

            # 获取进程信息
            pid = 0
            process_name = ""
            process_path = ""
            try:
                import psutil
                _, pid = wrapper.element.GetCurrentPropertyValue(30002)  # UIA_ProcessIdPropertyId
                if pid:
                    proc = psutil.Process(pid)
                    process_name = proc.name()
                    process_path = proc.exe()
            except Exception:
                pass

            # 构建控件树
            root = self._build_control_tree(wrapper, max_depth, max_children)

            snapshot = WindowSnapshot(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                process_path=process_path,
                pid=pid,
                captured_at=time.time(),
                root=root,
            )

            # 缓存快照
            cache_key = f"{process_name}_{hwnd}"
            with self._lock:
                self._snapshots[cache_key] = snapshot

            return snapshot

        except Exception as e:
            return WindowSnapshot(captured_at=time.time())

    def _build_control_tree(
        self,
        element: Any,
        max_depth: int,
        max_children: int,
        current_depth: int = 0,
    ) -> Optional[ControlNode]:
        """递归构建控件树。"""
        if current_depth >= max_depth:
            return None

        try:
            node = ControlNode(depth=current_depth)

            # 获取控件属性
            if hasattr(element, "automation_id"):
                node.automation_id = element.automation_id() or ""
            if hasattr(element, "window_text"):
                node.name = element.window_text() or ""
            if hasattr(element, "friendly_class_name"):
                node.control_type = element.friendly_class_name() or ""
            if hasattr(element, "class_name"):
                node.class_name = element.class_name() or ""
            if hasattr(element, "rectangle"):
                rect = element.rectangle()
                if rect:
                    node.rect = (rect.left, rect.top, rect.right, rect.bottom)

            # 获取子控件
            if hasattr(element, "children") and current_depth < max_depth - 1:
                children = element.children()
                limited_children = children[:max_children]
                for idx, child in enumerate(limited_children):
                    child_node = self._build_control_tree(
                        child, max_depth, max_children, current_depth + 1
                    )
                    if child_node:
                        child_node.index = idx
                        node.children.append(child_node)

            return node

        except Exception:
            return None

    def get_snapshot(self, key: str) -> Optional[WindowSnapshot]:
        """获取缓存的快照。"""
        with self._lock:
            snapshot = self._snapshots.get(key)
            if snapshot and (time.time() - snapshot.captured_at) < _snapshot_ttl_sec:
                return snapshot
            return None

    def list_cached_keys(self) -> List[str]:
        """列出所有缓存的键。"""
        with self._lock:
            return list(self._snapshots.keys())

    def clear_cache(self) -> None:
        """清除所有缓存。"""
        with self._lock:
            self._snapshots.clear()


# 全局管理器实例
_snapshot_manager = RuntimeSnapshotManager()


def capture_window_snapshot(
    window: Any,
    max_depth: int = 5,
    max_children: int = 50,
) -> WindowSnapshot:
    """
    捕获窗口快照的便捷函数。
    """
    return _snapshot_manager.capture_window(window, max_depth, max_children)


def get_cached_snapshot(key: str) -> Optional[WindowSnapshot]:
    """获取缓存的快照。"""
    return _snapshot_manager.get_snapshot(key)


def find_similar_control(
    reference: ControlNode,
    snapshot: WindowSnapshot,
    match_threshold: float = 0.6,
) -> Optional[ControlNode]:
    """
    在快照中查找与参考控件最相似的控件。

    相似度评分考虑：
    - automation_id 完全匹配（权重最高）
    - control_type 匹配
    - name 相似度（编辑距离）
    - class_name 匹配
    - 相对位置相似性
    """
    if not snapshot or not snapshot.root:
        return None

    candidates = snapshot.root.flatten()
    best_match: Optional[ControlNode] = None
    best_score = 0.0

    for candidate in candidates:
        score = _calculate_similarity(reference, candidate)
        if score > best_score and score >= match_threshold:
            best_score = score
            best_match = candidate

    return best_match


def _calculate_similarity(a: ControlNode, b: ControlNode) -> float:
    """计算两个控件节点的相似度（0-1）。"""
    weights = {
        "automation_id": 0.4,
        "control_type": 0.25,
        "class_name": 0.2,
        "name": 0.15,
    }

    scores = {}

    # automation_id 完全匹配
    if a.automation_id and b.automation_id:
        scores["automation_id"] = 1.0 if a.automation_id == b.automation_id else 0.0
    else:
        scores["automation_id"] = 0.0

    # control_type 匹配
    if a.control_type and b.control_type:
        scores["control_type"] = 1.0 if a.control_type == b.control_type else 0.0
    else:
        scores["control_type"] = 0.0

    # class_name 匹配
    if a.class_name and b.class_name:
        scores["class_name"] = 1.0 if a.class_name == b.class_name else 0.0
    else:
        scores["class_name"] = 0.0

    # name 相似度（简单的包含匹配）
    if a.name and b.name:
        a_lower = a.name.lower()
        b_lower = b.name.lower()
        if a_lower == b_lower:
            scores["name"] = 1.0
        elif a_lower in b_lower or b_lower in a_lower:
            scores["name"] = 0.7
        else:
            scores["name"] = 0.0
    else:
        scores["name"] = 0.0

    # 计算加权总分
    total_score = sum(scores[k] * weights[k] for k in weights)
    return total_score


def find_control_by_fuzzy_match(
    snapshot: WindowSnapshot,
    query: str,
    control_type_hint: Optional[str] = None,
) -> List[Tuple[ControlNode, float]]:
    """
    基于自然语言查询模糊查找控件。

    Args:
        query: 查询字符串（如"确定按钮"、"用户名输入框"）
        control_type_hint: 控件类型提示（如"Button"、"Edit"）

    Returns:
        按匹配分数排序的 (ControlNode, score) 列表
    """
    if not snapshot or not snapshot.root:
        return []

    query_lower = query.lower().strip()
    candidates = snapshot.root.flatten()
    results: List[Tuple[ControlNode, float]] = []

    # 解析查询中的关键词
    keywords = set(query_lower.split())
    # 常见控件类型映射
    type_hints = {
        "按钮": ["button", "push button"],
        "输入框": ["edit", "text"],
        "文本框": ["edit", "text"],
        "下拉框": ["combobox", "combo box"],
        "列表": ["list", "listbox"],
        "复选框": ["checkbox"],
        "单选框": ["radiobutton"],
        "标签": ["text", "label"],
    }

    for candidate in candidates:
        score = 0.0

        # 名称匹配
        if candidate.name:
            name_lower = candidate.name.lower()
            # 完全匹配
            if query_lower == name_lower:
                score += 1.0
            # 包含匹配
            elif query_lower in name_lower or name_lower in query_lower:
                score += 0.8
            # 关键词匹配
            else:
                name_words = set(name_lower.split())
                keyword_matches = len(keywords & name_words)
                score += 0.5 * (keyword_matches / max(len(keywords), 1))

        # 控件类型匹配
        if control_type_hint and candidate.control_type:
            hint_lower = control_type_hint.lower()
            type_lower = candidate.control_type.lower()
            if hint_lower == type_lower:
                score += 0.3
            elif hint_lower in type_lower or type_lower in hint_lower:
                score += 0.2

        # 检查是否有中文类型提示词
        for hint_cn, type_list in type_hints.items():
            if hint_cn in query_lower:
                for t in type_list:
                    if t in candidate.control_type.lower():
                        score += 0.25
                        break

        if score > 0.2:
            results.append((candidate, score))

    # 按分数降序排序
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def get_snapshot_summary(snapshot: WindowSnapshot) -> Dict[str, Any]:
    """获取快照摘要信息。"""
    if not snapshot or not snapshot.root:
        return {"error": "无有效快照"}

    all_nodes = snapshot.root.flatten()

    # 统计控件类型
    type_counts: Dict[str, int] = {}
    for node in all_nodes:
        t = node.control_type or "Unknown"
        type_counts[t] = type_counts.get(t, 0) + 1

    return {
        "title": snapshot.title,
        "process": snapshot.process_name,
        "pid": snapshot.pid,
        "hwnd": snapshot.hwnd,
        "captured_at": snapshot.captured_at,
        "control_count": len(all_nodes),
        "depth": max((n.depth for n in all_nodes), default=0),
        "control_types": dict(sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:10]),
    }


def save_snapshot_to_file(snapshot: WindowSnapshot, filepath: str) -> bool:
    """将快照保存到JSON文件。"""
    try:
        data = snapshot.to_dict()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_snapshot_from_file(filepath: str) -> Optional[WindowSnapshot]:
    """从JSON文件加载快照。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        snapshot = WindowSnapshot(
            hwnd=data.get("hwnd", 0),
            title=data.get("title", ""),
            process_name=data.get("process_name", ""),
            process_path=data.get("process_path", ""),
            pid=data.get("pid", 0),
            captured_at=data.get("captured_at", 0),
        )
        if data.get("root"):
            snapshot.root = ControlNode.from_dict(data["root"])
        return snapshot
    except Exception:
        return None
