# -*- coding: utf-8 -*-
"""跨端联动新功能 API 烟雾测试。

用法:
    1. 先启动服务:  python app.py
    2. 再运行本脚本: python tests/smoke_cross_end_api.py [--base http://localhost:5000]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error

BASE = "http://localhost:5000"
PASS = 0
FAIL = 0


def api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def test_templates():
    print("\n=== 场景模板 ===")
    code, data = api("GET", "/api/ai/cross-end/templates")
    check("模板列表 200", code == 200)
    check("返回 success", data.get("success") is True)
    templates = data.get("templates") or []
    check("模板数 >= 5", len(templates) >= 5, f"(got {len(templates)})")

    # 获取单个模板
    code, data = api("GET", "/api/ai/cross-end/templates/ecommerce-order-verify")
    check("单模板 200", code == 200)
    check("含 plan_template", "plan_template" in (data.get("template") or {}))

    # 实例化模板
    code, data = api("POST", "/api/ai/cross-end/templates/ecommerce-order-verify/instantiate", {
        "parameters": {
            "api_base_url": "https://test.example.com",
            "product_id": "P-001",
            "web_order_url": "https://test.example.com/orders",
        }
    })
    check("实例化 200", code == 200)
    check("实例化 success", data.get("success") is True)
    plan = data.get("plan") or {}
    check("plan 含 stages", len(plan.get("stages") or []) >= 3)
    check("参数已替换", "test.example.com" in json.dumps(plan))


def test_version_management():
    print("\n=== 版本管理 ===")
    # 先创建一个场景
    scenario_id = "smoke-test-ver"
    plan = {
        "scenario": "烟雾测试",
        "stages": [{"id": "s1", "layer": "api", "label": "test"}],
    }
    code, data = api("POST", f"/api/ai/cross-end/scenario/{scenario_id}/versions", {
        "plan": plan, "message": "smoke v1"
    })
    # 某些路由可能不存在，检查
    if code == 404:
        check("版本保存路由存在", False, "(404 - route not found)")
        return
    check("版本保存 200", code == 200)

    # 查询版本历史
    code, data = api("GET", f"/api/ai/cross-end/scenario/{scenario_id}/versions")
    check("版本历史 200", code == 200)

    # diff
    code, data = api("GET", f"/api/ai/cross-end/scenario/{scenario_id}/diff?v1=1&v2=1")
    check("版本 diff 200", code == 200)


def test_timeline():
    print("\n=== 时间线 ===")
    code, data = api("GET", "/api/ai/cross-end/timeline")
    check("时间线列表 200", code == 200)
    check("返回 ok", data.get("ok") is True)


def test_performance():
    print("\n=== 性能监控 ===")
    code, data = api("GET", "/api/ai/cross-end/performance/trends")
    check("性能趋势 200", code == 200)


def test_multi_device():
    print("\n=== 多设备发现 ===")
    code, data = api("GET", "/api/ai/cross-end/multi-device/discover")
    check("设备发现 200", code == 200)
    check("返回 success", data.get("success") is True)


def test_debug_panel():
    print("\n=== 调试面板 ===")
    code, data = api("GET", "/api/ai/cross-end/debug/panel")
    check("调试面板 200", code == 200)
    check("返回 ok", data.get("ok") is True)


def test_cross_end_scenarios():
    print("\n=== 场景 CRUD ===")
    code, data = api("GET", "/api/ai/cross-end/scenarios/all")
    check("场景列表 200", code == 200)


def main():
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    args = parser.parse_args()
    BASE = args.base

    print(f"烟雾测试目标: {BASE}")
    print("=" * 50)

    test_templates()
    test_version_management()
    test_timeline()
    test_performance()
    test_multi_device()
    test_debug_panel()
    test_cross_end_scenarios()

    print("\n" + "=" * 50)
    print(f"结果: {PASS} ✅  {FAIL} ❌  共 {PASS + FAIL} 项")
    if FAIL:
        print("⚠️  有失败项，请检查服务是否正常运行")
        sys.exit(1)
    else:
        print("🎉 全部通过")


if __name__ == "__main__":
    main()
