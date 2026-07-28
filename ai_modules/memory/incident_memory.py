# -*- coding: utf-8 -*-
"""IncidentMemory / Runbook 轻量检索（R15）。

不强制向量库：默认关键词/令牌重叠检索，落盘 JSONL。
若 ``LOCAL_MEMORY_ENABLE=1`` 且 Ollama 可用，可额外写入 ``ai_memory_store``（可选增强）。

诚实约束：检索命中仅为「建议」，不得据此自动判绿。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)

# 内置 runbook 种子（可被文件覆盖/追加）
_SEED_RUNBOOKS: List[Dict[str, Any]] = [
    {
        "id": "rb-desktop-attach",
        "kind": "runbook",
        "tags": ["desktop", "attach_window", "DESKTOP_NO_SESSION", "DESKTOP_SOFT_FAIL"],
        "title": "桌面窗口附着失败",
        "body": (
            "确认 Desktop Gateway 已启动；放宽 window_title_re（去掉 ^$ 锚点）；"
            "检查 DESKTOP_APP_ALIASES / @erp；开启 DESKTOP_RUNTIME_HEAL。"
        ),
        "error_codes": ["DESKTOP_NO_SESSION", "DESKTOP_SOFT_FAIL", "DESKTOP_STEP_FAILED"],
    },
    {
        "id": "rb-hitl-timeout",
        "kind": "runbook",
        "tags": ["hitl", "timeout"],
        "title": "HITL 超时",
        "body": (
            "人机门禁超时必须失败，不得假绿。请在跨端页继续/取消，或增大 timeout_s；"
            "流水线场景勿依赖无人值守 HITL。"
        ),
        "error_codes": ["HITL_TIMEOUT", "HITL_CANCELLED"],
    },
    {
        "id": "rb-assert-mismatch",
        "kind": "runbook",
        "tags": ["assert", "cross_end", "vars"],
        "title": "跨端断言/变量不匹配",
        "body": (
            "核对上游 extract/store_as 是否写入变量；下游 {{var}} 是否为空；"
            "mismatch 场景应诚实失败。检查断言源与脱敏字段。"
        ),
        "error_codes": ["CROSS_END_ASSERT_FAILED", "VAR_EXTRACT_MISSING"],
    },
    {
        "id": "rb-lock-busy",
        "kind": "runbook",
        "tags": ["lock", "execution"],
        "title": "执行锁占用",
        "body": "本机执行锁 busy：等待前序用例结束，或错峰调度；ImportError 不得静默绕过锁。",
        "error_codes": ["LOCK_BUSY", "EXECUTION_LOCK_UNAVAILABLE"],
    },
]


def _data_dir() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2] / "data"
    d = root / "incident_memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store_path() -> Path:
    return _data_dir() / "incidents.jsonl"


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) > 1]


def _score(query_tokens: List[str], doc: Dict[str, Any]) -> float:
    if not query_tokens:
        return 0.0
    blob = " ".join(
        [
            str(doc.get("title") or ""),
            str(doc.get("body") or ""),
            " ".join(doc.get("tags") or []),
            " ".join(doc.get("error_codes") or []),
            str(doc.get("error_code") or ""),
            str(doc.get("error_message") or ""),
            str(doc.get("layer") or ""),
        ]
    ).lower()
    doc_tokens = set(_tokenize(blob))
    if not doc_tokens:
        return 0.0
    hit = sum(1 for t in query_tokens if t in doc_tokens)
    # error_code 精确命中加权
    codes = {str(c).upper() for c in (doc.get("error_codes") or [])}
    ec = str(doc.get("error_code") or "").upper()
    if ec:
        codes.add(ec)
    for t in query_tokens:
        if t.upper() in codes:
            hit += 2
    return float(hit) / float(len(query_tokens) + 1)


def _load_all() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = [dict(x) for x in _SEED_RUNBOOKS]
    path = _store_path()
    if not path.is_file():
        return rows
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("id"):
                rows.append(obj)
    except OSError:
        pass
    return rows


def record_incident(
    *,
    error_code: str = "",
    error_message: str = "",
    layer: str = "",
    title: str = "",
    body: str = "",
    tags: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
    kind: str = "incident",
) -> Dict[str, Any]:
    """追加一条事故/经验记录（JSONL）。"""
    rec = {
        "id": f"inc-{uuid.uuid4().hex[:12]}",
        "kind": kind or "incident",
        "title": (title or error_code or "incident")[:120],
        "body": (body or error_message or "")[:2000],
        "error_code": (error_code or "")[:64],
        "error_message": (error_message or "")[:500],
        "error_codes": [error_code] if error_code else [],
        "layer": (layer or "")[:32],
        "tags": list(tags or []),
        "meta": dict(meta or {}),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _LOCK:
        path = _store_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # 可选向量增强（失败静默）
    try:
        if (os.environ.get("LOCAL_MEMORY_ENABLE") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            from ai_memory_store import ingest

            ingest(
                user_id=0,
                kind="incident",
                source_text=f"{rec['title']}\n{rec['body']}\n{rec['error_code']}",
                meta={"incident_id": rec["id"], "error_code": rec["error_code"]},
            )
    except Exception:
        pass
    return rec


def search_incidents(
    query: str,
    *,
    limit: int = 5,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """轻量检索：关键词重叠；返回带 score 的副本。"""
    q = (query or "").strip()
    tokens = _tokenize(q)
    lim = max(1, min(int(limit or 5), 20))
    scored: List[Dict[str, Any]] = []
    for doc in _load_all():
        if kind and str(doc.get("kind") or "") != kind:
            continue
        sc = _score(tokens, doc)
        if sc <= 0 and not tokens:
            continue
        if sc <= 0:
            continue
        item = dict(doc)
        item["score"] = round(sc, 4)
        scored.append(item)
    scored.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return scored[:lim]


def search_runbooks(query: str, *, limit: int = 5) -> List[Dict[str, Any]]:
    """RunbookRag 叙事入口：仅 kind=runbook。"""
    return search_incidents(query, limit=limit, kind="runbook")


def suggest_for_failure(
    *,
    error_code: str = "",
    error_message: str = "",
    layer: str = "",
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """供 Verifier/Planner：失败时取建议（不得当成功依据）。"""
    q = " ".join(
        [
            str(error_code or ""),
            str(layer or ""),
            str(error_message or "")[:200],
        ]
    ).strip()
    hits = search_incidents(q, limit=limit)
    # 若无命中，再只按 error_code 搜 runbook
    if not hits and error_code:
        hits = search_runbooks(error_code, limit=limit)
    return hits


def remember_verifier_failure(state: Any) -> Optional[Dict[str, Any]]:
    """从 TestRunState / report 写入一条 incident（失败时）。"""
    if state is None:
        return None
    status = str(getattr(state, "status", "") or "")
    if status == "success":
        return None
    report = getattr(state, "report", None) or {}
    execution = getattr(state, "execution", None) or {}
    err = (
        (report.get("reason") if isinstance(report, dict) else None)
        or execution.get("error")
        or (getattr(state, "errors", None) or [None])[-1]
        or ""
    )
    code = str(execution.get("error_code") or "")
    return record_incident(
        error_code=code,
        error_message=str(err),
        layer="cross_end",
        title=f"AgentTeams fail: {code or 'unknown'}",
        body=str(err),
        tags=["agent_teams", "verifier"],
        meta={"run_id": getattr(state, "run_id", "")},
    )
