"""
本地向量记忆：Ollama /api/embeddings + SQLite 存储 + 余弦 Top-K，供生成/修复提示增强。
环境：LOCAL_MEMORY_ENABLE=1，LOCAL_EMBED_MODEL（默认 nomic-embed-text）。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import requests
from requests.exceptions import RequestException

from database import Database

_MAX_QUERY_CHARS = 4000
_MAX_SOURCE_CHARS = 8000
_MAX_BLOCK_CHARS = 3500


def _env_bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def memory_enabled() -> bool:
    return _env_bool("LOCAL_MEMORY_ENABLE", False)


def _base_url() -> str:
    return (os.environ.get("LOCAL_LLM_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def _embed_model() -> str:
    return (os.environ.get("LOCAL_EMBED_MODEL") or "nomic-embed-text").strip() or "nomic-embed-text"


def _timeout() -> int:
    raw = (os.environ.get("LOCAL_EMBED_TIMEOUT") or "").strip()
    if raw.isdigit():
        return int(raw)
    return 120


def embed_text(text: str) -> np.ndarray:
    """
    调用 Ollama POST /api/embeddings，返回 float32 一维向量。
    """
    t = (text or "").strip()
    if not t:
        raise ValueError("empty text for embedding")
    if len(t) > _MAX_SOURCE_CHARS:
        t = t[: _MAX_SOURCE_CHARS - 1] + "…"
    url = f"{_base_url()}/api/embeddings"
    model = _embed_model()
    last_err: Optional[Exception] = None
    for key in ("prompt", "input"):
        try:
            payload: Dict[str, Any] = {"model": model, key: t}
            resp = requests.post(url, json=payload, timeout=_timeout())
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            emb = data.get("embedding")
            if emb is None and isinstance(data.get("embeddings"), list) and data["embeddings"]:
                emb = data["embeddings"][0]
            if isinstance(emb, list) and emb:
                return np.array(emb, dtype=np.float32)
        except (RequestException, ValueError, TypeError) as e:
            last_err = e
            continue
    raise ValueError(
        f"Ollama embeddings 失败（模型 {_embed_model()} 是否已 ollama pull？）: {last_err}"
    )


def _to_blob(vec: np.ndarray) -> Tuple[bytes, int]:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    return v.tobytes(), int(v.shape[0])


def _from_blob(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32, count=dim).copy()


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    an = float(np.linalg.norm(a))
    bn = float(np.linalg.norm(b))
    if an < 1e-9 or bn < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (an * bn))


def ingest(
    user_id: int,
    kind: str,
    source_text: str,
    tenant_id: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    if not memory_enabled():
        return None
    st = (source_text or "").strip()
    if not st or len(st) < 8:
        return None
    if len(st) > _MAX_SOURCE_CHARS:
        st = st[: _MAX_SOURCE_CHARS - 1] + "…"
    vec = embed_text(st)
    blob, dim = _to_blob(vec)
    db = Database()
    meta_s = json.dumps(meta or {}, ensure_ascii=False) if meta else None
    return db.insert_ai_context_memory(
        user_id=user_id,
        kind=(kind or "note")[:64],
        source_text=st,
        embedding=blob,
        embedding_dim=dim,
        tenant_id=tenant_id,
        meta_json=meta_s,
    )


def search(
    user_id: int,
    query_text: str,
    tenant_id: Optional[int] = None,
    topk: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if not memory_enabled():
        return []
    q = (query_text or "").strip()
    if not q or len(q) < 3:
        return []
    if len(q) > _MAX_QUERY_CHARS:
        q = q[: _MAX_QUERY_CHARS - 1] + "…"
    k = topk
    if k is None:
        raw = (os.environ.get("LOCAL_MEMORY_TOPK") or "5").strip()
        k = int(raw) if raw.isdigit() else 5
    k = max(1, min(k, 20))

    db = Database()
    rows = db.fetch_ai_context_memory_rows(user_id=user_id, tenant_id=tenant_id)
    if not rows:
        return []

    try:
        qv = embed_text(q)
    except ValueError:
        return []

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for r in rows:
        try:
            dim = int(r["embedding_dim"])
            blob = r["embedding"]
            if not blob or len(blob) < dim * 4:
                continue
            v = _from_blob(blob, dim)
            if v.shape[0] != qv.shape[0]:
                continue
            s = _cosine(qv, v)
        except (TypeError, ValueError, IndexError, BufferError):
            continue
        meta_obj: Any = None
        mj = (r.get("meta_json") or "").strip()
        if mj:
            try:
                meta_obj = json.loads(mj)
            except json.JSONDecodeError:
                meta_obj = None
        scored.append(
            (
                s,
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "source_text": r["source_text"],
                    "score": s,
                    "meta": meta_obj,
                },
            )
        )
    scored.sort(key=lambda x: -x[0])
    return [x[1] for x in scored[:k]]


def format_memory_block(hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return ""
    lines: List[str] = ["Similar past context (retrieved from local memory, do not treat as current page state):"]
    n = 0
    for h in hits:
        st = re.sub(r"\s+", " ", (h.get("source_text") or "")[:1200]).strip()
        if not st:
            continue
        n += 1
        kind = (h.get("kind") or "note")[:32]
        lines.append(f"[{n}] ({kind}, score~{h.get('score', 0):.3f}) {st}")
    if n == 0:
        return ""
    out = "\n".join(lines)
    if len(out) > _MAX_BLOCK_CHARS:
        out = out[: _MAX_BLOCK_CHARS - 1] + "…"
    return out


def build_query_for_case(goal: str, probe_url: str = "", project_name: str = "") -> str:
    parts = [p for p in (project_name, probe_url, goal) if (p or "").strip()]
    return "\n".join(parts)


def ingest_repair_case(
    user_id: int,
    task_type: str,
    payload: Any,
    cloud_result: Optional[Dict[str, Any]] = None,
    tenant_id: Optional[int] = None,
) -> Optional[int]:
    if not memory_enabled():
        return None
    try:
        blob = json.dumps(payload, ensure_ascii=False)[: _MAX_SOURCE_CHARS]
    except (TypeError, ValueError):
        blob = str(payload)[:_MAX_SOURCE_CHARS]
    out_part = ""
    if isinstance(cloud_result, dict):
        cr = cloud_result.get("cloud_response")
        if cr is not None:
            try:
                out_part = json.dumps(cr, ensure_ascii=False)[:4000]
            except (TypeError, ValueError):
                out_part = str(cr)[:4000]
    text = f"task={task_type}\ninput={blob}\noutput={out_part}"
    return ingest(
        user_id,
        "repair",
        text,
        tenant_id=tenant_id,
        meta={"task_type": task_type},
    )


def memory_ingest_run_success_enabled() -> bool:
    """与 LOCAL_MEMORY_ENABLE 同时开启时，成功执行后写入一条 run_success 模式（默认关，防库膨胀）。"""
    return memory_enabled() and _env_bool("LOCAL_MEMORY_INGEST_RUN_SUCCESS", False)


def ingest_successful_run(
    user_id: int,
    tenant_id: Optional[int],
    case_id: int,
    case_name: str,
    case_url: str,
    duration_sec: float,
    run_history_id: int,
) -> Optional[int]:
    if not memory_ingest_run_success_enabled():
        return None
    text = "\n".join(
        [
            "run_success",
            f"case_id={case_id}",
            f"run_history_id={run_history_id}",
            f"name={case_name[:400]}",
            f"url={(case_url or '')[:500]}",
            f"duration_s={round(float(duration_sec), 2)}",
        ]
    )
    return ingest(
        user_id,
        "run_success",
        text,
        tenant_id=tenant_id,
        meta={"case_id": case_id, "run_id": run_history_id},
    )
