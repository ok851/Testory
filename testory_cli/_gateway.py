# -*- coding: utf-8 -*-
"""共享：嵌入式浏览器网关 HTTP 调用。"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _ensure_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_ROOT / ".env")
    except ImportError:
        pass
    try:
        import env_example_sync

        env_example_sync.sync_env_from_example(_ROOT)
    except Exception:
        pass


def gateway_run_steps(session_id: str, steps: List[Dict[str, Any]], *, user_id: int = 0) -> Tuple[Optional[Dict], Optional[str]]:
    _ensure_env()
    from embedded_browser_client import embedded_gateway_json

    sid = (session_id or "").strip()
    if not sid:
        return None, "session_id 不能为空"
    return embedded_gateway_json(
        "POST",
        f"/internal/session/{sid}/run-steps",
        user_id=user_id or None,
        body={"steps": steps},
        timeout_sec=180.0,
    )


def gateway_screenshot_png(session_id: str, *, user_id: int = 0) -> Tuple[Optional[bytes], Optional[str]]:
    _ensure_env()
    from embedded_browser_client import embedded_gateway_json

    j, err = embedded_gateway_json(
        "GET",
        f"/internal/session/{session_id}/screenshot",
        user_id=user_id or None,
        timeout_sec=30.0,
    )
    if err:
        return None, err
    if not j or not j.get("success"):
        return None, str((j or {}).get("detail") or "screenshot failed")
    raw = (j.get("data") or "").strip()
    if not raw:
        return None, "empty screenshot"
    try:
        return base64.b64decode(raw), None
    except Exception as e:
        return None, str(e)


def load_steps_file(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("steps"), list):
        return data["steps"]
    raise ValueError("steps 文件须为 JSON 数组或 {\"steps\": [...]}")
