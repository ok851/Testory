# -*- coding: utf-8 -*-
import os

import pytest


def test_desktop_lazy_gateway_default_with_desktop_mode(monkeypatch):
    monkeypatch.delenv("DESKTOP_LAZY_GATEWAY_BOOT", raising=False)
    monkeypatch.setenv("UAT_DESKTOP_MODE", "1")
    from modules.desktop.desktop_startup import desktop_lazy_gateway_boot

    assert desktop_lazy_gateway_boot() is True


def test_desktop_lazy_gateway_can_disable(monkeypatch):
    monkeypatch.setenv("DESKTOP_LAZY_GATEWAY_BOOT", "0")
    monkeypatch.setenv("UAT_DESKTOP_MODE", "1")
    from modules.desktop.desktop_startup import desktop_lazy_gateway_boot

    assert desktop_lazy_gateway_boot() is False


def test_startup_status_payload():
    from modules.desktop.desktop_startup import startup_status_payload

    payload = startup_status_payload()
    assert "phase" in payload
    assert "message" in payload
