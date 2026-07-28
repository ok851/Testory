# -*- coding: utf-8 -*-
"""生产策略：body 上限、LLM 超时/降级、IP 允许列表、简易限流。"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_RATE_LOCK = threading.Lock()
_RATE_BUCKET: Dict[str, List[float]] = {}


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        return int(raw)
    return default


def _env_bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def max_diff_chars() -> int:
    # 允许环境下调到 500（测试/小 diff）；默认 200k
    return max(500, min(_env_int("CODE_INTEL_MAX_DIFF_CHARS", 200_000), 2_000_000))


def max_files() -> int:
    return max(1, min(_env_int("CODE_INTEL_MAX_FILES", 200), 2000))


def max_snippet_chars() -> int:
    return max(500, min(_env_int("CODE_INTEL_MAX_SNIPPET_CHARS", 8000), 50_000))


def llm_enabled_default() -> bool:
    """企业可设 CODE_INTEL_USE_LLM=0 强制启发式。"""
    return _env_bool("CODE_INTEL_USE_LLM", True)


def llm_timeout_s() -> float:
    return float(max(3, min(_env_int("CODE_INTEL_LLM_TIMEOUT_S", 45), 300)))


def task_ttl_days() -> int:
    return max(1, min(_env_int("CODE_INTEL_TASK_TTL_DAYS", 30), 365))


def dedup_window_minutes() -> int:
    """同 MR/短时窗口合并分析（分钟）。"""
    return max(0, min(_env_int("CODE_INTEL_DEDUP_WINDOW_MIN", 15), 24 * 60))


def webhook_ip_allowlist() -> List[str]:
    raw = (os.environ.get("CODE_INTEL_WEBHOOK_IP_ALLOWLIST") or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def rate_limit_per_minute() -> int:
    return max(0, min(_env_int("CODE_INTEL_RATE_LIMIT_PER_MIN", 60), 10_000))


def clamp_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """截断 oversized 字段，返回 (new_payload, warnings)。"""
    warns: List[str] = []
    out = dict(payload)
    diff = str(out.get("diff") or "")
    cap = max_diff_chars()
    if len(diff) > cap:
        out["diff"] = diff[:cap] + "\n…(truncated by policy)…"
        warns.append(f"diff 截断至 {cap} 字符")

    files = out.get("changed_files") or []
    if isinstance(files, str):
        files = [f.strip() for f in files.replace(";", "\n").splitlines() if f.strip()]
    orig_files = files if isinstance(files, list) else []
    files = [str(f) for f in orig_files if f][: max_files()]
    if len(orig_files) > max_files():
        warns.append(f"changed_files 截断至 {max_files()} 个")
    out["changed_files"] = files

    snippets = out.get("file_snippets")
    if isinstance(snippets, dict):
        sc = max_snippet_chars()
        clipped: Dict[str, str] = {}
        for i, (k, v) in enumerate(list(snippets.items())[: max_files()]):
            s = str(v)
            if len(s) > sc:
                s = s[:sc] + "…"
                warns.append(f"file_snippets[{k}] 截断")
            clipped[str(k)[:500]] = s
            if i >= max_files() - 1:
                break
        out["file_snippets"] = clipped
    else:
        out["file_snippets"] = {}

    mr = str(out.get("mr_description") or "")
    if len(mr) > 8000:
        out["mr_description"] = mr[:8000]
        warns.append("mr_description 截断至 8000")
    return out, warns


def check_ip_allowed(client_ip: str) -> Tuple[bool, Optional[str]]:
    allow = webhook_ip_allowlist()
    if not allow:
        return True, None
    ip = (client_ip or "").strip()
    if not ip:
        return False, "missing client ip"
    # 简单精确/前缀匹配（支持 10.0.0.）
    for rule in allow:
        if ip == rule or (rule.endswith(".") and ip.startswith(rule)):
            return True, None
        if "/" in rule:
            # 极简 CIDR：仅支持 /8 /16 /24 前缀字节
            try:
                net, bits = rule.split("/", 1)
                b = int(bits)
                parts = net.split(".")
                ip_parts = ip.split(".")
                if len(parts) == 4 and len(ip_parts) == 4:
                    n = b // 8
                    if ip_parts[:n] == parts[:n]:
                        return True, None
            except Exception:
                continue
    return False, f"ip {ip} not in allowlist"


def check_rate_limit(key: str) -> Tuple[bool, Optional[str]]:
    limit = rate_limit_per_minute()
    if limit <= 0:
        return True, None
    now = time.time()
    window = 60.0
    k = (key or "global")[:200]
    with _RATE_LOCK:
        bucket = _RATE_BUCKET.setdefault(k, [])
        bucket[:] = [t for t in bucket if now - t < window]
        if len(bucket) >= limit:
            return False, f"rate limit exceeded ({limit}/min)"
        bucket.append(now)
    return True, None


def resolve_use_llm(request_flag: Any) -> bool:
    if not llm_enabled_default():
        return False
    if request_flag is False or str(request_flag).strip().lower() in ("0", "false", "no"):
        return False
    if request_flag is True or str(request_flag).strip().lower() in ("1", "true", "yes"):
        return True
    return True
