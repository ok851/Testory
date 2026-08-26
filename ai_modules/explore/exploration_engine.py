"""AI 自主探索执行器：Web/Desktop 双端 + 异常检测 + 报告。"""

from __future__ import annotations

import base64
import math
import os
import random
import time
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import ExplorationBudget, ExplorationStrategy, ExplorationContext


class WebExplorer:

    def __init__(self, context: ExplorationContext, budget: ExplorationBudget, strategy: ExplorationStrategy):
        self.context = context
        self.budget = budget
        self.strategy = strategy

    def discover_interactive_elements(self, page: Any) -> List[Dict[str, Any]]:
        try:
            items = page.evaluate("""() => {
                const els = document.querySelectorAll('a, button, input, select, [role="button"], [onclick]');
                const results = [];
                els.forEach((el, i) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 5 || rect.height < 5 || rect.top < 0 || rect.left < 0) return;
                    results.push({
                        index: i,
                        tag: el.tagName.toLowerCase(),
                        text: (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').slice(0, 80),
                        selector: el.id ? '#' + el.id : (el.className ? '.' + el.className.split(' ')[0] : el.tagName.toLowerCase()),
                        rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height},
                        identifier: (el.id || el.getAttribute('data-testid') || el.getAttribute('name') || el.innerText?.slice(0, 30) || el.tagName + '-' + i)
                    });
                });
                return results;
            }""")
            return items if isinstance(items, list) else []
        except Exception:
            return []

    def _check_console_errors(self, page: Any) -> List[str]:
        """收集页面控制台错误（JS 异常 / 资源加载失败等）。"""
        try:
            errors = page.evaluate("""() => {
                const errs = [];
                if (window.__explore_console_errors) {
                    for (const e of window.__explore_console_errors.splice(0, 20)) {
                        errs.push(String(e).slice(0, 200));
                    }
                }
                return errs;
            }""")
            return errors if isinstance(errors, list) else []
        except Exception:
            return []

    def _install_console_listener(self, page: Any) -> None:
        """注入 JS 监听器，捕获 console.error 与未处理异常。"""
        try:
            page.evaluate("""() => {
                if (window.__explore_console_listener_installed) return;
                window.__explore_console_errors = [];
                window.addEventListener('error', (ev) => {
                    window.__explore_console_errors.push(ev.message || 'unknown error');
                });
                window.addEventListener('unhandledrejection', (ev) => {
                    window.__explore_console_errors.push('Promise rejection: ' + (ev.reason || ''));
                });
                window.__explore_console_listener_installed = true;
            }""")
        except Exception:
            pass

    def explore_page(self, page: Any, page_label: str = "") -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "page_label": page_label,
            "actions": [],
            "errors": [],
            "screenshots": [],
            "anomalies": [],
        }

        self._install_console_listener(page)

        try:
            elements = self.discover_interactive_elements(page)
        except Exception:
            elements = []
        if not elements:
            return result

        visited: set = set()
        last_url: str = ""
        try:
            last_url = page.url or ""
        except Exception:
            pass
        rediscovery_count = 0

        while self.budget.can_continue():
            candidate = self.strategy.select_next(
                [{"priority": 2 if e.get("tag") == "button" else 0, **e} for e in elements],
                visited,
            )
            if not candidate:
                break
            ident = candidate.get("identifier", "")
            visited.add(ident)
            self.budget.record_step(ident)
            sel = candidate.get("selector", "")
            text = candidate.get("text", "")[:40]

            start_ts = time.perf_counter()
            try:
                if candidate.get("tag") == "input":
                    try:
                        page.fill(sel, "test_explore")
                    except Exception:
                        page.click(sel)
                else:
                    page.click(sel, timeout=3000)
                page.wait_for_timeout(500)
                ok = True
                err_msg = None
            except Exception as e:
                ok = False
                err_msg = str(e)[:100]

            elapsed = round((time.perf_counter() - start_ts) * 1000, 1)

            # --- URL 变化后重新发现元素 ---
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""
            if current_url and current_url != last_url:
                last_url = current_url
                new_elements = self.discover_interactive_elements(page)
                if new_elements:
                    elements = new_elements
                    rediscovery_count += 1
                    self._install_console_listener(page)

            action_record = {
                "label": text,
                "selector": sel,
                "tag": candidate.get("tag", ""),
                "ok": ok,
                "error": err_msg,
                "elapsed_ms": elapsed,
            }
            result["actions"].append(action_record)
            self.context.record_action(action_record)

            if not ok and err_msg:
                self.context.record_error({
                    "page": page_label,
                    "action": text,
                    "error": err_msg,
                })
                result["errors"].append(action_record)

            # --- 截图 + 异常检测 ---
            try:
                ss_buf = page.screenshot(type="png")
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                ss_path = os.path.join(
                    os.environ.get("UAT_DATA_DIR", "."),
                    "explore_screenshots",
                    f"web_{page_label}_{ts}_{self.budget.steps_taken}.png",
                )
                os.makedirs(os.path.dirname(ss_path), exist_ok=True)
                with open(ss_path, "wb") as f:
                    f.write(ss_buf)
                result["screenshots"].append(ss_path)
                self.context.record_screenshot(ss_path)

                # 异常检测：白屏 / 低对比度
                ss_b64 = base64.b64encode(ss_buf).decode("ascii")
                for check_fn, label in (
                    (AnomalyDetector.check_white_screen, "white_screen"),
                    (AnomalyDetector.check_low_contrast, "low_contrast"),
                ):
                    detected, detail = check_fn(ss_b64)
                    if detected:
                        anomaly = {
                            "type": label,
                            "detail": detail,
                            "step": self.budget.steps_taken,
                            "page": page_label,
                        }
                        result["anomalies"].append(anomaly)
                        self.context.record_error(anomaly)
            except Exception:
                pass

            # --- 控制台错误检测 ---
            console_errs = self._check_console_errors(page)
            for cerr in console_errs[:5]:
                anomaly = {
                    "type": "console_error",
                    "detail": cerr,
                    "step": self.budget.steps_taken,
                    "page": page_label,
                }
                result["anomalies"].append(anomaly)
                self.context.record_error(anomaly)

        result["rediscovery_count"] = rediscovery_count
        return result


