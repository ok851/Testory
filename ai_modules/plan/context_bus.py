from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


_VAR_REF_PATTERN = re.compile(r"\{\{(.+?)\}\}")


class CrossEndContext:

    def __init__(self, plan_id: str = "", scenario: str = ""):
        self.plan_id = plan_id
        self.scenario = scenario
        self._variables: Dict[str, Any] = {}
        self._assertions: List[Dict[str, Any]] = []
        self._stage_results: Dict[str, Dict[str, Any]] = {}
        self._stage_order: List[str] = []
        self._errors: List[Dict[str, Any]] = []
        self.started_at: str = ""
        self.finished_at: str = ""

    def mark_start(self) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()

    def mark_finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def set_variable(self, key: str, value: Any) -> None:
        self._variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        return self._variables.get(key, default)

    def merge_stage_extraction(
        self, stage_id: str, extracted: Dict[str, Any]
    ) -> None:
        if not extracted:
            return
        self._stage_order.append(stage_id)
        for var_name, var_value in extracted.items():
            qualified = f"stage-N.{var_name}" if stage_id == "stage-N" else f"{stage_id}.{var_name}"
            self._variables[qualified] = var_value
            self._variables[var_name] = var_value

    def resolve(self, text: str) -> str:
        if not text or "{{" not in text:
            return text

        def _repl(m: re.Match) -> str:
            key = m.group(1).strip()
            val = self._resolve_key(key)
            return str(val) if val is not None else m.group(0)

        return _VAR_REF_PATTERN.sub(_repl, text)

    def _resolve_key(self, key: str) -> Any:
        if key in self._variables:
            return self._variables[key]

        parts = key.split(".")
        val: Any = self._variables
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                return None
        return val if val is not self._variables else None

    def resolve_deep(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.resolve(obj)
        if isinstance(obj, dict):
            return {k: self.resolve_deep(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.resolve_deep(item) for item in obj]
        return obj

    def record_stage_result(
        self,
        stage_id: str,
        result: Dict[str, Any],
        extracted: Optional[Dict[str, Any]] = None,
    ) -> None:
        ok = result.get("ok_assert") is True
        skipped_failure = bool(result.get("skipped_failure") or result.get("recovery_action") == "skip")
        self._stage_results[stage_id] = {
            "status_code": result.get("status_code"),
            "ok": ok,
            "error": result.get("error"),
            "elapsed_ms": result.get("elapsed_ms", 0),
            "extracted": extracted or {},
            "skipped_failure": skipped_failure,
            "recovery_action": result.get("recovery_action"),
            "cleanup": bool(result.get("cleanup")),
        }
        if extracted:
            self.merge_stage_extraction(stage_id, extracted)
        # 重试后以最新结论为准：先清掉该阶段旧错误
        self._errors = [e for e in self._errors if e.get("stage_id") != stage_id]
        if not ok:
            self._errors.append({
                "stage_id": stage_id,
                "error": result.get("error") or result.get("assert_message", "Unknown error"),
                "skipped_failure": skipped_failure,
            })

    def add_assertion(
        self, label: str, passed: bool, detail: str = ""
    ) -> None:
        self._assertions.append({
            "label": label,
            "passed": passed,
            "detail": detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_stage_result(self, stage_id: str) -> Optional[Dict[str, Any]]:
        return self._stage_results.get(stage_id)

    @property
    def all_passed(self) -> bool:
        return self.evaluate_pass(ignore_skipped_failures=False, ignore_cleanup_failures=False)

    def evaluate_pass(
        self,
        *,
        ignore_skipped_failures: bool = False,
        ignore_cleanup_failures: bool = True,
    ) -> bool:
        """评估是否通过。默认：跳过的失败仍算未通过；cleanup 失败默认可忽略。"""
        if not self._stage_results:
            return False
        for a in self._assertions:
            if not a["passed"]:
                return False
        for sid, sdata in self._stage_results.items():
            sdata = sdata or {}
            if sdata.get("ok"):
                continue
            if ignore_cleanup_failures and sdata.get("cleanup"):
                continue
            if ignore_skipped_failures and sdata.get("skipped_failure"):
                continue
            return False
        return True

    @property
    def pass_count(self) -> int:
        return sum(1 for a in self._assertions if a["passed"])

    @property
    def fail_count(self) -> int:
        return sum(1 for a in self._assertions if not a["passed"])

    def summary(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "scenario": self.scenario,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "all_passed": self.all_passed,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "stage_count": len(self._stage_results),
            "stage_order": list(self._stage_order),
            "variables": dict(self._variables),
            "assertions": list(self._assertions),
            "errors": list(self._errors),
        }

    def extract_from_screenshot(
        self,
        screenshot_path: str,
        hints: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        extracted: Dict[str, Any] = {}
        try:
            from desktop_ocr import extract_text
            raw = extract_text(screenshot_path)
        except Exception:
            return extracted
        if hints:
            import re as _re
            for hint in hints:
                var_name = hint.get("name", "")
                pattern = hint.get("pattern", "")
                if var_name and pattern:
                    m = _re.search(pattern, raw)
                    if m:
                        extracted[var_name] = m.group(1) if m.lastindex else m.group(0)
        return extracted

    def try_extract_keyword_from_screenshot(
        self,
        screenshot_path: str,
        keyword: str,
    ) -> Optional[str]:
        try:
            from desktop_ocr import find_text_location, engine_name
            loc = find_text_location(screenshot_path, keyword)
            return f"found@{loc}" if loc else None
        except Exception:
            return None

    def to_dict(self) -> Dict[str, Any]:
        return self.summary()
