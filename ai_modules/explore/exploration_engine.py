"""AI 自主探索执行器：Web/Desktop 双端 + 异常检测 + 报告。"""

from __future__ import annotations

import base64
import math
import os
import random
import time
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional

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

    def explore_page(self, page: Any, page_label: str = "") -> Dict[str, Any]:
        result: Dict[str, Any] = {"page_label": page_label, "actions": [], "errors": [], "screenshots": []}
        try:
            elements = self.discover_interactive_elements(page)
        except Exception:
            elements = []
        if not elements:
            return result

        visited: set = set()
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
            except Exception:
                pass

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
            from desktop_automation import sync_desktop_execute_step
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
            },
            "errors": context.errors[:50],
            "actions": actions[:100],
            "screenshots": context.screenshots[:50],
        }
