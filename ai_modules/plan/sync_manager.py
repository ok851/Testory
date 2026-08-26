from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from .context_bus import CrossEndContext

_POLL_INTERVAL_S = 0.5
_POLL_MAX_S = 30.0
_API_POLL_INTERVAL_S = 1.0
_API_POLL_MAX_S = 30.0
_UI_POLL_MAX_S = 60.0


def _is_missing_value(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str) and not val.strip():
        return True
    return False


def _as_float(val: Any, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _normalize_sync_spec(raw: Any) -> Optional[Dict[str, Any]]:
    """将 stage.wait_for / 简写字段归一为 {type, ...}。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return {"type": "time_sync", "seconds": float(raw)}
    if isinstance(raw, str):
        t = raw.strip().lower()
        if not t:
            return None
        if t in ("data_sync", "state_sync", "api_state_sync", "time_sync", "human_sync"):
            return {"type": t}
        # 裸字符串视为 time_sync 秒数失败 → 当作 data key 无意义；拒绝
        return {"type": t}
    if not isinstance(raw, dict):
        return None
    out = dict(raw)
    sync_type = (
        out.get("type")
        or out.get("sync_type")
        or out.get("kind")
        or ""
    )
    out["type"] = str(sync_type).strip().lower() or "data_sync"
    return out


def collect_stage_sync_specs(stage: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 stage 收集预执行同步规格（不含 HITL，HITL 仍走专用路径）。"""
    specs: List[Dict[str, Any]] = []
    if not isinstance(stage, dict):
        return specs

    vars_to_read = stage.get("vars_to_read")
    if isinstance(vars_to_read, str) and vars_to_read.strip():
        vars_to_read = [vars_to_read.strip()]
    if isinstance(vars_to_read, list) and vars_to_read:
        keys = [str(k).strip() for k in vars_to_read if str(k).strip()]
        if keys:
            specs.append({
                "type": "data_sync",
                "keys": keys,
                "timeout_s": _as_float(
                    stage.get("data_sync_timeout_s") or stage.get("sync_timeout_s"),
                    _POLL_MAX_S,
                ),
                "source": "vars_to_read",
            })

    # 顶层简写：data_sync / api_state_sync / state_sync / time_sync
    for key, default_type in (
        ("data_sync", "data_sync"),
        ("api_state_sync", "api_state_sync"),
        ("state_sync", "state_sync"),
        ("time_sync", "time_sync"),
    ):
        if key not in stage:
            continue
        raw = stage.get(key)
        if isinstance(raw, dict):
            spec = dict(raw)
            spec.setdefault("type", default_type)
        elif key == "time_sync" and isinstance(raw, (int, float)):
            spec = {"type": "time_sync", "seconds": float(raw)}
        elif key == "data_sync" and isinstance(raw, list):
            spec = {"type": "data_sync", "keys": raw}
        elif isinstance(raw, bool):
            # true 且无 keys → 无意义，跳过；false 忽略
            continue
        else:
            spec = _normalize_sync_spec(raw) or {"type": default_type}
        spec["type"] = str(spec.get("type") or default_type).strip().lower()
        spec["source"] = key
        specs.append(spec)

    wait_for = stage.get("wait_for")
    if wait_for is None and "sync" in stage:
        wait_for = stage.get("sync")
    if isinstance(wait_for, list):
        for item in wait_for:
            spec = _normalize_sync_spec(item)
            if spec:
                spec["source"] = "wait_for"
                specs.append(spec)
    elif wait_for is not None:
        spec = _normalize_sync_spec(wait_for)
        if spec:
            spec["source"] = "wait_for"
            specs.append(spec)

    return specs


class SyncPointManager:

    def __init__(self, context: CrossEndContext):
        self.context = context
        self._plan_stages: List[Dict[str, Any]] = []
        self._last_hitl_gate_id: Optional[str] = None

    def _lookup_variable(self, key: str) -> Any:
        key = (key or "").strip()
        if not key:
            return None
        val = self.context.get_variable(key)
        if val is not None:
            return val
        # 兼容嵌套解析 {{a.b}}
        try:
            return self.context._resolve_key(key)
        except Exception:
            return None

    def wait_for_data_sync(
        self,
        required_keys: List[str],
        max_wait_s: float = _POLL_MAX_S,
        interval_s: float = _POLL_INTERVAL_S,
    ) -> Tuple[bool, List[str], float]:
        """轮询等待上下文变量就绪。返回 (ok, missing_keys, waited_s)。"""
        keys = [str(k).strip() for k in (required_keys or []) if str(k).strip()]
        if not keys:
            return False, ["<empty_keys>"], 0.0

        max_wait_s = max(0.0, float(max_wait_s))
        interval_s = max(0.05, float(interval_s))
        waited = 0.0
        missing: List[str] = []

        while True:
            missing = [k for k in keys if _is_missing_value(self._lookup_variable(k))]
            if not missing:
                return True, [], waited
            if waited >= max_wait_s:
                return False, missing, waited
            sleep_for = min(interval_s, max(0.0, max_wait_s - waited))
            if sleep_for <= 0:
                return False, missing, waited
            time.sleep(sleep_for)
            waited += sleep_for

    def wait_for_ui_state(
        self,
        check_fn: Callable[[], bool],
        max_wait_s: float = 30.0,
        interval_s: float = 0.5,
        label: str = "ui_state",
    ) -> Tuple[bool, float]:
        waited = 0.0
        max_wait_s = max(0.0, float(max_wait_s))
        interval_s = max(0.05, float(interval_s))
        while waited < max_wait_s:
            try:
                if check_fn():
                    return True, waited
            except Exception:
                pass
            sleep_for = min(interval_s, max(0.0, max_wait_s - waited))
            if sleep_for <= 0:
                break
            time.sleep(sleep_for)
            waited += sleep_for
        return False, waited

    def wait_for_api_state(
        self,
        poll_fn: Callable[[], Optional[Any]],
        target_value: Any,
        json_path: str = "",
        max_wait_s: float = 30.0,
        interval_s: float = 1.0,
        compare: str = "equals",
    ) -> Tuple[bool, Any, float]:
        waited = 0.0
        last_val = None
        max_wait_s = max(0.0, float(max_wait_s))
        interval_s = max(0.05, float(interval_s))
        while waited < max_wait_s:
            try:
                val = poll_fn()
            except Exception:
                val = None
            last_val = val
            if val is not None or compare == "not_null":
                if compare == "equals" and val == target_value:
                    return True, val, waited
                if compare == "in" and isinstance(target_value, list) and val in target_value:
                    return True, val, waited
                if compare == "not_null" and val is not None:
                    return True, val, waited
                if compare == "gt" and isinstance(val, (int, float)) and val > target_value:
                    return True, val, waited
            sleep_for = min(interval_s, max(0.0, max_wait_s - waited))
            if sleep_for <= 0:
                break
            time.sleep(sleep_for)
            waited += sleep_for
        return False, last_val, waited

    def wait_for_time_sync(self, seconds: float) -> Tuple[bool, float]:
        seconds = max(0.0, float(seconds))
        if seconds > 0:
            time.sleep(seconds)
        return True, seconds

    def wait_for_human(
        self,
        prompt: str,
        timeout_s: float = 300.0,
        *,
        gate_id: str = "",
        user_id: str = "",
        hint: str = "",
        poll_interval_s: float = 0.25,
    ) -> bool:
        """阻塞等待人工确认。仅 resume 返回 True；超时/取消返回 False（禁止恒 True）。"""
        from modules.ai.agent_hitl import wait_hitl_gate

        plan_id = getattr(self.context, "plan_id", "") or "plan"
        gid = (gate_id or "").strip() or f"cross_end:{plan_id}:{uuid.uuid4().hex[:10]}"
        self._last_hitl_gate_id = gid
        try:
            self.context.set_variable("_hitl_gate_id", gid)
            self.context.set_variable("_hitl_prompt", prompt or "")
        except Exception:
            pass
        return wait_hitl_gate(
            gid,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            auto_open=True,
            reason=prompt or "等待人工确认",
            hint=hint or "",
            user_id=str(user_id or ""),
            scope="cross_end",
        )

    def _run_one_sync(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        sync_type = str(spec.get("type") or "").strip().lower()
        detail: Dict[str, Any] = {
            "type": sync_type or "unknown",
            "source": spec.get("source"),
            "ok": False,
        }

        if sync_type in ("data_sync", "data"):
            keys = spec.get("keys") or spec.get("required_keys") or spec.get("vars") or []
            if isinstance(keys, str):
                keys = [keys]
            if not isinstance(keys, list) or not keys:
                detail["error"] = "data_sync 未声明 keys / vars_to_read"
                detail["error_code"] = "SYNC_DATA_EMPTY_KEYS"
                return detail
            timeout_s = _as_float(
                spec.get("timeout_s") or spec.get("max_wait_s") or spec.get("timeout"),
                _POLL_MAX_S,
            )
            interval_s = _as_float(spec.get("interval_s"), _POLL_INTERVAL_S)
            ok, missing, waited = self.wait_for_data_sync(
                keys, max_wait_s=timeout_s, interval_s=interval_s
            )
            detail["waited_s"] = waited
            detail["keys"] = list(keys)
            if ok:
                detail["ok"] = True
            else:
                detail["missing"] = missing
                detail["error"] = f"data_sync 超时，缺失变量: {', '.join(missing)}"
                detail["error_code"] = "SYNC_DATA_TIMEOUT"
            return detail

        if sync_type in ("time_sync", "sleep", "wait"):
            if "seconds" in spec:
                seconds = _as_float(spec.get("seconds"), 0.0)
            elif "ms" in spec:
                seconds = _as_float(spec.get("ms"), 0.0) / 1000.0
            elif "wait_s" in spec:
                seconds = _as_float(spec.get("wait_s"), 0.0)
            elif "timeout_s" in spec:
                seconds = _as_float(spec.get("timeout_s"), 0.0)
            else:
                seconds = 0.0
            ok, waited = self.wait_for_time_sync(seconds)
            detail["ok"] = ok
            detail["waited_s"] = waited
            return detail

        if sync_type in ("api_state_sync", "api_state"):
            return self._run_api_state_sync(spec, detail)

        if sync_type in ("state_sync", "ui_state", "ui_state_sync"):
            return self._run_ui_state_sync(spec, detail)

        if sync_type in ("human_sync", "hitl"):
            # 编排主路径已有 HITL；此处若声明则真实等待，禁止恒 True
            prompt = str(spec.get("prompt") or spec.get("reason") or "等待人工确认")
            timeout_s = _as_float(spec.get("timeout_s") or spec.get("timeout"), 300.0)
            ok = self.wait_for_human(prompt, timeout_s=timeout_s)
            detail["ok"] = bool(ok)
            if not ok:
                detail["error"] = "human_sync 未通过（超时或取消）"
                detail["error_code"] = "SYNC_HITL_FAILED"
            return detail

        detail["error"] = f"未知同步类型: {sync_type or '(empty)'}"
        detail["error_code"] = "SYNC_UNKNOWN_TYPE"
        return detail

    def _run_api_state_sync(self, spec: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
        timeout_s = _as_float(
            spec.get("timeout_s") or spec.get("max_wait_s") or spec.get("timeout"),
            _API_POLL_MAX_S,
        )
        interval_s = _as_float(spec.get("interval_s"), _API_POLL_INTERVAL_S)
        json_path = str(spec.get("json_path") or spec.get("path") or "").strip()
        compare = str(spec.get("compare") or "equals").strip().lower()
        target = spec.get("equals", spec.get("target_value", spec.get("target")))
        if compare == "equals" and target is None and "not_null" not in str(spec.get("compare") or ""):
            # 允许仅 not_null
            if spec.get("not_null"):
                compare = "not_null"
            elif "equals" not in spec and "target_value" not in spec and "target" not in spec:
                compare = "not_null"

        request = spec.get("request") or spec.get("poll_request")
        if not isinstance(request, dict) or not request:
            detail["error"] = "api_state_sync 缺少 request"
            detail["error_code"] = "SYNC_API_NO_REQUEST"
            return detail

        from modules.integration.api_http_helper import execute_api_spec_sync, get_json_path_value

        def _poll() -> Any:
            try:
                resolved_req = self.context.resolve_deep(request)
            except Exception:
                resolved_req = request
            if not isinstance(resolved_req, dict):
                return None
            try:
                http_result = execute_api_spec_sync(
                    resolved_req,
                    resolve_text=lambda t: self.context.resolve(t) if isinstance(t, str) else t,
                )
            except Exception:
                return None
            if not isinstance(http_result, dict):
                return None
            body = http_result.get("response_json")
            if json_path:
                return get_json_path_value(body, json_path)
            return body

        ok, last_val, waited = self.wait_for_api_state(
            _poll,
            target_value=target,
            json_path=json_path,
            max_wait_s=timeout_s,
            interval_s=interval_s,
            compare=compare,
        )
        detail["waited_s"] = waited
        detail["last_value"] = last_val
        detail["compare"] = compare
        detail["target"] = target
        if ok:
            detail["ok"] = True
        else:
            detail["error"] = (
                f"api_state_sync 超时（compare={compare}, target={target!r}, last={last_val!r})"
            )
            detail["error_code"] = "SYNC_API_TIMEOUT"
        return detail

    def _run_ui_state_sync(self, spec: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
        timeout_s = _as_float(
            spec.get("timeout_s") or spec.get("max_wait_s") or spec.get("timeout"),
            _UI_POLL_MAX_S,
        )
        interval_s = _as_float(spec.get("interval_s"), _POLL_INTERVAL_S)

        # 变量条件：可单测、无浏览器依赖
        var_name = spec.get("variable") or spec.get("var") or spec.get("key")
        if var_name:
            compare = str(spec.get("compare") or "equals").strip().lower()
            target = spec.get("equals", spec.get("target_value", spec.get("target")))
            if spec.get("not_null") or compare == "not_null":
                compare = "not_null"

            def _check_var() -> bool:
                val = self._lookup_variable(str(var_name))
                if compare == "not_null":
                    return not _is_missing_value(val)
                if compare == "equals":
                    return val == target
                if compare == "in" and isinstance(target, list):
                    return val in target
                if compare == "gt" and isinstance(val, (int, float)) and isinstance(target, (int, float)):
                    return val > target
                return False

            ok, waited = self.wait_for_ui_state(
                _check_var, max_wait_s=timeout_s, interval_s=interval_s, label=str(var_name)
            )
            detail["waited_s"] = waited
            detail["variable"] = str(var_name)
            if ok:
                detail["ok"] = True
            else:
                detail["error"] = f"state_sync 变量条件未满足: {var_name}"
                detail["error_code"] = "SYNC_UI_VAR_TIMEOUT"
            return detail

        selector = str(spec.get("selector") or spec.get("css") or "").strip()
        if not selector:
            detail["error"] = "state_sync 需提供 variable 或 selector"
            detail["error_code"] = "SYNC_UI_NO_TARGET"
            return detail

        try:
            from modules.web.browser_manager import get_page

            page = get_page()
        except Exception:
            page = None
        if page is None:
            detail["error"] = "state_sync 需要浏览器页面，但 get_page() 为空"
            detail["error_code"] = "SYNC_UI_NO_PAGE"
            return detail

        state = str(spec.get("state") or "visible").strip() or "visible"

        def _check_sel() -> bool:
            try:
                loc = page.locator(selector)
                if state == "attached":
                    return loc.count() > 0
                # visible
                return bool(loc.first.is_visible())
            except Exception:
                return False

        ok, waited = self.wait_for_ui_state(
            _check_sel, max_wait_s=timeout_s, interval_s=interval_s, label=selector
        )
        detail["waited_s"] = waited
        detail["selector"] = selector
        if ok:
            detail["ok"] = True
        else:
            detail["error"] = f"state_sync 选择器超时: {selector}"
            detail["error_code"] = "SYNC_UI_SELECTOR_TIMEOUT"
        return detail

    def run_pre_stage_syncs(self, stage: Dict[str, Any]) -> Dict[str, Any]:
        """
        阶段执行前同步门禁。
        任一同步失败 → ok=False，禁止继续当绿。
        """
        specs = collect_stage_sync_specs(stage)
        if not specs:
            return {"ok": True, "syncs": [], "skipped": True}

        results: List[Dict[str, Any]] = []
        for spec in specs:
            # human_sync 交给编排 HITL，避免双重 gate；若仅出现在 wait_for 仍执行
            one = self._run_one_sync(spec)
            results.append(one)
            if not one.get("ok"):
                return {
                    "ok": False,
                    "syncs": results,
                    "error": one.get("error") or "同步门禁失败",
                    "error_code": one.get("error_code") or "SYNC_FAILED",
                }
        return {"ok": True, "syncs": results}

    def acquire(self, stage_id: str, depends_on: List[str]) -> bool:
        if not depends_on:
            return True
        for dep_sync in depends_on:
            dep_sync = str(dep_sync or "").strip()
            if not dep_sync:
                continue
            found = False
            for sid, sdata in self.context._stage_results.items():
                if not isinstance(sdata, dict):
                    continue
                # 跳过失败 / 未通过不得满足依赖
                if sdata.get("skipped_failure"):
                    continue
                if not (sdata.get("ok") or sdata.get("ok_assert")):
                    continue
                for ps in self._plan_stages:
                    if not isinstance(ps, dict):
                        continue
                    if ps.get("id") == sid and ps.get("sync_point") == dep_sync:
                        found = True
                        break
                if found:
                    break
            if not found:
                return False
        return True

    def set_plan_stages(self, stages: List[Dict[str, Any]]) -> None:
        self._plan_stages = list(stages or [])
