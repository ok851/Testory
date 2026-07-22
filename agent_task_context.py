# -*- coding: utf-8 -*-
"""跨端任务上下文总线：vars / artifacts / surface 句柄，供 Hermes 与平台共享。"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskContext:
    session_id: str
    vars: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    active_surface: str = "auto"  # web|desktop|mobile|api|auto
    desktop_session_id: str = "default"
    mobile_udid: str = ""
    hermes_session_id: str = ""
    hitl_pending: Optional[Dict[str, Any]] = None
    tool_trace: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)

    def set_var(self, key: str, value: Any) -> None:
        if key:
            self.vars[str(key)] = value

    def get_var(self, key: str, default: Any = None) -> Any:
        return self.vars.get(key, default)

    def add_artifact(self, kind: str, path_or_text: str, **extra: Any) -> None:
        item = {"kind": kind, "value": path_or_text, "ts": time.time()}
        item.update(extra)
        self.artifacts.append(item)
        if len(self.artifacts) > 80:
            self.artifacts = self.artifacts[-80:]

    def append_trace(self, tool: str, summary: str, ok: bool = True, **extra: Any) -> None:
        row = {"tool": tool, "summary": (summary or "")[:400], "ok": ok, "ts": time.time()}
        row.update(extra)
        self.tool_trace.append(row)
        if len(self.tool_trace) > 200:
            self.tool_trace = self.tool_trace[-200:]

    def request_hitl(self, reason: str, hint: str = "") -> Dict[str, Any]:
        self.hitl_pending = {
            "reason": reason,
            "hint": hint,
            "requested_at": time.time(),
            "id": uuid.uuid4().hex[:12],
        }
        return dict(self.hitl_pending)

    def clear_hitl(self) -> None:
        self.hitl_pending = None

    def instruction_prefix(self) -> str:
        """注入 Hermes user instruction 的上下文前缀。

        注意：不要写成 Hermes 可解析的 `[session_id=…]` 恢复令牌；
        平台 task id 仅作上下文说明，避免触发 Hermes 内部损坏会话。
        """
        surface = (self.active_surface or "auto").strip().lower()
        lines = [
            f"【Testory 任务上下文 task_id={self.session_id}】",
            f"active_surface={surface}",
        ]
        if surface == "web":
            lines.append("本任务为网页自动化：只用 browser_*，禁止 windows_* / skill_view / terminal 探环境。")
        elif surface == "desktop":
            lines.append(f"desktop_session_id={self.desktop_session_id}")
            lines.append("本任务为桌面 GUI：优先 MCP windows_* / get_screen_*。")
        else:
            lines.append(f"desktop_session_id={self.desktop_session_id}")
        if self.mobile_udid:
            lines.append(f"mobile_udid={self.mobile_udid}")
        if self.vars:
            preview = {k: str(v)[:120] for k, v in list(self.vars.items())[:20]}
            lines.append("共享变量 vars=" + str(preview))
        if self.artifacts:
            kinds = [a.get("kind") for a in self.artifacts[-5:]]
            lines.append(f"最近产物 artifacts_kinds={kinds}")
        if self.hitl_pending:
            lines.append(
                "【人机接管待处理】"
                + str(self.hitl_pending.get("reason") or "")
                + " — 等待用户完成后继续"
            )
        if surface == "web":
            lines.append(
                "网页规则：当前标签页内操作；禁止新开空白标签；"
                "已在目标 URL 则禁止 navigate；优先 DOM 控件清单 click/type；"
                "browser_snapshot 仅难定位兜底（DOM ref，非视觉）。"
            )
        elif surface == "desktop":
            lines.append(
                "桌面规则：优先 UIA/结构化感知；视觉为辅；未核验勿声称已输入。"
            )
        else:
            lines.append(
                "跨端规则：可用时优先结构化感知（DOM/UIA/dump）；视觉为辅。"
                "Web 遇 OS 弹窗时切桌面 gateway；需要接口校验时调用 testory-api-http。"
                "高风险写操作前先 inspect/只读确认。将可复用结果写入 vars。"
            )
        return "\n".join(lines) + "\n\n"

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "vars": dict(self.vars),
            "artifact_count": len(self.artifacts),
            "active_surface": self.active_surface,
            "desktop_session_id": self.desktop_session_id,
            "mobile_udid": self.mobile_udid,
            "hermes_session_id": self.hermes_session_id,
            "hitl_pending": dict(self.hitl_pending) if self.hitl_pending else None,
            "trace_count": len(self.tool_trace),
        }


_LOCK = threading.RLock()
_STORE: Dict[str, TaskContext] = {}


def new_task_context(
    *,
    session_id: str = "",
    active_surface: str = "auto",
    desktop_session_id: str = "",
    mobile_udid: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> TaskContext:
    sid = (session_id or "").strip() or uuid.uuid4().hex
    ctx = TaskContext(
        session_id=sid,
        active_surface=(active_surface or "auto").strip().lower() or "auto",
        desktop_session_id=(desktop_session_id or "").strip() or sid[:16],
        mobile_udid=(mobile_udid or "").strip(),
        hermes_session_id="",  # 默认不复用 Hermes 内部会话；损坏时再 reset
        meta=dict(meta or {}),
    )
    with _LOCK:
        _STORE[sid] = ctx
        if len(_STORE) > 64:
            # 丢弃最旧
            oldest = sorted(_STORE.values(), key=lambda c: c.created_at)[: max(0, len(_STORE) - 48)]
            for o in oldest:
                _STORE.pop(o.session_id, None)
    return ctx


def get_task_context(session_id: str) -> Optional[TaskContext]:
    with _LOCK:
        return _STORE.get((session_id or "").strip())


def reset_task_context(session_id: str) -> TaskContext:
    """会话损坏时换新 hermes_session_id，保留 vars。"""
    old = get_task_context(session_id)
    vars_keep = dict(old.vars) if old else {}
    surface = old.active_surface if old else "auto"
    desk = old.desktop_session_id if old else ""
    udid = old.mobile_udid if old else ""
    ctx = new_task_context(
        session_id=session_id or uuid.uuid4().hex,
        active_surface=surface,
        desktop_session_id=desk,
        mobile_udid=udid,
    )
    ctx.vars.update(vars_keep)
    ctx.hermes_session_id = uuid.uuid4().hex
    ctx.meta["reset_from_corruption"] = True
    return ctx
