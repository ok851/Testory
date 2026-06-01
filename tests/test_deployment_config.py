"""部署模式配置测试。"""
from deployment_config import (
    DeploymentMode,
    get_deployment_mode,
    hide_billing_ui,
    is_client_mode,
    is_server_mode,
    is_standalone_mode,
)


def test_default_standalone(monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("UAT_DESKTOP_MODE", raising=False)
    assert get_deployment_mode() == DeploymentMode.STANDALONE
    assert is_standalone_mode()
    assert not hide_billing_ui()


def test_client_mode(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "client")
    assert get_deployment_mode() == DeploymentMode.CLIENT
    assert hide_billing_ui()


def test_server_mode(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "server")
    assert get_deployment_mode() == DeploymentMode.SERVER
    assert is_server_mode()
