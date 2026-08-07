#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CLI 入口：从命令行执行跨端计划（供 CI/CD 脚本调用）。

用法：
  # 从 JSON 文件执行
  python cross_end_cli.py --plan plan.json --project-id 1

  # 从已保存场景执行
  python cross_end_cli.py --scenario-id my-scenario --project-id 1

  # 通过 API 执行（远程模式）
  python cross_end_cli.py --plan plan.json --api-url http://localhost:5000 --api-token xxx

  # 从自然语言分解后执行
  python cross_end_cli.py --describe "先通过API创建用户，再在浏览器中验证登录" --project-id 1

输出：
  exit code 0 = success, 1 = failure, 2 = error
  JSON result to stdout
"""
from __future__ import annotations

import argparse
import json
import sys
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _load_plan_from_file(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        print(json.dumps({"ok": False, "error": f"文件不存在: {path}"}), file=sys.stderr)
        sys.exit(2)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(json.dumps({"ok": False, "error": f"JSON 解析失败: {e}"}), file=sys.stderr)
        sys.exit(2)
    if isinstance(raw, dict) and raw.get("plan"):
        return raw["plan"]
    return raw


def _execute_local(plan: Dict[str, Any], project_id: Optional[int] = None) -> Dict[str, Any]:
    """本地执行（需完整环境）。"""
    from ai_modules.execute.orchestrator import execute_cross_end_plan

    result = execute_cross_end_plan(
        plan,
        project_id=project_id,
        trigger_source="cli",
        record_history=True,
    )
    return result


def _execute_remote(
    plan: Dict[str, Any],
    api_url: str,
    api_token: str,
    project_id: Optional[int] = None,
    async_mode: bool = False,
) -> Dict[str, Any]:
    """远程 API 执行。"""
    import urllib.request
    import urllib.error

    url = api_url.rstrip("/") + "/api/ci/cross-end/runs"
    payload = {
        "plan": plan,
        "project_id": project_id,
        "async": async_mode,
        "trigger_source": "cli",
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}: {body[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not async_mode or not result.get("ok"):
        return result

    # 异步模式：轮询
    run_id = result.get("run_id")
    poll_url = result.get("poll_url")
    if not run_id or not poll_url:
        return result

    print(f"异步执行已启动 run_id={run_id}，轮询中...", file=sys.stderr)
    poll_full_url = api_url.rstrip("/") + poll_url
    for i in range(120):
        time.sleep(10)
        try:
            req2 = urllib.request.Request(
                poll_full_url,
                headers={"Authorization": f"Bearer {api_token}"},
            )
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                status_result = json.loads(resp2.read().decode("utf-8"))
            if status_result.get("terminal") or status_result.get("status") in ("success", "failed"):
                return status_result
            print(f"  poll {i+1}: status={status_result.get('status')}", file=sys.stderr)
        except Exception as e:
            print(f"  poll {i+1} error: {e}", file=sys.stderr)

    return {"ok": False, "error": "轮询超时", "run_id": run_id}


def _decompose_and_execute(
    description: str,
    project_id: Optional[int] = None,
) -> Dict[str, Any]:
    """自然语言分解 + 本地执行。"""
    from ai_modules.plan.plan_decomposer import CrossEndPlanDecomposer

    decomposer = CrossEndPlanDecomposer()
    result = decomposer.decompose_sync(description)
    if not result.get("ok"):
        return {"ok": False, "error": "分解失败", "warnings": result.get("warnings", [])}

    plan = result["plan"]
    if project_id:
        plan["project_id"] = project_id
    return _execute_local(plan, project_id)


def main():
    parser = argparse.ArgumentParser(description="Testory 跨端计划 CLI 执行器")
    parser.add_argument("--plan", help="CrossEndPlan JSON 文件路径")
    parser.add_argument("--scenario-id", help="已保存的场景 ID")
    parser.add_argument("--describe", help="自然语言场景描述（自动分解后执行）")
    parser.add_argument("--project-id", type=int, help="项目 ID")
    parser.add_argument("--api-url", help="远程 API URL（不指定则本地执行）")
    parser.add_argument("--api-token", help="远程 API Token")
    parser.add_argument("--async", dest="async_mode", action="store_true", help="异步模式")
    parser.add_argument("--output", default="-", help="输出文件路径（默认 stdout）")

    args = parser.parse_args()

    plan: Optional[Dict[str, Any]] = None

    if args.plan:
        plan = _load_plan_from_file(args.plan)
    elif args.scenario_id:
        if not args.api_url:
            # 本地加载场景
            from ai_modules.execute.orchestrator import get_cross_platform_scenario
            sc = get_cross_platform_scenario(args.scenario_id)
            if not sc:
                print(json.dumps({"ok": False, "error": f"场景不存在: {args.scenario_id}"}))
                sys.exit(1)
            plan = sc.get("plan") or sc
        else:
            # 远程加载场景
            import urllib.request
            url = args.api_url.rstrip("/") + f"/api/ai/cross-end/scenario/{args.scenario_id}"
            headers = {}
            if args.api_token:
                headers["Authorization"] = f"Bearer {args.api_token}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                sc_data = json.loads(resp.read().decode("utf-8"))
            if not sc_data.get("ok"):
                print(json.dumps(sc_data))
                sys.exit(1)
            plan = (sc_data.get("scenario") or {}).get("plan") or sc_data.get("scenario")
    elif args.describe:
        result = _decompose_and_execute(args.describe, args.project_id)
        _output_result(result, args.output)
        sys.exit(0 if result.get("ok") and result.get("success") else 1)
    else:
        parser.print_help()
        sys.exit(2)

    if not plan or not isinstance(plan, dict) or not plan.get("stages"):
        print(json.dumps({"ok": False, "error": "无效的 plan（缺少 stages）"}))
        sys.exit(2)

    if args.api_url and args.api_token:
        result = _execute_remote(plan, args.api_url, args.api_token, args.project_id, args.async_mode)
    else:
        result = _execute_local(plan, args.project_id)

    _output_result(result, args.output)
    success = result.get("ok") and result.get("success")
    sys.exit(0 if success else 1)


def _output_result(result: Dict[str, Any], output: str) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output == "-":
        print(text)
    else:
        Path(output).write_text(text, encoding="utf-8")
        print(f"结果已写入: {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
