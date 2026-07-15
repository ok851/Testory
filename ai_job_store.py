# -*- coding: utf-8 -*-
"""AI 后台任务持久化存储（SQLite）。

替代 app.py 中的进程内字典 _AI_BG_JOBS，支持进程重启后恢复任务状态。
使用 WAL 模式确保读写并发安全。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


def _default_db_path() -> str:
    data_dir = os.environ.get("UAT_DATA_DIR") or os.environ.get("DATABASE_PATH", "")
    if data_dir:
        base = str(Path(data_dir).parent)
    else:
        base = str(Path(__file__).parent / "data")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "ai_jobs.db")


class AIJobStore:
    """SQLite-backed AI job store with thread-safe operations."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _default_db_path()
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_schema(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ai_jobs (
                job_id      TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                kind        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'running',
                cancelled   INTEGER NOT NULL DEFAULT 0,
                http_status INTEGER,
                result_json TEXT,
                error       TEXT,
                t0          REAL NOT NULL,
                t_done      REAL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_ai_jobs_user   ON ai_jobs(user_id);
        """)
        conn.commit()

    def create(self, user_id: int, kind: str) -> str:
        """创建新任务，返回 job_id。"""
        job_id = str(uuid.uuid4())
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO ai_jobs (job_id, user_id, kind, status, t0) VALUES (?, ?, ?, 'running', ?)",
            (job_id, user_id, kind, time.time()),
        )
        conn.commit()
        return job_id

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        """获取任务记录，返回 dict 或 None。"""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM ai_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def is_cancelled(self, job_id: str) -> bool:
        """检查任务是否被取消（高频调用，走缓存优化）。"""
        conn = self._get_conn()
        row = conn.execute("SELECT cancelled FROM ai_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return bool(row and row["cancelled"])

    def set_result(self, job_id: str, result: Dict[str, Any], http_status: int = 200):
        """设置任务完成结果。"""
        conn = self._get_conn()
        # 检查是否已取消
        row = conn.execute("SELECT cancelled FROM ai_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row and row["cancelled"]:
            conn.execute(
                "UPDATE ai_jobs SET status='cancelled', t_done=? WHERE job_id=?",
                (time.time(), job_id),
            )
            conn.commit()
            return

        success = result.get("success", False)
        status = "done" if success else "error"
        error = result.get("error") if not success else None
        conn.execute(
            "UPDATE ai_jobs SET status=?, http_status=?, result_json=?, error=?, t_done=? WHERE job_id=?",
            (status, http_status, json.dumps(result, ensure_ascii=False), error, time.time(), job_id),
        )
        conn.commit()

    def set_error(self, job_id: str, error: str, http_status: int = 500):
        """设置任务错误。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE ai_jobs SET status='error', http_status=?, error=?, t_done=? WHERE job_id=?",
            (http_status, error, time.time(), job_id),
        )
        conn.commit()

    def set_cancelled(self, job_id: str):
        """标记任务取消。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE ai_jobs SET cancelled=1 WHERE job_id=?",
            (job_id,),
        )
        conn.commit()

    def prune(self, max_age_seconds: int = 3600, max_remove: int = 500):
        """清理已完成/取消的过期任务。"""
        cutoff = time.time() - max_age_seconds
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM ai_jobs WHERE job_id IN "
            "(SELECT job_id FROM ai_jobs WHERE status IN ('done','error','cancelled') AND t_done < ? LIMIT ?)",
            (cutoff, max_remove),
        )
        conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        result_json = d.pop("result_json", None)
        if result_json:
            try:
                d["result"] = json.loads(result_json)
            except (json.JSONDecodeError, TypeError):
                d["result"] = None
        else:
            d["result"] = None
        d["cancelled"] = bool(d.get("cancelled"))
        return d


# 全局单例
_store: Optional[AIJobStore] = None
_store_lock = threading.Lock()


def get_job_store() -> AIJobStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = AIJobStore()
    return _store
