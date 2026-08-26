"""
平台时间约定：
- SQLite 中无时区的 TIMESTAMP / 文本时间，与 SQLite CURRENT_TIMESTAMP 一致，按 **UTC** 理解。
- 返回给前端 JSON 的时间字段统一转为 **Asia/Shanghai** 的 ISO 8601（含 +08:00），避免浏览器按本机时区误解析。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

BEIJING = ZoneInfo("Asia/Shanghai")


def utc_now_sqlite_str() -> str:
    """写入 SQLite 的 UTC 时间，格式 YYYY-MM-DD HH:MM:SS（秒精度）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def beijing_now_iso() -> str:
    """当前时刻的北京时间 ISO 字符串（含 +08:00），用于通知文案等即时展示。"""
    return (
        datetime.now(timezone.utc)
        .astimezone(BEIJING)
        .replace(microsecond=0)
        .isoformat()
    )


def beijing_now_strftime(fmt: str) -> str:
    """按北京时间格式化当前时刻（用于报告「生成时间」等）。"""
    return datetime.now(timezone.utc).astimezone(BEIJING).strftime(fmt)


def _parse_db_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    # 纯日期：按「北京当日 0 点」理解（常见于到期日）
    if len(s) == 10 and s[4] == "-" and s[7] == "-" and "T" not in s and " " not in s:
        try:
            d0 = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=BEIJING)
            return d0.astimezone(timezone.utc)
        except ValueError:
            return None

    norm = s.replace("Z", "+00:00").replace("z", "+00:00")
    dt: Optional[datetime] = None
    try:
        dt = datetime.fromisoformat(norm)
    except ValueError:
        dt = None
    if dt is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                dt = datetime.strptime(s[:26], fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_beijing_iso(value: Any) -> Any:
    """将 DB / 中间层时间值转为北京时间 ISO；无法解析则原样返回。"""
    if value is None:
        return None
    dtu = _parse_db_timestamp(value)
    if dtu is None:
        return value
    return dtu.astimezone(BEIJING).replace(microsecond=0).isoformat()


def utc_sqlite_str_plus_days(days: int) -> str:
    """UTC 的「当前 + days」SQLite 格式字符串（用于订单等到期写入）。"""
    t = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=days)
    return t.strftime("%Y-%m-%d %H:%M:%S")
