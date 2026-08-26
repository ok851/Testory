# -*- coding: utf-8 -*-
"""Readonly DB assertion skeleton aligned with the merged execution plan.

Design constraints:
- default to readonly access
- allow only simple SELECT statements
- enforce table allowlist and row limits
- support connection encryption via environment variables
- automatic result masking for sensitive fields
- keep implementation safe enough for first-phase rollout
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Set


_SELECT_PATTERN = re.compile(r"^\s*select\b", re.IGNORECASE)
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|grant|revoke|attach|detach|vacuum)\b",
    re.IGNORECASE)
_SUBQUERY_PATTERN = re.compile(r"\b(select)\b", re.IGNORECASE)

# Sensitive field patterns for auto-masking
_SENSITIVE_FIELD_PATTERNS = [
    re.compile(r"(password|passwd|pwd|secret|token|key|credential)", re.IGNORECASE),
    re.compile(r"(phone|mobile|tel|email|mail|id_card|identity|身份证)", re.IGNORECASE),
    re.compile(r"(credit_card|card_number|银行卡|卡号)", re.IGNORECASE),
]

# Default masking rules
_DEFAULT_MASK_RULES: Dict[str, str] = {
    "password": "***",
    "passwd": "***",
    "pwd": "***",
    "secret": "***",
    "token": "***",
    "phone": "phone_tail4",
    "mobile": "phone_tail4",
    "email": "email_partial",
    "id_card": "id_card_partial",
}


def _default_allowed_tables() -> List[str]:
    raw = os.environ.get("DB_ASSERT_ALLOWED_TABLES", "").strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _extract_table_tokens(sql: str) -> List[str]:
    simple = re.findall(r"from\s+([A-Za-z0-9_\.]+)|join\s+([A-Za-z0-9_\.]+)", sql, re.IGNORECASE)
    tables: List[str] = []
    for left, right in simple:
        token = left or right
        if token:
            tables.append(token.split(".")[-1])
    return tables


def _ensure_readonly_sql(sql: str, allowed_tables: Sequence[str]) -> None:
    if not sql or not _SELECT_PATTERN.search(sql):
        raise ValueError("仅支持只读 SELECT 查询")
    if _FORBIDDEN_KEYWORDS.search(sql):
        raise ValueError("包含不允许的 SQL 关键字")
    # Prevent nested subqueries that might bypass readonly
    if _SUBQUERY_PATTERN.search(sql[6:]):  # Skip first SELECT
        raise ValueError("不允许嵌套子查询")
    if allowed_tables:
        tables = _extract_table_tokens(sql)
        allowed_set = {t.lower() for t in allowed_tables}
        for table in tables:
            if table.lower() not in allowed_set:
                raise ValueError(f"不允许查询表: {table}")


def _decrypt_connection_string(encrypted: str) -> str:
    """Decrypt connection string from environment variable.
    
    Supports:
    - Plain text (no encryption)
    - Base64 encoded
    - Hash-based verification (DB_ASSERT_DSN_HASH)
    """
    if not encrypted:
        return ""
    
    # Check if it's base64 encoded
    try:
        decoded = base64.b64decode(encrypted).decode("utf-8")
        # Verify hash if configured
        expected_hash = os.environ.get("DB_ASSERT_DSN_HASH", "").strip()
        if expected_hash:
            actual_hash = hashlib.sha256(decoded.encode()).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError("连接字符串哈希验证失败")
        return decoded
    except Exception:
        # Not base64, treat as plain text
        return encrypted


def _connect(connection_name: str) -> sqlite3.Connection:
    dsn = os.environ.get(f"DB_ASSERT_DSN_{connection_name.upper()}") or os.environ.get("DB_ASSERT_DSN")
    if not dsn:
        raise ValueError("未配置数据库连接信息，请通过环境变量 DB_ASSERT_DSN 配置")
    
    # Decrypt if needed
    dsn = _decrypt_connection_string(dsn)
    
    conn = sqlite3.connect(dsn, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 3000")
        conn.execute("PRAGMA query_only = ON")
    except Exception:
        pass
    return conn


def _is_sensitive_field(field_name: str) -> bool:
    """Check if a field name matches sensitive patterns."""
    for pattern in _SENSITIVE_FIELD_PATTERNS:
        if pattern.search(field_name):
            return True
    return False


def _mask_value(value: Any, field_name: str) -> Any:
    """Apply masking rules to a value based on field name."""
    if value is None:
        return None
    
    value_str = str(value)
    field_lower = field_name.lower()
    
    # Check for specific masking rules
    for pattern, rule in _DEFAULT_MASK_RULES.items():
        if pattern in field_lower:
            if rule == "***":
                return "***"
            elif rule == "phone_tail4":
                if len(value_str) >= 4:
                    return f"****{value_str[-4:]}"
                return "****"
            elif rule == "email_partial":
                if "@" in value_str:
                    local, domain = value_str.split("@", 1)
                    if len(local) > 2:
                        return f"{local[:2]}***@{domain}"
                    return f"***@{domain}"
                return "***"
            elif rule == "id_card_partial":
                if len(value_str) >= 8:
                    return f"{value_str[:4]}****{value_str[-4:]}"
                return "****"
    
    return value


def _mask_sensitive_fields(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Automatically mask sensitive fields in query results."""
    if not rows:
        return rows
    
    # Identify sensitive fields from first row
    first_row = rows[0]
    sensitive_fields: Set[str] = set()
    for field_name in first_row.keys():
        if _is_sensitive_field(field_name):
            sensitive_fields.add(field_name)
    
    if not sensitive_fields:
        return rows
    
    # Apply masking
    masked_rows = []
    for row in rows:
        masked_row = {}
        for key, value in row.items():
            if key in sensitive_fields:
                masked_row[key] = _mask_value(value, key)
            else:
                masked_row[key] = value
        masked_rows.append(masked_row)
    
    return masked_rows


