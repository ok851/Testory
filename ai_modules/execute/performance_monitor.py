# -*- coding: utf-8 -*-
"""跨端执行性能监控：指标收集/瓶颈分析/报告增强。

功能：
- 每次跨端执行自动收集性能指标
- 阶段级耗时分析（含设备/网络等待时间）
- 瓶颈识别（最慢阶段、最长等待、设备响应）
- 性能趋势（多次执行对比）
- JUnit XML 增强（含 time 属性和 failure detail）
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_METRICS: List[Dict[str, Any]] = []
_MAX_METRICS = 200


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metrics_dir() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    root = Path(env).expanduser().resolve() if env else Path(__file__).resolve().parents[2] / "data"
    d = root / "performance_metrics"
    d.mkdir(parents=True, exist_ok=True)
    return d


class PerformanceMetrics:
    """单次跨端执行的性能指标。"""

    def __init__(self, run_id: str, plan_id: str = "", scenario: str = ""):
        self.run_id = run_id
        self.plan_id = plan_id
        self.scenario = scenario
        self.started_at = _utc_iso()
        self.finished_at: str = ""
        self.total_ms: float = 0
        self.stage_metrics: List[Dict[str, Any]] = []
        self.device_metrics: Dict[str, Dict[str, Any]] = {}
        self.sync_wait_ms: float = 0
        self.hitl_wait_ms: float = 0
        self.risk_check_ms: float = 0
        self.overhead_ms: float = 0  # 编排器开销（非阶段执行时间）
        self.success: bool = False
        self._t0 = time.perf_counter()

    def record_stage(
        self,
        stage_id: str,
        layer: str,
        elapsed_ms: float,
        *,
        ok: bool,
        steps_executed: int = 0,
        executor: str = "",
        device_results: Optional[List[Dict[str, Any]]] = None,
        sync_wait_ms: float = 0,
    ) -> None:
        entry = {
            "stage_id": stage_id,
            "layer": layer,
            "elapsed_ms": elapsed_ms,
            "ok": ok,
            "steps_executed": steps_executed,
            "executor": executor,
            "sync_wait_ms": sync_wait_ms,
        }
        self.stage_metrics.append(entry)
        self.sync_wait_ms += sync_wait_ms

        for dr in (device_results or []):
            udid = dr.get("device_udid", "unknown")
            self.device_metrics[udid] = {
                "elapsed_ms": dr.get("elapsed_ms", 0),
                "ok": dr.get("ok", False),
                "steps_executed": dr.get("steps_executed", 0),
            }

    def record_hitl(self, elapsed_ms: float) -> None:
        self.hitl_wait_ms += elapsed_ms

    def record_risk_check(self, elapsed_ms: float) -> None:
        self.risk_check_ms += elapsed_ms

    def finish(self, success: bool) -> Dict[str, Any]:
        self.finished_at = _utc_iso()
        self.total_ms = round((time.perf_counter() - self._t0) * 1000, 1)
        self.success = success
        stage_total = sum(s.get("elapsed_ms", 0) for s in self.stage_metrics)
        self.overhead_ms = max(0, self.total_ms - stage_total)
        return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "scenario": self.scenario,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_ms": self.total_ms,
            "success": self.success,
            "stage_metrics": self.stage_metrics,
            "device_metrics": self.device_metrics,
            "sync_wait_ms": self.sync_wait_ms,
            "hitl_wait_ms": self.hitl_wait_ms,
            "risk_check_ms": self.risk_check_ms,
            "overhead_ms": self.overhead_ms,
            "bottleneck": self._find_bottleneck(),
            "summary": self._build_summary(),
        }

    def _find_bottleneck(self) -> Dict[str, Any]:
        """识别性能瓶颈。"""
        if not self.stage_metrics:
            return {"type": "none"}
        slowest = max(self.stage_metrics, key=lambda s: s.get("elapsed_ms", 0))
        slowest_pct = (slowest.get("elapsed_ms", 0) / self.total_ms * 100) if self.total_ms > 0 else 0
        bottleneck = {
            "stage_id": slowest.get("stage_id"),
            "layer": slowest.get("layer"),
            "elapsed_ms": slowest.get("elapsed_ms", 0),
            "percentage": round(slowest_pct, 1),
        }
        # 同步等待是否占大头
        if self.sync_wait_ms > self.total_ms * 0.3:
            return {"type": "sync_wait", "sync_wait_ms": self.sync_wait_ms, "detail": bottleneck}
        if self.hitl_wait_ms > self.total_ms * 0.3:
            return {"type": "hitl_wait", "hitl_wait_ms": self.hitl_wait_ms, "detail": bottleneck}
        if slowest_pct > 50:
            return {"type": "slow_stage", "detail": bottleneck}
        return {"type": "distributed", "detail": bottleneck}

    def _build_summary(self) -> Dict[str, Any]:
        """构建性能摘要。"""
        if not self.stage_metrics:
            return {}
        times = [s.get("elapsed_ms", 0) for s in self.stage_metrics]
        layers = {}
        for s in self.stage_metrics:
            l = s.get("layer", "unknown")
            layers.setdefault(l, []).append(s.get("elapsed_ms", 0))
        layer_summary = {
            l: {
                "count": len(v),
                "total_ms": sum(v),
                "avg_ms": round(statistics.mean(v), 1) if v else 0,
                "max_ms": max(v) if v else 0,
            }
            for l, v in layers.items()
        }
        return {
            "stage_count": len(self.stage_metrics),
            "total_stage_ms": sum(times),
            "avg_stage_ms": round(statistics.mean(times), 1) if times else 0,
            "max_stage_ms": max(times) if times else 0,
            "min_stage_ms": min(times) if times else 0,
            "layer_summary": layer_summary,
        }


def record_metrics(metrics: Dict[str, Any]) -> None:
    """记录性能指标到全局存储和磁盘。"""
    with _LOCK:
        _METRICS.append(metrics)
        if len(_METRICS) > _MAX_METRICS:
            _METRICS.pop(0)
    # 持久化
    try:
        run_id = metrics.get("run_id", "unknown")
        p = _metrics_dir() / f"{run_id}.json"
        p.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_performance_report(run_id: str) -> Optional[Dict[str, Any]]:
    """获取指定执行的性能报告。"""
    p = _metrics_dir() / f"{run_id}.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    with _LOCK:
        for m in _METRICS:
            if m.get("run_id") == run_id:
                return m
    return None


def get_performance_trends(scenario: str = "", limit: int = 10) -> List[Dict[str, Any]]:
    """获取性能趋势数据。"""
    with _LOCK:
        records = list(_METRICS)
    # 从磁盘加载
    for p in _metrics_dir().glob("*.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
            if m.get("run_id") and not any(r.get("run_id") == m["run_id"] for r in records):
                records.append(m)
        except Exception:
            pass
    if scenario:
        records = [r for r in records if scenario.lower() in str(r.get("scenario", "")).lower()]
    records.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return records[:limit]


def analyze_bottlenecks(run_id: str) -> Dict[str, Any]:
    """详细瓶颈分析。"""
    metrics = get_performance_report(run_id)
    if not metrics:
        return {"error": "未找到性能数据"}
    stages = metrics.get("stage_metrics") or []
    if not stages:
        return {"error": "无阶段数据"}

    analysis: Dict[str, Any] = {
        "run_id": run_id,
        "total_ms": metrics.get("total_ms", 0),
        "bottleneck": metrics.get("bottleneck", {}),
        "recommendations": [],
    }

    bottleneck = metrics.get("bottleneck", {})
    bn_type = bottleneck.get("type", "none")

    if bn_type == "sync_wait":
        analysis["recommendations"].append("同步等待占比较高，建议：检查上游阶段是否及时产出变量；增大 data_sync_timeout")
    elif bn_type == "hitl_wait":
        analysis["recommendations"].append("人机接管等待占比较高，建议：优化 HITL prompt；考虑自动化验证码提取")
    elif bn_type == "slow_stage":
        detail = bottleneck.get("detail", {})
        analysis["recommendations"].append(
            f"阶段 {detail.get('stage_id')} 占总耗时 {detail.get('percentage')}%，"
            f"建议：检查该阶段步骤数和设备响应时间"
        )

    # 检查移动端设备响应
    device_metrics = metrics.get("device_metrics") or {}
    for udid, dm in device_metrics.items():
        if dm.get("elapsed_ms", 0) > 10000:
            analysis["recommendations"].append(f"设备 {udid} 响应较慢 ({dm['elapsed_ms']:.0f}ms)，建议：检查网络或设备性能")

    # 检查各层耗时分布
    summary = metrics.get("summary", {})
    layer_summary = summary.get("layer_summary", {})
    for layer, ls in layer_summary.items():
        if ls.get("avg_ms", 0) > 30000:
            analysis["recommendations"].append(f"{layer} 层平均耗时 {ls['avg_ms']:.0f}ms，建议：优化该层步骤或增加超时")

    if not analysis["recommendations"]:
        analysis["recommendations"].append("性能良好，无明显瓶颈")

    return analysis


def generate_enhanced_junit(run_id: str, result: Dict[str, Any]) -> str:
    """生成增强版 JUnit XML（含详细时间、failure detail、performance 属性）。"""
    stage_results = result.get("stage_results") or []
    metrics = get_performance_report(run_id) or {}
    stage_metrics_map = {
        s["stage_id"]: s for s in (metrics.get("stage_metrics") or []) if s.get("stage_id")
    }

    elapsed_sec = (metrics.get("total_ms") or result.get("total_elapsed_ms") or 0) / 1000
    fail_count = sum(1 for s in stage_results if not s.get("ok_assert"))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(
        '<testsuites tests="{}" failures="{}" time="{:.2f}" '
        'name="cross-end-{}" timestamp="{}">'.format(
            len(stage_results), fail_count, elapsed_sec,
            run_id, metrics.get("started_at", ""),
        )
    )
    lines.append(
        '  <testsuite name="cross-end-{}" tests="{}" failures="{}" time="{:.2f}">'.format(
            run_id, len(stage_results), fail_count, elapsed_sec,
        )
    )

    for sr in stage_results:
        sid = sr.get("stage_id", "unknown")
        layer = sr.get("layer", "")
        ok = sr.get("ok_assert", False)
        sm = stage_metrics_map.get(sid, {})
        t = (sm.get("elapsed_ms") or sr.get("elapsed_ms") or 0) / 1000
        executor = sm.get("executor") or sr.get("executor", "")
        steps = sm.get("steps_executed") or sr.get("steps_executed", 0)

        attrs = 'name="{}" classname="cross-end.{}" time="{:.2f}"'.format(sid, layer, t)
        if executor:
            attrs += ' executor="{}"'.format(executor)
        if steps:
            attrs += ' steps="{}"'.format(steps)

        lines.append('    <testcase {}>'.format(attrs))
        if not ok:
            err = (sr.get("error") or "unknown").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            ec = sr.get("error_code", "")
            error_detail = json.dumps({
                "error_code": ec,
                "layer": layer,
                "executor": executor,
                "steps_executed": steps,
            }, ensure_ascii=False)
            lines.append(
                '      <failure message="{}" type="{}" detail="{}">{}</failure>'.format(
                    err, ec, error_detail.replace('"', '&quot;'), err
                )
            )
        lines.append('    </testcase>')

    lines.append('  </testsuite>')
    lines.append('</testsuites>')
    return "\n".join(lines)
