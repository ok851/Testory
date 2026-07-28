# -*- coding: utf-8 -*-
"""企业增强包入口。"""

from .execution_farm import (
    dispatch_hint,
    dispatch_readiness,
    farm_summary,
    list_nodes,
    probe_node,
    register_node,
    remove_node,
    select_preferred_node,
)
from .farm_batch import run_probe_fanout
from .farm_jobs import cancel_job, enqueue_job, get_job, jobs_summary, list_jobs, run_job
from .farm_worker import drain_queued_jobs, list_queued_jobs
from .gateway_resolve import farm_gateway_opt_in, resolve_desktop_gateway
from .readiness import enterprise_ops_readiness
from .sla_alerts import evaluate_sla_alerts
from .sla_evidence import list_metrics, record_metric, summarize_sla_evidence
from .sla_webhook import maybe_post_sla_webhook

__all__ = [
    "list_nodes",
    "register_node",
    "remove_node",
    "probe_node",
    "farm_summary",
    "select_preferred_node",
    "dispatch_hint",
    "dispatch_readiness",
    "enterprise_ops_readiness",
    "resolve_desktop_gateway",
    "farm_gateway_opt_in",
    "enqueue_job",
    "run_job",
    "cancel_job",
    "get_job",
    "list_jobs",
    "jobs_summary",
    "run_probe_fanout",
    "drain_queued_jobs",
    "list_queued_jobs",
    "record_metric",
    "list_metrics",
    "summarize_sla_evidence",
    "evaluate_sla_alerts",
    "maybe_post_sla_webhook",
]
