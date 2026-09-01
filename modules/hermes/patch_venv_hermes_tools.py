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
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
_BROWSER_SNAPSHOT_TTL = 8.0   # 快照缓存 TTL（秒）；变更命令会主动失效缓存，可适当放宽
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
                 "logger.debug(\"supervisor snapshot merge failed: %s\", _sv_exc)",
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

    # ── 1i. _run_browser_command Popen：显式 cwd 防 \\?\ UNC 回退 ──
    cwd_main = '''            # ===== [Testory-patch] 显式 cwd =====
            # 继承的 \\\\?\\ 前缀 cwd（Tauri/长路径启动）会让 cmd.exe 拒绝：
            # "UNC paths are not supported. Defaulting to Windows directory."
            # → npx 的 .cmd shim 回退 C:\\Windows 运行，agent-browser daemon
            #   版本检测/缓存查找错乱（"Daemon version mismatch detected"）。
            # task_socket_dir 是普通临时目录路径，作 cwd 安全。
            _cwd = task_socket_dir
            if _cwd.startswith("\\\\\\\\?\\\\"):
                _cwd = _cwd[4:]
            proc = subprocess.Popen(
                cmd_parts,
                stdout=stdout_fd,
                stderr=stderr_fd,
                stdin=subprocess.DEVNULL,
                env=browser_env,
                cwd=_cwd,
                **_popen_extra,
            )'''
    ok &= _apply(BROWSER_TOOL, "Popen 显式 cwd（主命令）",
                 "            proc = subprocess.Popen(\n                cmd_parts,\n                stdout=stdout_fd,\n                stderr=stderr_fd,\n                stdin=subprocess.DEVNULL,\n                env=browser_env,\n                **_popen_extra,\n            )",
                 cwd_main)

    # ── 1j. 临时 Chrome 会话 _run_tmp Popen：同样显式 cwd ──
    cwd_tmp = '''            # ===== [Testory-patch] 显式 cwd（同 _run_browser_command，防 \\\\?\\ UNC 回退）=====
            _cwd = task_socket_dir
            if _cwd.startswith("\\\\\\\\?\\\\"):
                _cwd = _cwd[4:]
            proc = subprocess.Popen(
                full, stdout=stdout_fd, stderr=stderr_fd,
                stdin=subprocess.DEVNULL, env=browser_env,
                cwd=_cwd,
                **_popen_extra,
            )'''
    ok &= _apply(BROWSER_TOOL, "Popen 显式 cwd（临时会话）",
                 "            proc = subprocess.Popen(\n                full, stdout=stdout_fd, stderr=stderr_fd,\n                stdin=subprocess.DEVNULL, env=browser_env,\n                **_popen_extra,\n            )",
                 cwd_tmp)

    # ── 1k. _browser_eval：多行 JS 折叠，防 .cmd shim 经 cmd.exe 传参截断 ──
    eval_flat = '''    # ===== [Testory-patch] eval 表达式换行折叠 =====
    # Windows 下 npx 的 .cmd shim 经 cmd.exe 传参，多行 JS 会在首个换行处被
    # 截断 → "Evaluation error: SyntaxError: Unexpected end of input"。
    # 折叠为空格（截断必失败，折叠仅影响极少数依赖 ASI 的写法）。
    if "\\n" in expression or "\\r" in expression:
        expression = re.sub(r"[\\r\\n]+", " ", expression).strip()'''
    ok &= _apply(BROWSER_TOOL, "eval 换行折叠",
                 "    # --- Fallback: agent-browser CLI subprocess (original path) -------------",
                 eval_flat)

    # ── 1l. _browser_eval：统一换行折叠提前到函数级（快路径 + CLI 双路生效）──
    # 1k 只覆盖 CLI 兜底路径；快路径（CDPSupervisor）若先走则不会折叠，
    # 一旦落回 CLI 仍可能截断。统一折叠保证双路行为一致。
    eval_flat_global = '''    # ===== [Testory-patch] eval 表达式统一换行折叠（快路径 + CLI 双路生效）=====
    # Windows 下 npx 的 .cmd shim 经 cmd.exe 传参，多行 JS 会在首个换行处被
    # 截断 → "Evaluation error: SyntaxError: Unexpected end of input"。
    # 折叠为空格（截断必失败，折叠仅影响极少数依赖 ASI 的写法）。
    # 快路径 CDP 本身支持多行，但统一折叠保证双路行为一致、CLI 兜底不截断。
    _eval_orig_len = len(expression)
    if "\\n" in expression or "\\r" in expression:
        expression = re.sub(r"[\\r\\n]+", " ", expression).strip()'''
    ok &= _apply(BROWSER_TOOL, "eval 统一折叠（函数级）",
                 "    # [Testory-patch] JS eval 可能改变页面状态 → 失效只读缓存\n    _browser_cache_invalidate(effective_task_id)",
                 eval_flat_global)

    # ── 1m. _browser_eval：快路径 supervisor 惰性重试（时序竞态兜底）──
    # 平台浏览器可能刚就绪（先 browser_console 后浏览器拉起），supervisor 未注册时
    # 主动重试 attach；成功后走 CDP 快路径，绕开 CLI 子进程的 cmd.exe 传参问题。
    eval_sup_retry = '''        # [Testory-patch] 惰性 supervisor 未注册时主动重试 attach：平台浏览器可能
        # 刚就绪（时序竞态），重试后快路径生效，绕开 CLI 的 cmd.exe 传参截断/超长问题。
        if supervisor is None:
            try:
                _ensure_cdp_supervisor(effective_task_id)
                supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)
            except Exception:
                supervisor = None'''
    ok &= _apply(BROWSER_TOOL, "eval 快路径 supervisor 重试",
                 "        supervisor = SUPERVISOR_REGISTRY.get(effective_task_id)\n        if supervisor is not None:",
                 eval_sup_retry)

    # ── 1n. _browser_eval：CLI 超长保护（cmd.exe 8191 字符上限）──
    # 超长 JS 会被 cmd.exe 静默截断成误导性的 SyntaxError: Unexpected end of input，
    # 直接给模型明确指引，而不是把残句丢给子进程浪费时间。
    eval_long = '''    # ===== [Testory-patch] CLI 超长保护 =====
    # cmd.exe 命令行上限 8191 字符，超长 JS 会被静默截断成误导性的
    # SyntaxError: Unexpected end of input。超长时给模型明确指引，
    # 而不是把截断后的残句丢给子进程浪费时间。
    if len(expression) > 6000:
        response = {
            "success": False,
            "error": (
                "JS 表达式过长（"
                + str(len(expression))
                + " 字符），无法通过命令行子进程安全执行"
                "（Windows cmd.exe 上限约 8191 字符，截断会产生误导性语法错误）。"
                "请改用更精简的表达式，例如 document.body.innerText.slice(0,2000)，"
                "或将提取逻辑拆成多步、每步控制返回值长度。"
            ),
        }
        return json.dumps(response, ensure_ascii=False)'''
    ok &= _apply(BROWSER_TOOL, "eval CLI 超长保护",
                 '    result = _run_browser_command(effective_task_id, "eval", [expression])',
                 eval_long)
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


