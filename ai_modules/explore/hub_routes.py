"""探索测试 API 路由端点。"""

from __future__ import annotations

from flask import Blueprint, request, jsonify

from . import ExplorationBudget, ExplorationStrategy, ExplorationContext
from .exploration_engine import WebExplorer, DesktopExplorer, AnomalyDetector, ExplorationReporter

explore_bp = Blueprint("explore", __name__, url_prefix="/api/ai/explore")

_active_session: dict = {}


@explore_bp.route("/start", methods=["POST"])
def start_explore():
    body = request.get_json(silent=True) or {}
    layer = (body.get("layer") or "web").lower()
    max_depth = int(body.get("max_depth", 5))
    max_steps = int(body.get("max_steps", 20))
    max_duration_s = float(body.get("max_duration_s", 120.0))
    scope = body.get("scope_urls", [])

    budget = ExplorationBudget(
        max_depth=max_depth,
        max_steps=max_steps,
        max_duration_s=max_duration_s,
        scope_urls=scope,
    )
    strategy_mode = body.get("strategy", ExplorationStrategy.GREEDY)
    strategy = ExplorationStrategy(mode=strategy_mode)
    ctx = ExplorationContext()

    result = {"layer": layer, "started_at": ctx.actions_taken}

    try:
        if layer == "web":
            from modules.web.browser_manager import get_page
            page = get_page()
            if page is None:
                return jsonify({"success": False, "error": "没有活动的浏览器页面"}), 400
            explorer = WebExplorer(ctx, budget, strategy)
            r = explorer.explore_page(page, page_label=body.get("page_label", "home"))
            result["explore_result"] = r
        elif layer == "desktop":
            explorer = DesktopExplorer(ctx, budget, strategy)
            r = explorer.explore_desktop(
                window_title_hint=body.get("window_title", ""),
                max_clicks=max_steps,
            )
            result["explore_result"] = r
        else:
            return jsonify({"success": False, "error": f"不支持的平台: {layer}"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    report = ExplorationReporter.build_report(ctx, budget)
    result["report"] = report
    result["success"] = True
    return jsonify(result)


@explore_bp.route("/status", methods=["GET"])
def explore_status():
    return jsonify({"active_sessions": len(_active_session)})


@explore_bp.route("/report", methods=["GET"])
def explore_report():
    return jsonify({"message": "报告已随 /start 一次性返回"})