class DesktopExplorer:

    def __init__(self, context: ExplorationContext, budget: ExplorationBudget, strategy: ExplorationStrategy):
        self.context = context
        self.budget = budget
        self.strategy = strategy

    def explore_desktop(
        self,
        window_title_hint: str = "",
        max_clicks: int = 10,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {"actions": [], "errors": [], "screenshots": []}
        self.budget.max_steps = min(self.budget.max_steps, max_clicks or 10)

        try:
            from modules.desktop.desktop_automation import sync_desktop_execute_step
        except Exception:
            result["errors"].append({"error": "desktop_automation module not available"})
            return result

        regions: List[Dict[str, Any]] = []
        try:
            import mss
            with mss.mss() as sct:
                monitor = sct.monitors[0]
                w, h = monitor["width"], monitor["height"]
                cols, rows = 4, 3
                for r in range(rows):
                    for c in range(cols):
                        regions.append({
                            "x": int(c * w / cols),
                            "y": int(r * h / rows),
                            "w": int(w / cols),
                            "h": int(h / rows),
                        })
        except Exception:
            regions = [{"x": 100, "y": 100, "w": 300, "h": 300}]

        random.shuffle(regions)
        steps = min(len(regions), self.budget.max_steps)
        for i in range(steps):
            if not self.budget.can_continue():
                break
            reg = regions[i]
            cx = int(reg["x"] + reg["w"] / 2)
            cy = int(reg["y"] + reg["h"] / 2)
            start_ts = time.perf_counter()
            try:
                sync_desktop_execute_step({
                    "action": "click",
                    "selector_type": "viewport_coord",
                    "selector_value": f"{cx},{cy}",
                })
                ok = True
                err_msg = None
            except Exception as e:
                ok = False
                err_msg = str(e)[:100]
            elapsed = round((time.perf_counter() - start_ts) * 1000, 1)
            action_record = {
                "label": f"region[{i}] @({cx},{cy})",
                "ok": ok,
                "error": err_msg,
                "elapsed_ms": elapsed,
            }
            result["actions"].append(action_record)
            self.context.record_action(action_record)
            self.budget.record_step(f"desktop_region_{i}")
            if not ok and err_msg:
                self.context.record_error({"action": f"region[{i}]", "error": err_msg})
                result["errors"].append(action_record)
            time.sleep(0.3)

        return result


class AnomalyDetector:

    @staticmethod
    def check_white_screen(
        screenshot_base64: str,
        threshold: float = 0.97,
    ) -> Tuple[bool, str]:
        try:
            import numpy as np
            from PIL import Image
            img_data = base64.b64decode(screenshot_base64)
            img = Image.open(BytesIO(img_data)).convert("L")
            arr = np.array(img, dtype=np.float32) / 255.0
            bright_ratio = float(np.mean(arr > 0.95))
            if bright_ratio > threshold:
                return True, f"白屏检测: {bright_ratio:.1%} 像素接近白色"
            return False, ""
        except Exception:
            return False, ""

    @staticmethod
    def check_low_contrast(
        screenshot_base64: str,
        threshold: float = 10.0,
    ) -> Tuple[bool, str]:
        try:
            import numpy as np
            from PIL import Image
            img_data = base64.b64decode(screenshot_base64)
            img = Image.open(BytesIO(img_data)).convert("L")
            arr = np.array(img, dtype=np.float32)
            std = float(np.std(arr))
            if std < threshold:
                return True, f"低对比度: std={std:.1f}"
            return False, ""
        except Exception:
            return False, ""

    @staticmethod
    def check_console_errors(
        errors: List[str],
        critical_patterns: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """检查控制台错误是否包含关键异常模式。"""
        if not errors:
            return False, ""
        patterns = critical_patterns or [
            "Uncaught TypeError",
            "Uncaught ReferenceError",
            "Uncaught SyntaxError",
            "net::ERR_",
            "Failed to load resource",
            "CORS policy",
        ]
        hits: List[str] = []
        for err in errors:
            for pat in patterns:
                if pat in err:
                    hits.append(err[:120])
                    break
        if hits:
            return True, f"控制台关键错误 ({len(hits)}): {'; '.join(hits[:3])}"
        return False, ""


class ExplorationReporter:

    @staticmethod
    def build_report(context: ExplorationContext, budget: ExplorationBudget) -> Dict[str, Any]:
        actions = context.actions_taken
        ok_count = sum(1 for a in actions if a.get("ok"))
        fail_count = len(actions) - ok_count
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_actions": len(actions),
                "ok": ok_count,
                "failed": fail_count,
                "error_count": len(context.errors),
                "screenshots": len(context.screenshots),
                "budget_remaining": budget.max_steps - budget.steps_taken,
                "elapsed_s": round(budget.elapsed_s, 1),
                "time_budget_s": budget.max_duration_s,
                "timed_out": budget.timed_out,
                "progress_ratio": round(budget.progress_ratio, 3),
            },
            "errors": context.errors[:50],
            "actions": actions[:100],
            "screenshots": context.screenshots[:50],
        }
