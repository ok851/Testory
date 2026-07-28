# -*- coding: utf-8 -*-
"""Phase B/C 收口：webhook 跳过逻辑 + readiness 标记。"""

from __future__ import annotations


def test_sla_webhook_skipped_without_url(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SLA_ALERT_WEBHOOK_URL", raising=False)
    from ai_modules.enterprise.sla_webhook import maybe_post_sla_webhook

    r = maybe_post_sla_webhook(force=True)
    assert r.get("skipped") is True
    assert r.get("posted") is False
    assert r.get("sla_met") is False
    assert r.get("sla_claim") is False


def test_ops_readiness_phase_bc_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.enterprise.readiness import enterprise_ops_readiness

    payload = enterprise_ops_readiness()
    assert payload.get("phase_bc_closed") is True
    assert payload.get("sla_claim") is False
    assert "PHASE_BC_COMPLETE" in (payload.get("doc") or "") or "phase" in (
        payload.get("disclaimer") or ""
    ).lower() or "收口" in (payload.get("disclaimer") or "")
