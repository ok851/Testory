"""
测试步骤回放报告（Phase 2b）：时间线 + 截图 + 友好失败说明。

输出目录：{UAT_DATA_DIR}/reports/vision_replay/{run_id}/
面向用户通过「查看本次测试回放」打开，不暴露原始路径。
"""
from __future__ import annotations

import base64
import html
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from logger import uat_logger

_last_replay_meta: Dict[str, Any] = {}
_meta_lock = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def vision_replay_enabled() -> bool:
    return _env_bool("VISION_STEP_REPORT_ENABLE", True)


def reports_root() -> Path:
    raw = (os.environ.get("UAT_DATA_DIR") or "").strip()
    root = Path(raw) if raw else Path("data")
    return root / "reports" / "vision_replay"


def pop_last_replay_meta() -> Optional[Dict[str, Any]]:
    with _meta_lock:
        meta = dict(_last_replay_meta) if _last_replay_meta else None
        _last_replay_meta.clear()
        return meta


def replay_run_dir(run_id: str) -> Path:
    safe = "".join(c for c in (run_id or "") if c.isalnum() or c in "-_")
    return reports_root() / (safe or "unknown")


def replay_index_path(run_id: str) -> Optional[Path]:
    p = replay_run_dir(run_id) / "index.html"
    return p if p.is_file() else None


def _friendly_step_label(step: Dict[str, Any]) -> str:
    if not isinstance(step, dict):
        return "步骤"
    desc = (step.get("description") or "").strip()
    if desc:
        return desc
    action = (step.get("action") or "").strip()
    mapping = {
        "navigate": "打开页面",
        "click": "点击",
        "input": "输入",
        "fill": "填写",
        "assert": "确认",
        "assert_vision": "画面确认",
        "wait_vision": "等待画面变化",
        "extract_vision": "读取画面信息",
        "ai_tap": "智能点击",
        "ai_input": "智能输入",
        "verify": "验证",
    }
    label = mapping.get(action, action or "步骤")
    sv = (step.get("selector_value") or step.get("selector") or "").strip()
    if sv and len(sv) < 60:
        return f"{label}：{sv}"
    return label


class VisionReplaySession:
    """单次脚本执行的回放记录器。"""

    def __init__(self, run_id: str, *, platform: str = ""):
        self.run_id = run_id
        self.run_dir = replay_run_dir(run_id)
        self.steps: List[Dict[str, Any]] = []
        self.started_at = time.time()
        self.platform = (platform or "").strip().lower()

    @classmethod
    def start(cls, *, platform: str = "") -> "VisionReplaySession":
        run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        sess = cls(run_id, platform=platform)
        sess.run_dir.mkdir(parents=True, exist_ok=True)
        return sess

    def record(
        self,
        index: int,
        step: Dict[str, Any],
        status: str,
        message: str = "",
        png_bytes: Optional[bytes] = None,
        duration_ms: int = 0,
    ) -> None:
        shot_name = ""
        if png_bytes:
            shot_name = f"step_{index:03d}.png"
            try:
                (self.run_dir / shot_name).write_bytes(png_bytes)
            except OSError as e:
                uat_logger.debug("vision replay screenshot: %s", e)
                shot_name = ""
        self.steps.append(
            {
                "index": index,
                "label": _friendly_step_label(step),
                "action": (step.get("action") or "") if isinstance(step, dict) else "",
                "status": status,
                "message": (message or "")[:500],
                "duration_ms": int(duration_ms or 0),
                "screenshot": shot_name,
            }
        )

    def finalize(self) -> Dict[str, Any]:
        meta = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": time.time(),
            "step_count": len(self.steps),
            "steps": self.steps,
            "platform": self.platform or "web",
        }
        try:
            (self.run_dir / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            html_path = self.run_dir / "index.html"
            html_path.write_text(_render_html(meta, self.run_dir), encoding="utf-8")
        except OSError as e:
            uat_logger.warning("vision replay finalize: %s", e)
        out = {
            "run_id": self.run_id,
            "url": f"/api/ai/vision/replay/{self.run_id}/",
            "step_count": len(self.steps),
        }
        with _meta_lock:
            _last_replay_meta.clear()
            _last_replay_meta.update(out)
        return out


def _render_html(meta: Dict[str, Any], run_dir: Path) -> str:
    steps = meta.get("steps") or []
    rows: List[str] = []
    for st in steps:
        status = st.get("status") or "unknown"
        badge_cls = "ok" if status == "success" else ("fail" if status == "error" else "skip")
        badge = {"success": "成功", "error": "未通过", "skipped": "跳过"}.get(status, status)
        img_html = ""
        shot = (st.get("screenshot") or "").strip()
        if shot:
            p = run_dir / shot
            if p.is_file():
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                img_html = f'<img class="shot" src="data:image/png;base64,{b64}" alt="步骤截图"/>'
        msg = html.escape(st.get("message") or "")
        label = html.escape(st.get("label") or "")
        dur = int(st.get("duration_ms") or 0)
        rows.append(
            f'<article class="step {badge_cls}">'
            f'<div class="head"><span class="idx">#{st.get("index")}</span>'
            f'<span class="label">{label}</span>'
            f'<span class="badge {badge_cls}">{badge}</span></div>'
            f'{f"<p class=msg>{msg}</p>" if msg else ""}'
            f'{f"<p class=dur>耗时 {dur} ms</p>" if dur else ""}'
            f'{img_html}'
            f"</article>"
        )
    body = "\n".join(rows) if rows else "<p>暂无步骤记录</p>"
    title = html.escape(f"测试回放 {meta.get('run_id', '')}")
    plat = (meta.get("platform") or "web").strip().lower()
    plat_label = {"android": "Android 移动端", "web": "Web 浏览器", "desktop": "桌面端"}.get(plat, plat)
    subtitle = html.escape(f"平台：{plat_label} · 共 {len(steps)} 步")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:0;background:#f4f6f8;color:#1a1a1a}}
header{{background:#1e3a5f;color:#fff;padding:16px 20px}}
header h1{{margin:0;font-size:1.15rem;font-weight:600}}
header p{{margin:6px 0 0;font-size:.85rem;opacity:.9}}
.wrap{{max-width:920px;margin:0 auto;padding:16px}}
.step{{background:#fff;border-radius:10px;padding:14px 16px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.step.fail{{border-left:4px solid #dc2626}}
.step.ok{{border-left:4px solid #16a34a}}
.head{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
.idx{{color:#64748b;font-size:.85rem}}
.label{{font-weight:600;flex:1}}
.badge{{font-size:.75rem;padding:2px 8px;border-radius:999px}}
.badge.ok{{background:#dcfce7;color:#166534}}
.badge.fail{{background:#fee2e2;color:#991b1b}}
.msg{{margin:8px 0 0;color:#475569;font-size:.9rem}}
.dur{{margin:4px 0 0;color:#94a3b8;font-size:.8rem}}
.shot{{display:block;max-width:100%;margin-top:10px;border-radius:6px;border:1px solid #e2e8f0}}
</style>
</head>
<body>
<header><h1>本次测试回放</h1><p>{subtitle}</p></header>
<div class="wrap">{body}</div>
</body>
</html>"""
