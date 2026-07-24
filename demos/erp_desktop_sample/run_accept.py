# -*- coding: utf-8 -*-
"""真机验收：Fake ERP / @erp 别名桌面样例（订单号 API↔窗口标题）。

用法（仓库根）:
  python demos/erp_desktop_sample/run_accept.py
  python demos/erp_desktop_sample/run_accept.py --mode alias
  python demos/erp_desktop_sample/run_accept.py --mode alias --expect-missing
  python demos/erp_desktop_sample/run_accept.py --mismatch

退出码：0 通过；1 诚实失败；2 预检未通过；3 别名缺失（--expect-missing 时期望 0）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _inject_fake_erp_alias(alias: str, order_id: str, hold_sec: float) -> str:
    """临时写入 DESKTOP_APP_ALIASES（进程内），不改磁盘 .env。"""
    from ai_modules.execute.erp_desktop_sample import suggest_fake_erp_alias_entry

    entry = suggest_fake_erp_alias_entry(order_id=order_id, hold_sec=hold_sec)
    payload = {alias: entry}
    raw = json.dumps(payload, ensure_ascii=False)
    os.environ["DESKTOP_APP_ALIASES"] = raw
    return raw


def main() -> int:
    from ai_modules.execute.desktop_preflight import check_desktop_preflight
    from ai_modules.execute.erp_desktop_sample import (
        build_erp_desktop_sample_plan,
        resolve_erp_alias,
    )
    from ai_modules.execute.orchestrator import execute_cross_end_plan
    from desktop_run_context import reset_desktop_run_context

    ap = argparse.ArgumentParser()
    ap.add_argument("--order-id", default="ORD-DEMO-404")
    ap.add_argument(
        "--mode",
        choices=("fake", "alias"),
        default="fake",
        help="fake=内置 Fake ERP；alias=经 DESKTOP_APP_ALIASES @erp",
    )
    ap.add_argument("--alias", default="erp", help="别名键，默认 erp")
    ap.add_argument(
        "--no-inject-alias",
        action="store_true",
        help="alias 模式且未配置时不自动注入 Fake ERP（默认会注入以便本机验收）",
    )
    ap.add_argument(
        "--expect-missing",
        action="store_true",
        help="alias 模式：期望别名缺失（不注入），验收诚实失败文案",
    )
    ap.add_argument(
        "--mismatch",
        action="store_true",
        help="种子订单与窗口订单故意不一致，应诚实失败",
    )
    args = ap.parse_args()

    if args.expect_missing and args.mode != "alias":
        print("ACCEPT: FAIL --expect-missing 仅适用于 --mode alias", flush=True)
        return 1

    pre = check_desktop_preflight()
    print(json.dumps({"preflight": pre}, ensure_ascii=False, indent=2))
    if not pre.get("ok") and not args.expect_missing:
        print("ACCEPT: FAIL preflight", flush=True)
        return 2

    window_oid = args.order_id
    seed_oid = "ORD-MISMATCH-999" if args.mismatch else args.order_id
    alias_key = (args.alias or "erp").strip().lstrip("@").lower() or "erp"

    if args.mode == "alias":
        if args.expect_missing:
            os.environ.pop("DESKTOP_APP_ALIASES", None)
            plan = build_erp_desktop_sample_plan(
                order_id=window_oid,
                launch_mode="alias",
                alias=alias_key,
            )
            err = (plan.get("meta") or {}).get("alias_error") or ""
            print(json.dumps({"plan_meta": plan.get("meta")}, ensure_ascii=False, indent=2))
            if "DESKTOP_ALIAS_MISSING" in err:
                print("ACCEPT: PASS alias missing honestly reported", flush=True)
                return 0
            print("ACCEPT: FAIL expected DESKTOP_ALIAS_MISSING", flush=True)
            return 1

        launch, err = resolve_erp_alias(alias_key, order_id=window_oid)
        injected = None
        if err and not args.no_inject_alias:
            injected = _inject_fake_erp_alias(alias_key, window_oid, hold_sec=45)
            print(
                json.dumps(
                    {"injected_DESKTOP_APP_ALIASES": injected, "note": "进程内临时注入 Fake ERP"},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            launch, err = resolve_erp_alias(alias_key, order_id=window_oid)
        if err:
            print(json.dumps({"alias_error": err}, ensure_ascii=False, indent=2))
            print("ACCEPT: FAIL alias not ready", flush=True)
            return 3

        plan = build_erp_desktop_sample_plan(
            order_id=window_oid,
            hold_sec=45,
            launch_mode="alias",
            alias=alias_key,
        )
        if (plan.get("meta") or {}).get("alias_error"):
            print(json.dumps({"plan_meta": plan.get("meta")}, ensure_ascii=False, indent=2))
            print("ACCEPT: FAIL alias_error in plan", flush=True)
            return 3
    else:
        plan = build_erp_desktop_sample_plan(order_id=window_oid, hold_sec=45)

    plan["variables"] = {"api_order_id": seed_oid}
    reset_desktop_run_context()

    result = execute_cross_end_plan(
        plan,
        user_id="erp-accept",
        record_history=True,
        trigger_source="erp-sample-accept-" + args.mode,
    )
    summary = {
        "mode": args.mode,
        "alias": alias_key if args.mode == "alias" else None,
        "success": result.get("success"),
        "gate_passed": result.get("gate_passed"),
        "error": result.get("error"),
        "error_code": result.get("error_code"),
        "assertion_failed": result.get("assertion_failed"),
        "assertion_details": result.get("assertion_details"),
        "variables": result.get("variables") or result.get("context_variables"),
        "stage_results": [
            {
                "stage_id": s.get("stage_id"),
                "ok_assert": s.get("ok_assert"),
                "error": s.get("error"),
                "error_code": s.get("error_code"),
                "extracted": s.get("extracted"),
                "steps_executed": s.get("steps_executed"),
            }
            for s in (result.get("stage_results") or [])
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.mismatch:
        if result.get("success"):
            print("ACCEPT: FAIL expected mismatch but got success (假绿)", flush=True)
            return 1
        print("ACCEPT: PASS mismatch honestly failed", flush=True)
        return 0

    if result.get("success") and result.get("gate_passed") is not False:
        print("ACCEPT: PASS erp desktop sample (" + args.mode + ")", flush=True)
        return 0
    print("ACCEPT: FAIL (honest)", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
