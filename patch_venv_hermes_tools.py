# -*- coding: utf-8 -*-
"""
Testory 平台补丁脚本：优化 Hermes .venv 依赖包中的 browser 工具调用效率。

背景：browser_snapshot / browser_console 每次调用都会拉起 CLI 子进程且无缓存，
导致 agent 在思考流程中反复调用（用户反馈「一调用就很多次，太影响效率」）。
本脚本对依赖包做最小侵入补丁（带 [Testory-patch] 标记，可重复执行、幂等）。

⚠️ .venv 会被 pip 升级覆盖：升级后重新运行 `python patch_venv_hermes_tools.py` 即可恢复。

补丁内容：
1. tools/browser_tool.py
   a. 新增只读工具短 TTL 缓存（snapshot 3s / console 5s），命中返回 "cached": true
   b. browser_navigate 自动快照写入缓存 → 导航后模型首次 browser_snapshot() 直接命中
   c. _run_browser_command 内：页面变更命令（非只读）自动失效该任务缓存，防过期快照
   d. browser_console 空结果加引导文案（无日志时提示带 expression）
2. agent/tool_guardrails.py
   a. hard_stop_enabled 默认 True（工具死循环硬停止，与平台侧上限拦截形成纵深防御）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BROWSER_TOOL = ROOT / ".venv" / "Lib" / "site-packages" / "tools" / "browser_tool.py"
GUARDRAILS = ROOT / ".venv" / "Lib" / "site-packages" / "agent" / "tool_guardrails.py"

MARK = "# ===== [Testory-patch"


def _apply(path: Path, name: str, anchor: str, insert: str, *, after: bool = True) -> bool:
    """幂等插入：anchor 定位，insert 紧邻插入；已含标记则跳过。返回是否本次应用。"""
    src = path.read_text(encoding="utf-8")
    if MARK in src:
        # 已打过任意补丁——逐条用各自标记判断
        pass
    marker_tag = insert.splitlines()[0].strip()[:60]
    if marker_tag and marker_tag in src:
        print(f"  [skip] {name}: 已应用")
        return True  # 已应用视为该步骤成功（幂等）
    if anchor not in src:
        print(f"  [FAIL] {name}: 未找到锚点，跳过（依赖包版本可能已变化）")
        return False
    if after:
        new = src.replace(anchor, anchor + "\n" + insert, 1)
    else:
        new = src.replace(anchor, insert + "\n" + anchor, 1)
    if new == src:
        print(f"  [FAIL] {name}: 替换无变化")
        return False
    path.write_text(new, encoding="utf-8")
    print(f"  [ok] {name}")
    return True


def patch_browser_tool() -> bool:
    if not BROWSER_TOOL.exists():
        print(f"  [FAIL] 找不到 {BROWSER_TOOL}")
        return False
    ok = True

    # ── 1a. 缓存助手（模块级，logger 之后）──
    cache_helper = '''# ===== [Testory-patch-begin] browser 只读工具短 TTL 缓存 =====
# 避免 agent 对同一页面反复 snapshot/console 时每次重新拉起 CLI 子进程。
# 命中缓存返回时附带 "cached": true。pip 升级 .venv 后重跑本脚本恢复。
_BROWSER_READONLY_CACHE: Dict[str, Tuple[float, float, str]] = {}  # key -> (ts, ttl, payload)
_BROWSER_READONLY_CACHE_LOCK = threading.Lock()
_BROWSER_SNAPSHOT_TTL = 3.0   # 快照缓存 TTL（秒）
_BROWSER_CONSOLE_TTL = 5.0    # console 日志缓存 TTL（秒）
_BROWSER_CACHE_MAX = 96


def _browser_cache_get(key: str) -> Optional[str]:
    with _BROWSER_READONLY_CACHE_LOCK:
        item = _BROWSER_READONLY_CACHE.get(key)
        if item is None:
            return None
        ts, ttl, payload = item
        if (time.time() - ts) < ttl:
            return payload
        _BROWSER_READONLY_CACHE.pop(key, None)
        return None


def _browser_cache_put(key: str, payload: str, ttl: float) -> None:
    with _BROWSER_READONLY_CACHE_LOCK:
        _BROWSER_READONLY_CACHE[key] = (time.time(), ttl, payload)
        if len(_BROWSER_READONLY_CACHE) > _BROWSER_CACHE_MAX:
            now = time.time()
            stale = [
                k for k, (ts, ttl, _) in _BROWSER_READONLY_CACHE.items()
                if (now - ts) >= ttl
            ]
            for k in stale:
                _BROWSER_READONLY_CACHE.pop(k, None)
            # 仍超上限（过期项不足）：按最旧淘汰，保证不超过上限
            if len(_BROWSER_READONLY_CACHE) > _BROWSER_CACHE_MAX:
                oldest = sorted(
                    _BROWSER_READONLY_CACHE.items(),
                    key=lambda kv: kv[1][0],
                )
                overflow = len(_BROWSER_READONLY_CACHE) - _BROWSER_CACHE_MAX
                for k, _ in oldest[:overflow]:
                    _BROWSER_READONLY_CACHE.pop(k, None)


def _browser_cache_invalidate(task_id: str) -> None:
    """页面状态可能变化时清空该任务全部只读缓存，避免返回过期快照。"""
    if not task_id:
        return
    prefix = f"{task_id}|"
    with _BROWSER_READONLY_CACHE_LOCK:
        for k in [k for k in _BROWSER_READONLY_CACHE if k.startswith(prefix)]:
            _BROWSER_READONLY_CACHE.pop(k, None)


def _browser_cached_json(key: str):
    """命中缓存返回 (payload_dict, True)；否则 (None, False)。"""
    raw = _browser_cache_get(key)
    if raw is None:
        return None, False
    try:
        payload = json.loads(raw)
    except Exception:
        payload = None
    if not isinstance(payload, dict) or not payload.get("success"):
        return None, False
    payload["cached"] = True
    return payload, True
# ===== [Testory-patch-end] browser 只读工具短 TTL 缓存 ====='''
    ok &= _apply(BROWSER_TOOL, "缓存助手",
                 "logger = logging.getLogger(__name__)", cache_helper)

    # ── 1b. _run_browser_command：变更命令失效缓存 ──
    invalidation = '''    # [Testory-patch] 页面变更命令（非只读）→ 失效该任务只读缓存，防过期快照
    if command not in ("snapshot", "console", "errors", "get_images", "images", "vision"):
        _browser_cache_invalidate(task_id)'''
    ok &= _apply(BROWSER_TOOL, "命令失效缓存",
                 "    timeout = _get_command_timeout()\n    args = args or []", invalidation)

    # ── 1c. browser_snapshot：读缓存 ──
    snap_read = '''    # [Testory-patch] snapshot TTL 缓存命中（同任务同参数，避免重复拉起 CLI）
    _snap_cache_key = f"{effective_task_id}|snapshot|{full}|{user_task or ''}"
    _snap_cached, _snap_hit = _browser_cached_json(_snap_cache_key)
    if _snap_hit:
        return json.dumps(_snap_cached, ensure_ascii=False)'''
    ok &= _apply(BROWSER_TOOL, "snapshot 读缓存",
                 "    effective_task_id = _last_session_key(task_id or \"default\")\n\n    # Build command args based on full flag",
                 snap_read)

    # ── 1d. browser_snapshot：写缓存 ──
    snap_write = '''        # [Testory-patch] snapshot 成功结果写入 TTL 缓存
        try:
            _browser_cache_put(
                _snap_cache_key,
                json.dumps(response, ensure_ascii=False),
                _BROWSER_SNAPSHOT_TTL,
            )
        except Exception:
            pass'''
    ok &= _apply(BROWSER_TOOL, "snapshot 写缓存",
                 "        logger.debug(\"supervisor snapshot merge failed: %s\", _sv_exc)\n\n        return json.dumps(response, ensure_ascii=False)",
                 snap_write)

    # ── 1e. browser_navigate：自动快照写入缓存 ──
    nav_fill = '''                # [Testory-patch] 导航自动快照写入只读缓存：模型随后的 browser_snapshot() 直接命中
                try:
                    _browser_cache_put(
                        f"{nav_session_key}|snapshot|False|",
                        json.dumps(
                            {"success": True, "snapshot": snapshot_text,
                             "element_count": len(refs) if refs else 0},
                            ensure_ascii=False,
                        ),
                        _BROWSER_SNAPSHOT_TTL,
                    )
                except Exception:
                    pass'''
    ok &= _apply(BROWSER_TOOL, "navigate 自动快照入缓存",
                 "                response[\"snapshot\"] = snapshot_text\n                response[\"element_count\"] = len(refs) if refs else 0",
                 nav_fill)

    # ── 1f. browser_console：读缓存（仅 clear=False 无表达式）──
    console_read = '''    # [Testory-patch] console 纯日志读取 TTL 缓存命中（clear=True 清空日志不缓存）
    _console_cache_key = f"{effective_task_id}|console|read"
    if not clear:
        _console_cached, _console_hit = _browser_cached_json(_console_cache_key)
        if _console_hit:
            return json.dumps(_console_cached, ensure_ascii=False)'''
    ok &= _apply(BROWSER_TOOL, "console 读缓存",
                 "    effective_task_id = _last_session_key(task_id or \"default\")\n\n    console_args = [\"--clear\"] if clear else []",
                 console_read)

    # ── 1g. browser_console：空结果引导 + 写缓存 ──
    console_write = '''    # [Testory-patch] 空结果引导文案 + 结果写入 TTL 缓存
    if not clear:
        if not messages and not errors:
            response["hint"] = (
                "当前无控制台日志/JS 错误。如需读取页面数据，"
                "请用 browser_console(expression=\\\"...\\\") 执行 JS 获取，不要重复读日志。"
            )
        try:
            _browser_cache_put(
                _console_cache_key,
                json.dumps(response, ensure_ascii=False),
                _BROWSER_CONSOLE_TTL,
            )
        except Exception:
            pass'''
    ok &= _apply(BROWSER_TOOL, "console 空结果引导+写缓存",
                 "    _copy_fallback_warning(response, console_result)\n    if errors_result.get(\"fallback_warning\") and not response.get(\"fallback_warning\"):\n        _copy_fallback_warning(response, errors_result)\n    return json.dumps(response, ensure_ascii=False)",
                 console_write)

    # ── 1h. _browser_eval：JS 执行可能改页面状态 → 失效缓存 ──
    eval_inv = '''    # [Testory-patch] JS eval 可能改变页面状态 → 失效只读缓存
    _browser_cache_invalidate(effective_task_id)'''
    ok &= _apply(BROWSER_TOOL, "browser_eval 失效缓存",
                 "    if _is_camofox_mode():\n        return _camofox_eval(expression, task_id)\n\n    effective_task_id = _last_session_key(task_id or \"default\")",
                 eval_inv)
    return ok


def patch_guardrails() -> bool:
    if not GUARDRAILS.exists():
        print(f"  [FAIL] 找不到 {GUARDRAILS}")
        return False
    src = GUARDRAILS.read_text(encoding="utf-8")
    anchor = "    hard_stop_enabled: bool = False"
    if "hard_stop_enabled: bool = True" in src:
        print("  [skip] hard_stop_enabled: 已开启")
        return True
    if anchor not in src:
        print("  [FAIL] hard_stop_enabled: 未找到默认值锚点")
        return False
    GUARDRAILS.write_text(
        src.replace(anchor, "    hard_stop_enabled: bool = True  # [Testory-patch] 工具死循环硬停止默认开启", 1),
        encoding="utf-8",
    )
    print("  [ok] hard_stop_enabled 默认 True")
    return True


def verify() -> bool:
    import py_compile
    ok = True
    for p in (BROWSER_TOOL, GUARDRAILS):
        try:
            py_compile.compile(str(p), doraise=True)
            print(f"  [verify] 语法 OK: {p.name}")
        except py_compile.PyCompileError as e:
            print(f"  [verify] 语法错误: {p.name}: {e}")
            ok = False
    return ok


def apply_patches(*, quiet: bool = True) -> bool:
    """幂等应用浏览器工具缓存补丁；供 ensure_hermes_home 启动钩子调用。"""
    import contextlib
    import io

    buf = io.StringIO()
    ctx = contextlib.redirect_stdout(buf) if quiet else contextlib.nullcontext()
    with ctx:
        ok1 = patch_browser_tool()
        ok2 = patch_guardrails()
    return bool(ok1 and ok2)


if __name__ == "__main__":
    print("== patch_venv_hermes_tools.py ==")
    ok1 = patch_browser_tool()
    ok2 = patch_guardrails()
    print("== 验证 ==")
    ok3 = verify()
    print("== 完成 ==")
    print("补丁结果:", "全部成功" if (ok1 and ok2 and ok3) else "有失败，请人工检查")
    sys.exit(0 if (ok1 and ok2 and ok3) else 1)