def execute_readonly_query(
    sql: str,
    *,
    params: Optional[Sequence[Any]] = None,
    connection_name: str = "default",
    max_rows: int = 200,
    auto_mask: bool = True,
) -> Dict[str, Any]:
    """Execute a readonly query with security checks and optional masking.
    
    Args:
        sql: SQL query (must be SELECT only)
        params: Query parameters
        connection_name: Connection configuration name
        max_rows: Maximum rows to return
        auto_mask: Automatically mask sensitive fields
    """
    allowed_tables = _default_allowed_tables()
    _ensure_readonly_sql(sql, allowed_tables)

    warnings: List[str] = []
    conn = _connect(connection_name)
    try:
        # Set query timeout
        conn.execute("PRAGMA busy_timeout = 5000")
        
        cur = conn.execute(sql, params or [])
        rows = cur.fetchmany(max(max_rows, 1))
        dicts = [dict(row) for row in rows]
        
        # Apply masking if enabled
        if auto_mask:
            dicts = _mask_sensitive_fields(dicts)
            if auto_mask and dicts and any(
                "***" in str(v) or "****" in str(v) 
                for row in dicts for v in row.values()
            ):
                warnings.append("sensitive_fields_masked")
        
        return {
            "ok": True,
            "rows": dicts,
            "row_count": len(dicts),
            "warnings": warnings,
            "masked": auto_mask,
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def execute_readonly_scalar_assertion(
    sql: str,
    *,
    expected: Any,
    params: Optional[Sequence[Any]] = None,
    connection_name: str = "default",
    max_rows: int = 1,
    auto_mask: bool = True,
) -> Dict[str, Any]:
    """Execute a readonly scalar assertion with security checks.
    
    Args:
        sql: SQL query (must be SELECT only)
        expected: Expected value to compare against
        params: Query parameters
        connection_name: Connection configuration name
        max_rows: Maximum rows to return
        auto_mask: Automatically mask sensitive fields
    """
    result = execute_readonly_query(
        sql,
        params=params,
        connection_name=connection_name,
        max_rows=max_rows,
        auto_mask=auto_mask,
    )

    if not result.get("ok"):
        return {
            "ok": False,
            "message": result.get("message") or "DB查询失败",
            "actual": None,
            "expected": expected,
            "warnings": result.get("warnings") or [],
        }

    rows = result.get("rows") or []
    actual = None
    if rows:
        first = rows[0]
        if isinstance(first, dict):
            actual = list(first.values())[0] if first else None
        else:
            actual = first

    ok = str(actual) == str(expected)
    return {
        "ok": ok,
        "message": "DB断言通过" if ok else f"DB断言失败: actual={actual}",
        "actual": actual,
        "expected": expected,
        "row_count": result.get("row_count"),
        "warnings": result.get("warnings") or [],
        "masked": result.get("masked", False),
    }