def check_agent_browser_cli() -> bool:
    """自检：agent-browser 是否已固定安装到 Hermes home node 目录。

    背景：browser_console 的 npx 兜底在 Windows 上每次重新下载（npm 缓存
    trash 失败 → 缓存无效化），导致 30s 超时 + 版本漂移。正确姿势是把
    agent-browser 固定装到 <hermes_home>/node（browser_tool 扩展 PATH 的
    候选目录之一，官方 install.sh 同路径），绕过 npx。幂等、只读。
    """
    try:
        from hermes_config import hermes_home_dir  # 同目录导入（本脚本位于 modules/hermes/）
        home = Path(hermes_home_dir())
    except Exception:
        base = os.environ.get("UAT_DATA_DIR") or os.environ.get("LOCALAPPDATA") or ""
        home = Path(base) / "Testory" / "hermes" if base else Path(".")
    shims = [
        home / "node" / "agent-browser.cmd",   # Windows npm --prefix 根 shim
        home / "node" / "bin" / "agent-browser",
        home / "node_modules" / ".bin" / "agent-browser",
    ]
    if any(p.is_file() for p in shims):
        print(f"  [ok] agent-browser 已固定安装（{shims[0].parent if shims[0].parent.exists() else home / 'node'}）")
        return True
    print("  [WARN] agent-browser 未固定安装到 Hermes home node 目录。")
    print("         browser_console 将退回 npx 每次下载（Windows 下易超时/版本漂移）。")
    print("         修复: npm install -g --prefix <hermes_home>/node agent-browser")
    print("               例如: node %NPM_PREFIX%/node_modules/npm/bin/npm-cli.js install \\")
    print("                     -g --prefix C:/Users/<user>/AppData/Local/Testory/hermes/node agent-browser")
    return True  # 仅自检，不阻断补丁流程


if __name__ == "__main__":
    print("== patch_venv_hermes_tools.py ==")
    ok1 = patch_browser_tool()
    ok2 = patch_guardrails()
    ok4 = check_agent_browser_cli()
    print("== 验证 ==")
    ok3 = verify()
    print("== 完成 ==")
    print("补丁结果:", "全部成功" if (ok1 and ok2 and ok3 and ok4) else "有失败，请人工检查")
    sys.exit(0 if (ok1 and ok2 and ok3 and ok4) else 1)
