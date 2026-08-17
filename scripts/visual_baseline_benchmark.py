# -*- coding: utf-8 -*-
"""Visual baseline benchmark scaffold.

Usage examples:
  python scripts/visual_baseline_benchmark.py --input benchmark_cases.json --out baseline_report.json

This script is intentionally lightweight: it provides the evaluation harness and
reporting structure first. Real UIA/OCR/Vision adapters should be wired case by case
into ``run_strategy(...)``.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List


STRATEGIES = ("uia", "ocr", "vision")


def run_strategy(strategy: str, case: Dict[str, Any]) -> Dict[str, Any]:
    start = time.time()
    ok = False
    note = ""
    try:
        if strategy == "uia":
            ok, note = _placeholder_uia(case)
        elif strategy == "ocr":
            ok, note = _placeholder_ocr(case)
        elif strategy == "vision":
            ok, note = _placeholder_vision(case)
        else:
            note = "未知策略"
    except Exception as exc:
        note = f"异常: {exc}"
    duration_ms = round((time.time() - start) * 1000, 2)
    return {
        "strategy": strategy,
        "ok": bool(ok),
        "note": note,
        "duration_ms": duration_ms,
    }


def _placeholder_uia(case: Dict[str, Any]) -> tuple[bool, str]:
    has_locator = bool(case.get("uia_locator") or case.get("automation_id"))
    return has_locator, "仅评测调度骨架，请接入真实 UIA 执行器"


def _placeholder_ocr(case: Dict[str, Any]) -> tuple[bool, str]:
    has_text = bool(case.get("expected_text") or case.get("ocr_hint"))
    return has_text, "仅评测调度骨架，请接入真实 OCR 路径"


def _placeholder_vision(case: Dict[str, Any]) -> tuple[bool, str]:
    has_image = bool(case.get("screenshot") or case.get("image_url"))
    return has_image, "仅评测调度骨架，请接入真实视觉 Grounding 模型"


def evaluate_cases(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {s: {"total": 0, "ok": 0} for s in STRATEGIES}
    details: List[Dict[str, Any]] = []

    for case in cases:
        case_result: Dict[str, Any] = {"case_id": case.get("case_id"), "results": {}}
        for strategy in STRATEGIES:
            result = run_strategy(strategy, case)
            case_result["results"][strategy] = result
            summary[strategy]["total"] += 1
            if result["ok"]:
                summary[strategy]["ok"] += 1
        details.append(case_result)

    rates = {}
    for strategy, data in summary.items():
        total = data["total"] or 1
        rates[strategy] = round(data["ok"] / total, 4)

    return {
        "summary": summary,
        "rates": rates,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the visual baseline benchmark scaffold")
    parser.add_argument("--input", required=True, help="JSON file containing benchmark cases")
    parser.add_argument("--out", required=True, help="Path to write baseline report")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        cases = json.load(f)

    report = evaluate_cases(cases)
    report["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps({"ok": True, "out": args.out, "rates": report["rates"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
