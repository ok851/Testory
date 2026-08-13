# -*- coding: utf-8 -*-
"""PC ↔ 手机 Sync：配对 token、用例 bundle、运行 job 队列。"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import jsonify, request

_LOCK = threading.RLock()
_PAIR_CODES: Dict[str, Dict[str, Any]] = {}
_DEVICE_TOKENS: Dict[str, Dict[str, Any]] = {}
_RUN_JOBS: Dict[str, Dict[str, Any]] = {}
_RUN_EVENTS: Dict[str, List[Dict[str, Any]]] = {}

# 原缺陷：600s 过长且与 UI 桩 API 断链导致「无效或过期」误报；延长至用户可接受窗口并统一注册。
_PAIR_TTL_SEC = 120
_PAIR_RETRY_WINDOW_SEC = 30
_STORE_PATH: Optional[Path] = None
_JOBS_PATH: Optional[Path] = None
_JOBS_FILE_MTIME: float = 0.0


def _sync_dirs() -> List[Path]:
    """可能存放 mobile_sync 的目录（兼容 UAT_DATA_DIR 与源码旁落盘）。"""
    dirs: List[Path] = []
    seen = set()
    candidates: List[Path] = []
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        candidates.append(Path(env) / "mobile_sync")
    try:
        from install_paths import uat_data_dir  # type: ignore

        candidates.append(Path(uat_data_dir()) / "mobile_sync")
    except Exception:
        pass
    candidates.append(Path(__file__).resolve().parent / "mobile_sync")
    for d in candidates:
        try:
            key = str(d.resolve())
        except Exception:
            key = str(d)
        if key in seen:
            continue
        seen.add(key)
        dirs.append(d)
    return dirs or [Path(__file__).resolve().parent / "mobile_sync"]


def _store_file() -> Path:
    global _STORE_PATH
    if _STORE_PATH is None:
        primary = _sync_dirs()[0]
        _STORE_PATH = primary / "tokens.json"
    return _STORE_PATH


def _jobs_file() -> Path:
    global _JOBS_PATH
    if _JOBS_PATH is None:
        _JOBS_PATH = _store_file().parent / "run_jobs.json"
    return _JOBS_PATH


def _load_persisted() -> None:
    # 合并所有候选目录中的 tokens，避免「Tauri UAT_DATA_DIR」与「源码旁 mobile_sync」分裂
    for d in _sync_dirs():
        path = d / "tokens.json"
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            tokens = raw.get("device_tokens") or {}
            if isinstance(tokens, dict):
                with _LOCK:
                    for tok, meta in tokens.items():
                        if tok and isinstance(meta, dict):
                            _DEVICE_TOKENS[tok] = meta
        except Exception:
            pass
    _load_jobs_from_disk(force=True)


def _save_persisted() -> None:
    path = _store_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        payload = {"device_tokens": _DEVICE_TOKENS}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _status_rank(st: str) -> int:
    s = (st or "").strip().lower()
    if s == "pending":
        return 0
    if s == "running":
        return 1
    if s in ("success", "ok", "error", "failed", "cancelled"):
        return 2
    return 0


def _load_jobs_from_disk(*, force: bool = False) -> None:
    """把磁盘上的 run job 合并进内存，解决双 Flask / 多进程各持一份 _RUN_JOBS 的问题。"""
    global _JOBS_FILE_MTIME
    path = _jobs_file()
    if not path.is_file():
        return
    try:
        mtime = float(path.stat().st_mtime)
    except Exception:
        return
    if not force and mtime <= _JOBS_FILE_MTIME:
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    jobs = raw.get("jobs") if isinstance(raw, dict) else None
    events = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(jobs, dict):
        return
    with _LOCK:
        for jid, job in jobs.items():
            if not jid or not isinstance(job, dict):
                continue
            cur = _RUN_JOBS.get(jid)
            if cur is None:
                _RUN_JOBS[jid] = dict(job)
            else:
                if _status_rank(str(job.get("status"))) > _status_rank(str(cur.get("status"))):
                    _RUN_JOBS[jid] = dict(job)
        if isinstance(events, dict):
            for jid, evs in events.items():
                if isinstance(evs, list) and jid:
                    _RUN_EVENTS.setdefault(jid, [])
                    if not _RUN_EVENTS[jid] and evs:
                        _RUN_EVENTS[jid] = list(evs)[-50:]
        _JOBS_FILE_MTIME = mtime


def _persist_jobs_unlocked() -> None:
    global _JOBS_FILE_MTIME
    path = _jobs_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 清理过旧终态，避免文件无限涨
    now = time.time()
    pruned: Dict[str, Dict[str, Any]] = {}
    for jid, job in list(_RUN_JOBS.items()):
        st = str(job.get("status") or "").strip().lower()
        finished = float(job.get("finished_at") or 0) or float(job.get("created_at") or 0)
        if st in ("success", "ok", "error", "failed", "cancelled") and finished and now - finished > 3600:
            _RUN_JOBS.pop(jid, None)
            _RUN_EVENTS.pop(jid, None)
            continue
        pruned[jid] = job
    payload = {
        "jobs": pruned,
        "events": {k: (v[-30:] if isinstance(v, list) else []) for k, v in _RUN_EVENTS.items() if k in pruned},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    try:
        _JOBS_FILE_MTIME = float(path.stat().st_mtime)
    except Exception:
        _JOBS_FILE_MTIME = time.time()


def _migrate_legacy_jobs_once() -> None:
    """启动时把其它目录里的 pending job 迁到主目录（仅一次合并）。"""
    primary = _jobs_file()
    for d in _sync_dirs():
        path = d / "run_jobs.json"
        if path == primary or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            jobs = raw.get("jobs") if isinstance(raw, dict) else None
            if not isinstance(jobs, dict):
                continue
            with _LOCK:
                changed = False
                for jid, job in jobs.items():
                    if not jid or not isinstance(job, dict):
                        continue
                    if jid not in _RUN_JOBS:
                        _RUN_JOBS[jid] = dict(job)
                        changed = True
                if changed:
                    _persist_jobs_unlocked()
        except Exception:
            continue


def _bootstrap_persisted() -> None:
    _load_persisted()
    _migrate_legacy_jobs_once()


_bootstrap_persisted()


def create_pair_code(user_id: int, tenant_id: Optional[int] = None) -> Dict[str, Any]:
    """生成并注册配对码；返回 code 与过期时间供 UI 倒计时。"""
    code = f"{secrets.randbelow(900000) + 100000:06d}"
    now = time.time()
    with _LOCK:
        _PAIR_CODES[code] = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "created_at": now,
            "used": False,
        }
    return {
        "pair_code": code,
        "created_at": now,
        "expires_at": now + _PAIR_TTL_SEC,
        "expires_in": _PAIR_TTL_SEC,
    }


def confirm_pair(code: str, device_id: str) -> Tuple[bool, str, Optional[str]]:
    code = (code or "").strip()
    device_id = (device_id or "").strip() or "device"
    now = time.time()
    with _LOCK:
        entry = _PAIR_CODES.get(code)
        if not entry:
            return False, "配对码无效或已过期", None
        created = float(entry.get("created_at") or 0)
        if now - created > _PAIR_TTL_SEC:
            _PAIR_CODES.pop(code, None)
            return False, "配对码已过期", None
        if entry.get("used"):
            paired_at = float(entry.get("paired_at") or 0)
            if (
                entry.get("device_id") == device_id
                and paired_at
                and now - paired_at <= _PAIR_RETRY_WINDOW_SEC
                and entry.get("device_token")
            ):
                return True, "ok", str(entry["device_token"])
            return False, "配对码无效或已过期", None
        token = secrets.token_urlsafe(32)
        entry["used"] = True
        entry["device_id"] = device_id
        entry["paired_at"] = now
        entry["device_token"] = token
        _DEVICE_TOKENS[token] = {
            "user_id": entry["user_id"],
            "tenant_id": entry.get("tenant_id"),
            "device_id": device_id,
            "paired_at": now,
        }
    _save_persisted()
    return True, "ok", token


def pair_code_payload(user_id: int, tenant_id: Optional[int] = None) -> Dict[str, Any]:
    """供 Flask 路由返回的标准配对码 JSON。"""
    info = create_pair_code(user_id, tenant_id)
    return {"success": True, **info}


def resolve_device_token() -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
    token = (request.headers.get("X-Mobile-Device-Token") or "").strip()
    if not token:
        return None, (jsonify({"success": False, "error": "缺少设备 token"}), 401)
    with _LOCK:
        meta = _DEVICE_TOKENS.get(token)
    if not meta:
        return None, (jsonify({"success": False, "error": "设备 token 无效"}), 401)
    return dict(meta), None


def list_paired_devices_for_user(user_id: int) -> List[Dict[str, Any]]:
    """该用户当前有效的已配对手机（双手：phone）。按 paired_at 新→旧。"""
    uid = int(user_id or 0)
    out: List[Dict[str, Any]] = []
    with _LOCK:
        for tok, meta in list(_DEVICE_TOKENS.items()):
            if not isinstance(meta, dict):
                continue
            try:
                if int(meta.get("user_id") or 0) != uid:
                    continue
            except Exception:
                continue
            out.append({
                "device_id": meta.get("device_id") or "",
                "user_id": uid,
                "paired_at": meta.get("paired_at"),
                "last_poll_at": meta.get("last_poll_at"),
                "poller_alive": _poller_alive_unlocked(meta),
                "token_suffix": (tok[-6:] if tok else ""),
            })
    out.sort(key=lambda d: float(d.get("paired_at") or 0), reverse=True)
    return out


# PcRunJobPoller 默认约 2s 一轮；超过此窗口视为无障碍/轮询未跑
_POLLER_STALE_SEC = 45.0


def _poller_alive_unlocked(meta: Dict[str, Any]) -> bool:
    try:
        ts = float(meta.get("last_poll_at") or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        return False
    return (time.time() - ts) <= _POLLER_STALE_SEC


def touch_device_poll(device_id: str = "", *, user_id: int = 0, token: str = "") -> None:
    """APK 拉取 pending 时心跳：证明 PcRunJobPoller/无障碍仍在跑。"""
    did = (device_id or "").strip()
    tok = (token or "").strip()
    now = time.time()
    with _LOCK:
        targets: List[Dict[str, Any]] = []
        if tok and tok in _DEVICE_TOKENS and isinstance(_DEVICE_TOKENS.get(tok), dict):
            targets.append(_DEVICE_TOKENS[tok])
        elif did:
            for meta in _DEVICE_TOKENS.values():
                if not isinstance(meta, dict):
                    continue
                if (meta.get("device_id") or "") != did:
                    continue
                if user_id and int(meta.get("user_id") or 0) not in (0, int(user_id)):
                    continue
                targets.append(meta)
        if not targets:
            return
        need_persist = False
        for meta in targets:
            prev = float(meta.get("last_poll_at") or 0)
            meta["last_poll_at"] = now
            if now - float(meta.get("_last_poll_persist") or 0) >= 12.0:
                meta["_last_poll_persist"] = now
                need_persist = True
            elif prev <= 0:
                meta["_last_poll_persist"] = now
                need_persist = True
        if need_persist:
            try:
                _save_persisted()
            except Exception:
                pass


def device_poller_status_for_user(user_id: int, device_id: str = "") -> Dict[str, Any]:
    """返回配对设备中最近一次 poll 状态（供 hand ready 门禁）。"""
    devices = list_paired_devices_for_user(int(user_id or 0))
    want = (device_id or "").strip()
    if want:
        devices = [d for d in devices if (d.get("device_id") or "") == want]
    alive = [d for d in devices if d.get("poller_alive")]
    best = alive[0] if alive else (devices[0] if devices else None)
    return {
        "paired_count": len(list_paired_devices_for_user(int(user_id or 0))),
        "candidates": devices,
        "alive_count": len(alive),
        "best": best,
        "stale_sec": _POLLER_STALE_SEC,
    }

def list_accessible_cases(db: Any, user_id: int) -> List[Dict[str, Any]]:
    projects = db.get_user_projects(user_id) or []
    out: List[Dict[str, Any]] = []
    for p in projects:
        pid = p.get("id")
        if not pid:
            continue
        for c in db.get_project_cases(int(pid)) or []:
            if (c.get("case_type") or "ui") == "api":
                continue
            out.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "project_id": pid,
                "project_name": p.get("name"),
            })
    return out


def normalize_device_step(step: Dict[str, Any]) -> Dict[str, Any]:
    """DB/API/Agent 步骤 → 手机可执行 IR。

    模型常发明 launch_app/start_app/shell 等 action；统一收敛到 open_app/home/tap 等。
    """
    out = dict(step)
    ms = out.get("mobile_spec")
    if isinstance(ms, str) and ms.strip():
        try:
            ms = json.loads(ms)
        except Exception:
            ms = {}
    if not isinstance(ms, dict):
        ms = {}
    for k in (
        "assert_text",
        "wait_duration_ms",
        "pre_wait_ms",
        "max_retries",
        "optional",
        "assert_type",
        "save_as",
        "key_code",
        "repeat_max",
        "until_assert_text",
        "captcha_hint",
        "captcha_fallback",
        "roi",
        "scroll_amount",
        "swipe_direction",
        "packageName",
        "package_name",
        "text",
    ):
        if ms.get(k) is not None and out.get(k) is None:
            out[k] = ms.get(k)

    action = str(out.get("action") or "").strip().lower().replace("-", "_")
    desc = str(out.get("description") or "").strip()
    pkg = (
        str(out.get("package_name") or out.get("app_package") or out.get("package") or "").strip()
        or str(ms.get("packageName") or ms.get("package_name") or "").strip()
    )
    sel = str(out.get("selector_value") or "").strip()
    if not pkg and sel.count(".") >= 1 and " " not in sel and sel.startswith("com."):
        pkg = sel
    if not pkg:
        pkg = _guess_android_package(desc, sel, str(out.get("text") or ""))

    # 打开应用类
    if action in (
        "open_app",
        "launch_app",
        "start_app",
        "launch",
        "startapp",
        "am_start",
        "start_activity",
    ) or (
        action == "shell"
        and "am start" in str(out.get("command") or out.get("input_value") or "").lower()
    ):
        if not pkg and action == "shell":
            cmd = str(out.get("command") or out.get("input_value") or "")
            # am start -n pkg/activity 或 -p pkg
            m = re.search(r"(?:-n\s+|component\s+)([\w.]+)/", cmd)
            if not m:
                m = re.search(r"-p\s+([\w.]+)", cmd)
            if m:
                pkg = m.group(1)
        out["action"] = "open_app"
        if pkg:
            out["package_name"] = pkg
            out["selector_value"] = pkg
            out["selector_type"] = out.get("selector_type") or "package"
            ms["packageName"] = pkg
            ms["package_name"] = pkg
        # 无包名时把描述里的应用名留给手机按桌面图标点
        app_label = _guess_app_label(desc) or desc
        if app_label and not ms.get("text"):
            ms["text"] = app_label
    elif action in ("home", "press_home", "goto_home") or (
        action in ("key_event", "press_key", "keycode")
        and str(out.get("keycode") or out.get("key_code") or out.get("input_value") or "")
        .strip()
        .upper()
        in ("HOME", "3", "KEYCODE_HOME")
    ):
        out["action"] = "home"
    elif action in ("back", "press_back"):
        out["action"] = "back"
    elif action in (
        "tap",
        "click",
        "find_and_tap",
        "tap_text",
        "click_text",
        "long_press",
        "longpress",
        "check",
        "uncheck",
        "toggle_check",
        "checkbox",
    ):
        out["action"] = "long_press" if action in ("long_press", "longpress") else "tap"
        _ensure_text_locator(out, ms, desc)
        if action in ("check", "uncheck", "toggle_check", "checkbox") or _is_check_intent(desc):
            ms["prefer_checkable"] = True
            out["prefer_checkable"] = True
    elif action in ("input", "type", "type_text", "fill", "set_text", "input_text"):
        out["action"] = "input"
        _normalize_input_step(out, ms, desc)
    elif action in ("wait", "sleep", "delay"):
        out["action"] = "wait"
        dur = (
            out.get("wait_duration_ms")
            or out.get("duration")
            or out.get("duration_ms")
            or out.get("timeout")
            or out.get("timeout_ms")
            or out.get("ms")
        )
        try:
            d = int(dur or 0)
            # 模型常写 2000 表示毫秒；若 < 50 当秒
            if 0 < d < 50:
                d *= 1000
            if d > 0:
                out["wait_duration_ms"] = d
        except Exception:
            pass

    out["mobile_spec"] = ms
    return out


def _looks_like_typed_content(value: str, description: str = "") -> bool:
    """text 字段像「要输入的内容」而非控件文案（手机号/密码等）。"""
    v = (value or "").strip()
    if not v:
        return False
    desc = (description or "").strip()
    if any(k in desc for k in ("输入", "填写", "键入", "密码", "验证码", "账号", "手机号")):
        if len(v) >= 4 or v.isdigit():
            return True
    if v.isdigit() and len(v) >= 6:
        return True
    if len(v) >= 8 and " " not in v and not v.startswith("com."):
        # 较长 token 更像输入内容而非按钮文案
        return True
    return False


def _is_check_intent(description: str) -> bool:
    d = (description or "").strip().lower()
    if not d:
        return False
    keys = (
        "勾选",
        "选中",
        "打勾",
        "勾上",
        "复选",
        "勾选框",
        "check ",
        "checkbox",
        "tick ",
        "toggle check",
    )
    return any(k in d for k in keys)


def _extract_ui_label(description: str) -> str:
    """从自然语言描述抽出短控件文案：点击登录按钮 → 登录。"""
    raw = (description or "").strip()
    if not raw:
        return ""
    m = re.search(r"[「\"'【《]([^」\"'】》]{1,24})[」\"'】》]", raw)
    if m:
        return m.group(1).strip()
    cleaned = re.sub(
        r"^(?:查找并)?(?:点击|点按|轻触|勾选|选择|打开|按下|按一下|点一下)\s*",
        "",
        raw,
    )
    cleaned = re.sub(r"(?:按钮|图标|控件|入口|选项|复选框|勾选框)$", "", cleaned).strip()
    cleaned = re.sub(r"^(?:一下|下)\s*", "", cleaned).strip()
    if 1 <= len(cleaned) <= 20:
        return cleaned
    if cleaned:
        return cleaned[:20]
    return raw[:20]


def _guess_input_label(description: str) -> str:
    """输入手机号 → 手机号；填写密码 → 密码。"""
    raw = (description or "").strip()
    if not raw:
        return ""
    m = re.search(
        r"(?:输入|填写|键入|在)\s*([A-Za-z\u4e00-\u9fff0-9/]{1,16})",
        raw,
    )
    if m:
        label = m.group(1).strip()
        label = re.sub(r"(?:框|输入框|字段|栏)$", "", label).strip()
        if label and label not in ("内容", "文本", "文字"):
            return label
    for hint in ("手机号", "手机号码", "QQ号", "账号", "帐号", "密码", "验证码", "用户名"):
        if hint in raw:
            return hint
    return ""


def _ensure_text_locator(out: Dict[str, Any], ms: Dict[str, Any], desc: str) -> None:
    """保证 tap/long_press 有 text 定位（APK 依赖 locator.text）。"""
    sel_type = str(out.get("selector_type") or "").strip().lower()
    sel = str(out.get("selector_value") or "").strip()
    text = str(out.get("text") or ms.get("text") or "").strip()
    if sel_type in ("resource_id", "id", "content_desc", "xpath", "coordinate", "package"):
        if sel_type in ("id",):
            out["selector_type"] = "resource_id"
        if sel and sel_type in ("resource_id", "content_desc") and not ms.get(
            "resource_id" if "resource" in sel_type else "content_desc"
        ):
            if "resource" in sel_type:
                ms["resource_id"] = sel
            else:
                ms["content_desc"] = sel
        return
    if not text or _looks_like_typed_content(text, desc):
        text = ""
    if not text and sel and sel_type in ("", "text") and not sel.startswith("com."):
        text = sel
    if not text:
        text = _extract_ui_label(desc)
    if text:
        out["selector_type"] = "text"
        out["selector_value"] = text
        out["text"] = text
        ms["text"] = text


def _normalize_input_step(out: Dict[str, Any], ms: Dict[str, Any], desc: str) -> None:
    """模型常把输入内容写在 text，且缺输入框定位。"""
    content = str(out.get("input_value") or "").strip()
    raw_text = str(out.get("text") or ms.get("text") or "").strip()
    sel = str(out.get("selector_value") or "").strip()
    sel_type = str(out.get("selector_type") or "").strip().lower()

    if not content:
        if raw_text and _looks_like_typed_content(raw_text, desc):
            content = raw_text
            raw_text = ""
        elif sel and _looks_like_typed_content(sel, desc) and sel_type in ("", "text"):
            content = sel
            sel = ""

    if content:
        out["input_value"] = content

    label = ""
    if sel and not _looks_like_typed_content(sel, desc) and not sel.startswith("com."):
        label = sel
    if not label and raw_text and not _looks_like_typed_content(raw_text, desc):
        label = raw_text
    if not label:
        label = _guess_input_label(desc)
    if label:
        out["selector_type"] = "text"
        out["selector_value"] = label
        out["text"] = label
        ms["text"] = label
    elif "text" in out and content and out.get("text") == content:
        # 避免把手机号留在 locator.text
        out.pop("text", None)
        ms.pop("text", None)


_APP_PACKAGE_HINTS = {
    "qq": "com.tencent.mobileqq",
    "微信": "com.tencent.mm",
    "wechat": "com.tencent.mm",
    "支付宝": "com.eg.android.AlipayGphone",
    "淘宝": "com.taobao.taobao",
    "抖音": "com.ss.android.ugc.aweme",
    "设置": "com.android.settings",
    "settings": "com.android.settings",
    "chrome": "com.android.chrome",
    "浏览器": "com.android.chrome",
}


def _guess_android_package(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return ""
    for name, pkg in _APP_PACKAGE_HINTS.items():
        if name.lower() in blob or name in blob:
            return pkg
    return ""


def _guess_app_label(description: str) -> str:
    raw = (description or "").strip()
    if not raw:
        return ""
    for name in _APP_PACKAGE_HINTS:
        if name in raw or name.lower() in raw.lower():
            return name if name not in ("wechat", "settings", "chrome") else (
                "微信" if name == "wechat" else ("设置" if name == "settings" else "Chrome")
            )
    # 「打开QQ应用」→ QQ
    m = re.search(r"(?:打开|启动|运行)\s*([A-Za-z\u4e00-\u9fff0-9]{1,12})", raw)
    if m:
        return m.group(1).replace("应用", "").strip()
    return ""


def normalize_device_steps(steps: Any) -> List[Dict[str, Any]]:
    if not isinstance(steps, list):
        return []
    return [normalize_device_step(s) for s in steps if isinstance(s, dict)]


def case_bundle(db: Any, case_id: int, user_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    case_row = db.get_test_case_v2(case_id)
    if not case_row:
        return None, "用例不存在"
    pid = case_row.get("project_id")
    if pid and not db.check_project_access(user_id, int(pid), "viewer"):
        return None, "无权限"
    steps = db.get_case_steps(case_id, page=1, page_size=500)
    return {"case": case_row, "steps": normalize_device_steps(steps)}, None


def enqueue_run_job(
    *,
    case_id: int,
    steps: List[Dict[str, Any]],
    user_id: int,
    device_id: str = "",
    source: str = "pc",
    job_kind: str = "run_steps",
    job_meta: Optional[Dict[str, Any]] = None,
) -> str:
    job_id = secrets.token_hex(12)
    _load_jobs_from_disk(force=True)
    with _LOCK:
        _RUN_JOBS[job_id] = {
            "job_id": job_id,
            "case_id": case_id,
            "steps": normalize_device_steps(steps),
            "user_id": user_id,
            "device_id": device_id,
            "source": source,
            "job_kind": (job_kind or "run_steps").strip() or "run_steps",
            "job_meta": dict(job_meta or {}),
            "status": "pending",
            "created_at": time.time(),
        }
        _RUN_EVENTS[job_id] = []
        _persist_jobs_unlocked()
    return job_id


def pop_pending_run_for_device(
    device_id: str,
    *,
    job_kind: str = "",
    user_id: int = 0,
) -> Optional[Dict[str, Any]]:
    """取出一条 pending job 并标为 running。

    job_kind 非空时只匹配该类型（避免 APK 取码轮询误吞 run_steps 任务）。
    """
    device_id = (device_id or "").strip()
    want_kind = (job_kind or "").strip().lower()
    want_uid = int(user_id or 0)
    _load_jobs_from_disk(force=True)

    def _kind_ok(job: Dict[str, Any]) -> bool:
        if not want_kind:
            return True
        kind = str(job.get("job_kind") or "run_steps").strip().lower()
        return kind == want_kind

    def _user_ok(job: Dict[str, Any]) -> bool:
        job_uid = int(job.get("user_id") or 0)
        if want_uid and job_uid and job_uid != want_uid:
            return False
        return True

    def _claim(job_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
        job["status"] = "running"
        job["claimed_by"] = device_id
        job["claimed_at"] = time.time()
        _persist_jobs_unlocked()
        return dict(job)

    with _LOCK:
        # 1) 精确 device 匹配（或 job 未指定 / unknown）
        skipped = []
        for job_id, job in list(_RUN_JOBS.items()):
            if job.get("status") != "pending":
                continue
            if not _user_ok(job):
                skipped.append(f"{job_id}:user_mismatch")
                continue
            if not _kind_ok(job):
                skipped.append(f"{job_id}:kind")
                continue
            target = (job.get("device_id") or "").strip()
            if target and target.lower() != "unknown" and device_id and target != device_id:
                skipped.append(f"{job_id}:device_mismatch({target}!={device_id})")
                continue
            return _claim(job_id, job)

        # 2) 回退：同用户 + 同 kind 的 agent 任务，忽略过期 device_id 绑定
        #    （历史 tokens 里的旧 ANDROID_ID 常导致永远 pending）
        for job_id, job in list(_RUN_JOBS.items()):
            if job.get("status") != "pending":
                continue
            if not _user_ok(job):
                continue
            if not _kind_ok(job):
                continue
            src = str(job.get("source") or "").strip().lower()
            if src not in (
                "mobile_run_steps",
                "mobile_run_case",
                "mobile_extract_otp",
                "agent_tool",
            ) and not src.startswith("mobile_"):
                continue
            try:
                from logger import uat_logger

                uat_logger.info(
                    "mobile pending fallback claim job=%s ignore_device=%s by=%s",
                    job_id,
                    job.get("device_id"),
                    device_id,
                )
            except Exception:
                pass
            return _claim(job_id, job)

        if skipped:
            try:
                from logger import uat_logger

                uat_logger.info(
                    "mobile pending miss device=%s kind=%s pending_skips=%s",
                    device_id,
                    want_kind or "*",
                    ";".join(skipped[:12]),
                )
            except Exception:
                pass
    return None


def requeue_run_job(job_id: str) -> bool:
    """将误取的 running job 退回 pending（未被执行时使用）。"""
    _load_jobs_from_disk(force=True)
    with _LOCK:
        job = _RUN_JOBS.get(job_id)
        if not job:
            return False
        if str(job.get("status") or "") != "running":
            return False
        job["status"] = "pending"
        job.pop("finished_at", None)
        job.pop("claimed_by", None)
        job.pop("claimed_at", None)
        _persist_jobs_unlocked()
        return True


def append_run_events(job_id: str, payload: Dict[str, Any]) -> bool:
    _load_jobs_from_disk(force=True)
    with _LOCK:
        job = _RUN_JOBS.get(job_id)
        if not job:
            return False
        _RUN_EVENTS.setdefault(job_id, []).append(payload)
        status = (payload.get("status") or "").strip().lower()
        err_code = str(payload.get("error_code") or "").strip().upper()
        # 本机忙：退回 pending，勿终态失败（Agent await 可继续等到下次 poll）
        if status == "busy" or err_code == "MOBILE_BUSY":
            if str(job.get("status") or "") == "running":
                job["status"] = "pending"
                job.pop("finished_at", None)
            _persist_jobs_unlocked()
            return True
        if status in ("success", "error", "failed", "cancelled", "ok"):
            job["status"] = "success" if status in ("success", "ok") else (
                "cancelled" if status == "cancelled" else "error"
            )
            job["finished_at"] = time.time()
            job["result_payload"] = dict(payload)
            _persist_jobs_unlocked()
        elif payload.get("status"):
            job["status"] = payload.get("status")
            _persist_jobs_unlocked()
        return True


def get_run_job(job_id: str) -> Optional[Dict[str, Any]]:
    _load_jobs_from_disk(force=True)
    with _LOCK:
        job = _RUN_JOBS.get(job_id)
        return dict(job) if job else None


def get_run_job_status_lite(job_id: str) -> Optional[Dict[str, Any]]:
    """轻量级状态查询：手机回放中轮询，仅返回 status / error / error_code / abort_reason。"""
    _load_jobs_from_disk(force=True)
    with _LOCK:
        job = _RUN_JOBS.get(job_id)
        if not job:
            return None
        return {
            "job_id": job_id,
            "status": str(job.get("status") or "").strip().lower(),
            "error": job.get("error") or "",
            "error_code": job.get("error_code") or "",
            "abort_reason": job.get("abort_reason") or "",
        }


def cancel_run_job(
    job_id: str,
    *,
    error: str = "",
    error_code: str = "MOBILE_JOB_CANCELLED",
    abort_reason: str = "",
) -> bool:
    """将 pending/running job 标为 cancelled（任务中止时避免手机稍后误执行）。

    abort_reason: 给手机端看的取消原因（如 user_pause / timeout）。
    """
    _load_jobs_from_disk(force=True)
    with _LOCK:
        job = _RUN_JOBS.get(job_id)
        if not job:
            return False
        st = str(job.get("status") or "").strip().lower()
        if st in ("success", "error", "failed", "cancelled", "ok"):
            return False
        job["status"] = "cancelled"
        job["error"] = (error or "").strip() or "任务已取消"
        job["error_code"] = error_code
        if abort_reason:
            job["abort_reason"] = abort_reason
        job["finished_at"] = time.time()
        _persist_jobs_unlocked()
        return True


def wait_for_run_job(
    job_id: str,
    *,
    timeout_sec: float = 600.0,
    poll_interval_sec: float = 1.0,
    abort_event: Any = None,
    on_tick: Any = None,
) -> Dict[str, Any]:
    """阻塞等待手机本机跑完并上报事件。返回 job 快照（含 result_payload）。

    abort_event: 任务中止时立刻返回，不再空等。
    on_tick: 可选回调 ``on_tick(job_snapshot)``，用于 UI 进度（勿做重活）。
    """
    deadline = time.time() + max(1.0, float(timeout_sec))
    terminal = {"success", "error", "failed", "cancelled", "ok"}
    last_tick = 0.0
    while time.time() < deadline:
        if abort_event is not None and getattr(abort_event, "is_set", lambda: False)():
            _reason = str(getattr(abort_event, "_abort_reason", "") or "").strip() or "user_pause"
            cancel_run_job(
                job_id,
                error="任务已中止，停止等待手机本机执行",
                error_code="MOBILE_AWAIT_ABORTED",
                abort_reason=_reason,
            )
            job = get_run_job(job_id) or {"job_id": job_id}
            job = dict(job)
            job["status"] = "cancelled"
            job["error"] = job.get("error") or "任务已中止，停止等待手机本机执行"
            job["error_code"] = job.get("error_code") or "MOBILE_AWAIT_ABORTED"
            return job
        job = get_run_job(job_id)
        if not job:
            return {
                "job_id": job_id,
                "status": "error",
                "error": "run job 不存在",
            }
        st = str(job.get("status") or "").strip().lower()
        if st in terminal:
            return job
        now = time.time()
        if on_tick is not None and (now - last_tick) >= 5.0:
            last_tick = now
            try:
                on_tick(dict(job))
            except Exception:
                pass
        time.sleep(max(0.2, float(poll_interval_sec)))
    job = get_run_job(job_id) or {"job_id": job_id}
    job = dict(job)
    st = str(job.get("status") or "").strip().lower()
    if st == "pending":
        hint = (
            f"手机未领取任务（status=pending，等了 {int(timeout_sec)}s）。"
            "请确认：① APK 已配对且显示已连接；② 无障碍服务已开启（PcRunJobPoller 仅在无障碍内轮询）；"
            "③ 手机与 PC 同网可访问 Flask。"
        )
        code = "MOBILE_JOB_NOT_PICKED"
    elif st == "running":
        hint = (
            f"手机已领取但未回报完成（status=running，等了 {int(timeout_sec)}s）。"
            "请查看手机无障碍回放是否卡住或报错。"
        )
        code = "MOBILE_JOB_RUNNING_STALL"
    else:
        hint = f"等待手机本机执行超时（{int(timeout_sec)}s）；请在手机上完成该阶段后重试"
        code = "MOBILE_DEVICE_AWAIT_TIMEOUT"
    job["status"] = "error"
    job["error"] = job.get("error") or hint
    job["error_code"] = job.get("error_code") or code
    return job


def _safe_llm_status_payload(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(profile, dict) or not profile:
        return {
            "ready": False,
            "provider": "",
            "model": "",
            "profile_id": "",
            "message": "PC 未绑定或未激活大模型，请在 PC 端 AI 配置中添加并激活",
        }
    provider = str(profile.get("provider") or profile.get("type") or "").strip()
    model = str(
        profile.get("model")
        or profile.get("model_name")
        or profile.get("default_model")
        or ""
    ).strip()
    pid = str(profile.get("id") or "").strip()
    ready = bool(provider or model)
    return {
        "ready": ready,
        "provider": provider,
        "model": model,
        "profile_id": pid,
        "message": "就绪" if ready else "配置不完整，请检查 PC 端模型绑定",
    }


def _mobile_ai_chitchat_reply(message: str) -> Optional[Dict[str, Any]]:
    """短问候不走完整用例生成，避免无意义的长 prompt + LLM 耗时。"""
    raw = (message or "").strip()
    if not raw or len(raw) > 24:
        return None
    normalized = raw.rstrip("？?！!~～。.! ").strip().lower()
    greetings = {
        "你是谁", "你好", "您好", "在吗", "谢谢", "谢谢你",
        "hi", "hello", "hey", "帮助", "help", "你能做什么", "怎么用",
    }
    if normalized not in greetings and raw.strip().rstrip("？?！!") not in greetings:
        return None
    return {
        "case_name": "",
        "description": (
            "我是 Testory 手机端 AI 助手：你描述测试场景，我通过 PC 已绑定的大模型生成步骤；"
            "录制与回放都在手机本机完成。请直接说要测什么，例如：打开设置并开启飞行模式。"
        ),
        "expected_result": "",
        "steps": [],
    }


def _normalize_phone_ai_action(action: str) -> str:
    a = (action or "tap").strip().lower()
    mapping = {
        "click": "tap",
        "input_text": "input",
        "type": "input",
        "open_app": "tap",
        "close_app": "back",
        "assert_text": "assert",
        "assert_element": "assert",
    }
    return mapping.get(a, a)


def _mobile_ai_free_chat(message: str, profile: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    """自由对话：不用「强制 JSON 用例」系统提示，避免慢且答非所问。"""
    from ai_local_inference import local_ai_service
    from ai_multi_provider import dispatch_chat_completion_messages

    messages = [
        {
            "role": "system",
            "content": (
                "你是 Testory 手机端测试助手。用简洁中文回答。"
                "用户当前在「对话」模式：不要输出 JSON，不要假装已经在手机上点开了应用。"
                "若用户想生成可回放步骤，提示切换到「生成用例」模式后再描述场景。"
                "可简要说明建议步骤，但标明需手动切换模式才会生成用例。"
            ),
        },
        {"role": "user", "content": message},
    ]
    raw = dispatch_chat_completion_messages(
        messages,
        None,
        profile,
        local_ai_service,
        temperature=0.4,
        timeout=min(90, int(__import__("os").environ.get("LOCAL_LLM_TIMEOUT", "240") or 240)),
    )
    text = ""
    if isinstance(raw, dict):
        text = str(raw.get("content") or raw.get("text") or "").strip()
        if not text:
            # OpenAI-compat shape
            try:
                choices = raw.get("choices") or []
                if choices:
                    msg = (choices[0] or {}).get("message") or {}
                    text = str(msg.get("content") or "").strip()
            except Exception:
                pass
    elif isinstance(raw, str):
        text = raw.strip()
    if not text:
        text = "（模型未返回文本）请检查 PC 端 custom_openai 配置，或切换到「生成用例」模式重试。"
    return {
        "success": True,
        "case_name": "",
        "description": text[:4000],
        "expected_result": "",
        "steps": [],
        "mode": "chat",
        "ai_status": status,
    }


def list_accessible_projects(db: Any, user_id: int) -> List[Dict[str, Any]]:
    projects = db.get_user_projects(user_id) or []
    out: List[Dict[str, Any]] = []
    for p in projects:
        pid = p.get("id")
        if not pid:
            continue
        out.append({"id": int(pid), "name": p.get("name") or f"项目 #{pid}"})
    return out


def _merge_step_ir_into_mobile_spec(raw: Dict[str, Any]) -> Dict[str, Any]:
    ms = raw.get("mobile_spec")
    if isinstance(ms, str) and ms.strip():
        try:
            ms = json.loads(ms)
        except Exception:
            ms = {}
    if not isinstance(ms, dict):
        ms = {}
    for k in (
        "assert_text",
        "wait_duration_ms",
        "pre_wait_ms",
        "max_retries",
        "optional",
        "assert_type",
        "save_as",
        "key_code",
        "repeat_max",
        "until_assert_text",
        "captcha_hint",
        "captcha_fallback",
        "roi",
        "scroll_amount",
        "swipe_direction",
    ):
        if raw.get(k) is not None and k not in ms:
            ms[k] = raw.get(k)
    return ms


def push_case_to_pc(
    db: Any,
    user_id: int,
    *,
    project_id: int,
    name: str,
    steps: List[Dict[str, Any]],
    remote_case_id: Optional[int] = None,
    replace: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if project_id <= 0:
        return None, "缺少 project_id"
    if not db.check_project_access(user_id, int(project_id), "editor"):
        return None, "无项目编辑权限"
    if not isinstance(steps, list) or not steps:
        return None, "steps 为空"
    case_name = (name or "移动端用例").strip() or "移动端用例"
    if remote_case_id and int(remote_case_id) > 0:
        bundle, emsg = case_bundle(db, int(remote_case_id), user_id)
        if emsg:
            return None, emsg
        case_id = int(remote_case_id)
        case_name = (bundle.get("case") or {}).get("name") or case_name
        if replace:
            db.delete_case_steps(case_id)
        existing = db.get_case_steps(case_id, page=1, page_size=500)
        next_order = 1 if replace else len(existing) + 1
    else:
        case_id = db.create_test_case_v2(
            int(project_id),
            case_name,
            platform="android",
            case_type="ui",
        )
        next_order = 1
    created = 0
    for raw in steps:
        if not isinstance(raw, dict):
            continue
        ms = _merge_step_ir_into_mobile_spec(raw)
        db.create_test_step(
            case_id=case_id,
            step_order=int(raw.get("step_order") or next_order),
            action=(raw.get("action") or "tap").strip(),
            selector_type=(raw.get("selector_type") or raw.get("strategy") or "").strip(),
            selector_value=(raw.get("selector_value") or "").strip(),
            input_value=(raw.get("input_value") or "").strip(),
            description=(raw.get("description") or "").strip(),
            automation_layer="android",
            mobile_spec=json.dumps(ms, ensure_ascii=False),
        )
        next_order += 1
        created += 1
    return {
        "case_id": case_id,
        "name": case_name,
        "project_id": int(project_id),
        "project_name": _project_name(db, int(project_id)),
        "step_count": created,
    }, None


def _project_name(db: Any, project_id: int) -> str:
    try:
        row = db.get_project(project_id)
        if row and row.get("name"):
            return str(row["name"])
    except Exception:
        pass
    return f"项目 #{project_id}"


def register_sync_routes(app, *, api_error_handler, login_required, role_required=None):
    """注册 /api/mobile/sync/* 与设备 token 鉴权的 probe 路由。"""

    def _roles(*args):
        if role_required is None:
            return lambda f: f
        return role_required(*args)

    # ── 健康检查（无认证，供移动端探测服务器可达性）──
    @app.route("/api/ping", methods=["GET"])
    def api_ping():
        return jsonify({"success": True, "message": "pong", "server": "testory"})

    @app.route("/api/mobile/sync/pair/init", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_sync_pair_init():
        from database import Database
        from flask_login import current_user

        db = Database()
        tid = db.get_user_tenant_id(current_user.id)
        return jsonify(pair_code_payload(current_user.id, tid))

    @app.route("/api/mobile/sync/pair/confirm", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_pair_confirm():
        body = request.get_json(silent=True) or {}
        ok, msg, token = confirm_pair(body.get("code") or body.get("pair_code"), body.get("device_id"))
        if not ok:
            return jsonify({"success": False, "error": msg}), 400
        return jsonify({"success": True, "device_token": token})

    @app.route("/api/mobile/sync/cases", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_cases():
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        db = Database()
        cases = list_accessible_cases(db, int(meta["user_id"]))
        return jsonify({"success": True, "cases": cases})

    @app.route("/api/mobile/sync/projects", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_projects():
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        db = Database()
        projects = list_accessible_projects(db, int(meta["user_id"]))
        return jsonify({"success": True, "projects": projects})

    @app.route("/api/mobile/sync/cases/push", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_push_case():
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        body = request.get_json(silent=True) or {}
        project_id = int(body.get("project_id") or 0)
        name = (body.get("name") or "").strip()
        steps_in = body.get("steps") or []
        remote_case_id = body.get("remote_case_id")
        replace = body.get("replace", True) is not False
        db = Database()
        result, emsg = push_case_to_pc(
            db,
            int(meta["user_id"]),
            project_id=project_id,
            name=name,
            steps=steps_in if isinstance(steps_in, list) else [],
            remote_case_id=int(remote_case_id) if remote_case_id else None,
            replace=replace,
        )
        if emsg:
            return jsonify({"success": False, "error": emsg}), 400
        return jsonify({"success": True, **result})

    @app.route("/api/mobile/sync/cases/<int:case_id>/bundle", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_case_bundle(case_id: int):
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        db = Database()
        bundle, emsg = case_bundle(db, case_id, int(meta["user_id"]))
        if emsg:
            return jsonify({"success": False, "error": emsg}), 404
        return jsonify({"success": True, **bundle})

    @app.route("/api/mobile/sync/cases/<int:case_id>/steps", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_upload_steps(case_id: int):
        meta, err = resolve_device_token()
        if err:
            return err
        from database import Database

        body = request.get_json(silent=True) or {}
        steps_in = body.get("steps") or []
        if not isinstance(steps_in, list) or not steps_in:
            return jsonify({"success": False, "error": "steps 为空"}), 400
        db = Database()
        bundle, emsg = case_bundle(db, case_id, int(meta["user_id"]))
        if emsg:
            return jsonify({"success": False, "error": emsg}), 404
        existing = db.get_case_steps(case_id, page=1, page_size=500)
        next_order = len(existing) + 1
        created = 0
        for raw in steps_in:
            if not isinstance(raw, dict):
                continue
            ms = _merge_step_ir_into_mobile_spec(raw)
            db.create_test_step(
                case_id=case_id,
                step_order=int(raw.get("step_order") or next_order),
                action=(raw.get("action") or "tap").strip(),
                selector_type=(raw.get("selector_type") or raw.get("strategy") or "").strip(),
                selector_value=(raw.get("selector_value") or "").strip(),
                input_value=(raw.get("input_value") or "").strip(),
                description=(raw.get("description") or "").strip(),
                automation_layer="android",
                mobile_spec=json.dumps(ms, ensure_ascii=False),
            )
            next_order += 1
            created += 1
        return jsonify({"success": True, "created": created})

    @app.route("/api/mobile/sync/run", methods=["POST"])
    @login_required
    @_roles("admin", "tester", "project_manager", "test_lead")
    @api_error_handler
    def api_mobile_sync_run_enqueue():
        from database import Database
        from app import load_case_and_steps

        body = request.get_json(silent=True) or {}
        case_id = int(body.get("case_id") or 0)
        if case_id <= 0:
            return jsonify({"success": False, "error": "缺少 case_id"}), 400
        db = Database()
        case, steps = load_case_and_steps(case_id, db)
        if not case:
            return jsonify({"success": False, "error": "用例不存在"}), 404
        if not steps:
            return jsonify({"success": False, "error": "无步骤"}), 400
        exec_steps = []
        for step in steps:
            s = dict(step)
            s["selector_value"] = db.resolve_variables(
                step.get("selector_value", ""),
                project_id=case.get("project_id"),
                case_id=case_id,
            )
            s["input_value"] = db.resolve_variables(
                step.get("input_value", ""),
                project_id=case.get("project_id"),
                case_id=case_id,
            )
            exec_steps.append(s)
        from flask_login import current_user

        job_id = enqueue_run_job(
            case_id=case_id,
            steps=exec_steps,
            user_id=current_user.id,
            device_id=(body.get("device_id") or "").strip(),
            source="pc",
        )
        return jsonify({"success": True, "job_id": job_id, "step_count": len(exec_steps)})

    @app.route("/api/mobile/sync/run/pending", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_run_pending():
        meta, err = resolve_device_token()
        if err:
            return err
        # 无论有无 job，都记心跳：证明无障碍内 PcRunJobPoller 在跑
        try:
            touch_device_poll(
                meta.get("device_id") or "",
                user_id=int(meta.get("user_id") or 0),
                token=str(meta.get("token") or meta.get("device_token") or ""),
            )
        except Exception:
            pass
        # resolve_device_token 返回的 meta 未必带 raw token；用 Authorization 再触一次
        try:
            auth = (request.headers.get("Authorization") or "").strip()
            if auth.lower().startswith("bearer "):
                touch_device_poll(
                    meta.get("device_id") or "",
                    user_id=int(meta.get("user_id") or 0),
                    token=auth[7:].strip(),
                )
        except Exception:
            pass
        job_kind = (request.args.get("job_kind") or "").strip()
        job = pop_pending_run_for_device(
            meta.get("device_id") or "",
            job_kind=job_kind,
            user_id=int(meta.get("user_id") or 0),
        )
        if not job:
            return jsonify({"success": True, "has_job": False})
        return jsonify({
            "success": True,
            "has_job": True,
            "job_id": job["job_id"],
            "case_id": job["case_id"],
            "steps": normalize_device_steps(job.get("steps") or []),
            "job_kind": job.get("job_kind") or "run_steps",
            "job_meta": job.get("job_meta") or {},
        })

    @app.route("/api/mobile/sync/cases/pull-batch", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_cases_pull_batch():
        from database import Database

        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        case_ids = body.get("case_ids") or []
        if not case_ids:
            return jsonify({"success": False, "error": "请选择要拉取的用例"}), 400

        db = Database()
        bundles = []
        for cid in case_ids:
            try:
                bid = int(cid) if str(cid).isdigit() else None
                if bid is None:
                    continue
                cdata, _ = case_bundle(db, bid, int(meta["user_id"]))
                if cdata:
                    bundles.append(cdata)
            except Exception:
                continue
        return jsonify({"success": True, "bundles": bundles})

    @app.route("/api/mobile/sync/run/events", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_run_events_post():
        from database import Database

        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        case_id = body.get("case_id", 0)
        case_name = body.get("case_name", "")
        status = body.get("status", "success")
        error = body.get("error", "")
        device_model = body.get("device_model", "")
        android_version = body.get("android_version", "")
        device_name = body.get("device_name", "")
        results = body.get("results") or []
        total_steps = body.get("total_steps", 0)
        passed_steps = body.get("passed_steps", 0)
        duration_ms = body.get("duration_ms", 0)

        try:
            db = Database()
            run_id = db.create_run_history(
                case_id, status, 0, error,
                extracted_text=device_model,
                expected_text=android_version,
                test_type="android"
            )
            if isinstance(results, list):
                for i, r in enumerate(results):
                    if not isinstance(r, dict):
                        continue
                    s_status = "success" if r.get("success", True) else "error"
                    s_desc = r.get("stepDescription") or r.get("description") or ""
                    s_err = r.get("errorMessage") or r.get("error") or ""
                    db.create_step_result(
                        run_id, None,
                        r.get("stepIndex") or (i + 1),
                        r.get("action") or "",
                        s_desc,
                        android_version,
                        device_model,
                        s_status,
                        s_err,
                        device_name,
                        int(r.get("durationMs") or 0),
                    )
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("移动端运行记录保存失败")
            return jsonify({"success": False, "error": str(e)}), 500

        return jsonify({"success": True, "run_id": run_id})

    @app.route("/api/mobile/sync/run/<job_id>/events", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_run_events(job_id: str):
        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        if not append_run_events(job_id, body):
            return jsonify({"success": False, "error": "job 不存在"}), 404
        st = str(body.get("status") or "").strip().lower()
        err_code = str(body.get("error_code") or "").strip().upper()
        if st != "busy" and err_code != "MOBILE_BUSY":
            _persist_run_history(job_id, body, int(meta["user_id"]))
        return jsonify({"success": True})

    @app.route("/api/mobile/sync/run/<job_id>/status", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_run_job_status(job_id: str):
        """轻量级状态查询：手机回放中每步轮询，检测 PC 是否已取消。"""
        meta, err = resolve_device_token()
        if err:
            return err
        info = get_run_job_status_lite(job_id)
        if info is None:
            return jsonify({"success": False, "error": "job 不存在"}), 404
        # 手机端只需判断是否应中止
        info["should_abort"] = info["status"] == "cancelled"
        return jsonify({"success": True, **info})

    @app.route("/api/mobile/sync/ai/status", methods=["GET"])
    @api_error_handler
    def api_mobile_sync_ai_status():
        meta, err = resolve_device_token()
        if err:
            return err
        try:
            from ai_multi_provider import get_active_llm_profile

            profile = get_active_llm_profile()
        except Exception:
            profile = None
        payload = _safe_llm_status_payload(profile)
        return jsonify({
            "success": True,
            "connected": True,
            **payload,
        })

    @app.route("/api/mobile/sync/ai/generate", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_ai_generate():
        from database import Database

        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        user_message = (body.get("message") or "").strip()
        if not user_message:
            return jsonify({"success": False, "error": "请输入测试需求描述"}), 400
        # chat=短闲聊；agent=同一统一 Agent 工具循环；generate=仅生成 Android 步骤
        mode = (body.get("mode") or body.get("intent") or "agent").strip().lower()
        if mode in ("case", "steps", "plan", "generate_case"):
            mode = "generate"
        if mode in ("chat", "talk", "free"):
            mode = "chat"
        if mode in ("agent", "auto", "tool", ""):
            mode = "agent"
        if mode not in ("chat", "generate", "agent"):
            mode = "agent"

        user_id = int(meta["user_id"])
        session_id = (body.get("session_id") or body.get("agent_session_id") or "").strip()
        user_data = Database().get_user_by_id(user_id)
        project_name = (user_data.get("project_name") or user_data.get("username") or "") if user_data else ""

        try:
            from ai_multi_provider import get_active_llm_profile

            profile = get_active_llm_profile()
        except Exception:
            profile = None
        status = _safe_llm_status_payload(profile)
        if not status.get("ready"):
            return jsonify({
                "success": False,
                "error": status.get("message") or "PC 未绑定大模型",
                "ai_status": status,
            }), 400

        # 寒暄：本地即时回复（agent/chat 均可）
        chitchat = _mobile_ai_chitchat_reply(user_message)
        if chitchat is not None:
            return jsonify({
                "success": True,
                **chitchat,
                "mode": mode,
                "ai_status": status,
            })

        # 统一 Agent：与 /ai-test 同一工具循环（一脑多端双手）
        if mode == "agent":
            try:
                from ai_chat_tool_loop import ChatToolLoopParams, run_unified_agent_blocking
                from ai_local_inference import local_ai_service
                from agent_unified_session import snapshot_connected_hands

                hands = snapshot_connected_hands(user_id)
                # 本请求来自已配对手机 → 强制 phone 双手可用
                hands["phone"] = True
                use_desk = bool(hands.get("desktop"))
                params = ChatToolLoopParams(
                    message=user_message,
                    project_name=project_name or "mobile",
                    current_plan={},
                    history=[],
                    profile=profile if isinstance(profile, dict) else None,
                    legacy_model="",
                    page_snapshot="",
                    probe_registry=None,
                    probe_url="",
                    memory_context="",
                    dom_context_pack="",
                    interaction_context={
                        "entry": "mobile_apk",
                        "device_id": meta.get("device_id") or "",
                        "hands": hands,
                    },
                    test_scope=user_message,
                    platform_type="desktop" if use_desk else "auto",
                    allow_screen_tools=use_desk,
                    allow_desktop_windows_tools=True if use_desk else False,
                    allow_hermes_execute=False,
                    allow_refine_test_plan=False,
                    generate_case_after_run=False,
                    user_id=user_id,
                    agent_session_id=session_id or None,
                    connected_hands=hands,
                )
                _plan, tool_meta, reply = run_unified_agent_blocking(
                    local_ai_service=local_ai_service,
                    params=params,
                )
                vars_out = tool_meta.get("cross_end_vars") if isinstance(tool_meta, dict) else {}
                if not isinstance(vars_out, dict):
                    vars_out = {}
                # 变量一律字符串化，便于 APK JSON 解码
                vars_str = {
                    str(k): ("" if v is None else str(v))
                    for k, v in vars_out.items()
                }
                digest: List[str] = []
                if isinstance(tool_meta, dict):
                    raw_d = tool_meta.get("steps_digest") or tool_meta.get("mobile_steps_digest")
                    if isinstance(raw_d, list):
                        digest = [str(x) for x in raw_d if x]
                    # 从 tools 结果摘要拼一段（若有）
                    if not digest and tool_meta.get("tools_used"):
                        digest = [f"tool:{t}" for t in (tool_meta.get("tools_used") or [])[:20]]
                sid_out = session_id or "default"
                # 成功跨端 run 自动沉淀 Skill 草稿（失败不沉淀）
                promote_meta = None
                failed = bool(
                    (isinstance(tool_meta, dict) and (
                        tool_meta.get("mobile_flow_halted")
                        or tool_meta.get("desktop_flow_halted")
                        or tool_meta.get("failed")
                    ))
                )
                if not failed and (vars_str or (tool_meta or {}).get("tools_used")):
                    try:
                        from ai_modules.skills.promote_from_run import promote_unified_agent_meta

                        _p, promote_meta = promote_unified_agent_meta(
                            tool_meta if isinstance(tool_meta, dict) else {},
                            user_message=user_message,
                            reply=reply or "",
                            skill_name="",
                            session_id=sid_out,
                            force=False,
                        )
                    except Exception:
                        promote_meta = None
                return jsonify({
                    "success": True,
                    "mode": "agent",
                    "reply": reply,
                    "description": reply,
                    "variables": vars_str,
                    "tools_used": list((tool_meta or {}).get("tools_used") or []),
                    "steps_digest": digest,
                    "connected_hands": hands,
                    "session_id": sid_out,
                    "promote": promote_meta,
                    "ai_status": status,
                })
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception("移动端统一 Agent 失败")
                return jsonify({
                    "success": False,
                    "error": f"Agent 执行失败: {e}",
                    "ai_status": status,
                }), 500

        # 对话模式：短回复，不强制生成用例 JSON
        if mode == "chat":
            try:
                return jsonify(_mobile_ai_free_chat(user_message, profile, status))
            except ValueError as e:
                return jsonify({"success": False, "error": str(e), "ai_status": status}), 500
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception("移动端AI对话失败")
                return jsonify({
                    "success": False,
                    "error": f"AI对话失败: {str(e)}",
                    "ai_status": status,
                }), 500

        try:
            from ai_local_inference import local_ai_service

            result = local_ai_service.generate_case_and_steps(
                goal=user_message,
                project_name=project_name,
                profile=profile,
                platform_type="android",
            )
            steps = result.get("steps") or []
            android_steps = []
            for s in steps:
                android_steps.append({
                    "action": _normalize_phone_ai_action(s.get("action", "tap")),
                    "selector_type": s.get("selector_type", ""),
                    "selector_value": s.get("selector_value", ""),
                    "input_value": s.get("input_value", ""),
                    "description": s.get("description", ""),
                    "automation_layer": s.get("automation_layer", "android"),
                })
            meta_out = result.get("meta") if isinstance(result.get("meta"), dict) else {}
            return jsonify({
                "success": True,
                "mode": "generate",
                "case_name": result.get("case_name", "AI生成用例"),
                "description": result.get("description", ""),
                "expected_result": result.get("expected_result", ""),
                "steps": android_steps,
                "ai_status": {
                    **status,
                    "provider": meta_out.get("provider") or status.get("provider"),
                    "model": meta_out.get("model") or status.get("model"),
                },
            })
        except ValueError as e:
            return jsonify({"success": False, "error": str(e), "ai_status": status}), 500
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("移动端AI生成失败")
            return jsonify({
                "success": False,
                "error": f"AI生成失败: {str(e)}",
                "ai_status": status,
            }), 500

    @app.route("/api/mobile/sync/captcha/solve", methods=["POST"])
    @api_error_handler
    def api_mobile_sync_captcha_solve():
        """手机截验证码 ROI → PC VLM → 返回结构化解法供本机手势。"""
        meta, err = resolve_device_token()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        b64 = (body.get("image_base64") or "").strip()
        if not b64:
            return jsonify({"success": False, "error": "缺少 image_base64"}), 400
        import base64

        try:
            if "," in b64 and b64.startswith("data:"):
                b64 = b64.split(",", 1)[1]
            image_bytes = base64.b64decode(b64)
        except Exception:
            return jsonify({"success": False, "error": "image_base64 无效"}), 400
        hint = (body.get("captcha_hint") or body.get("hint") or "").strip()
        instruction = (body.get("instruction") or "").strip()
        try:
            from ai_vision_local import captcha_vision_solve

            raw = captcha_vision_solve(
                image_bytes,
                instruction=instruction,
                captcha_hint=hint,
            )
        except Exception as e:
            return jsonify({"success": False, "error": f"VLM 调用失败: {e}"}), 500
        solution = {}
        if raw:
            text = raw.strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    solution = parsed
            except Exception:
                solution = {"type": "unknown", "raw": text[:500]}
        return jsonify({
            "success": bool(solution) and solution.get("type") not in (None, "unknown"),
            "solution": solution,
            "raw": (raw or "")[:2000],
        })

    # Vision probe route removed — mobile mirror/vision feature retired


def _persist_run_history(job_id: str, payload: Dict[str, Any], user_id: int) -> None:
    """手机执行完成后写入 run_history（简化版）。"""
    with _LOCK:
        job = _RUN_JOBS.get(job_id) or {}
    case_id = int(job.get("case_id") or 0)
    if case_id <= 0:
        return
    try:
        from database import Database

        db = Database()
        status = "success" if (payload.get("status") or "") == "success" else "error"
        err = payload.get("error") or ""
        run_id = db.create_run_history(case_id, status, 0, err, "", "", test_type="android")
        results = payload.get("results") or []
        if isinstance(results, list):
            for i, r in enumerate(results):
                if not isinstance(r, dict):
                    continue
                db.create_step_result(
                    run_id,
                    None,
                    r.get("step_order") or (i + 1),
                    r.get("action") or "",
                    "",
                    "",
                    "",
                    r.get("status") or "success",
                    r.get("error") or "",
                    "",
                    0,
                )
    except Exception:
        pass
