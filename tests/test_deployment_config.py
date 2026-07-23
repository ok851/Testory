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


def test_website_url_defaults_to_official(monkeypatch):
    from deployment_config import get_website_url
    from packages.testory_common.brand import OFFICIAL_WEBSITE_URL

    monkeypatch.delenv("WEBSITE_URL", raising=False)
    monkeypatch.setenv("DEPLOYMENT_MODE", "client")
    assert get_website_url() == OFFICIAL_WEBSITE_URL


def test_website_url_rejects_localhost_on_client(monkeypatch):
    from deployment_config import get_website_url
    from packages.testory_common.brand import OFFICIAL_WEBSITE_URL

    monkeypatch.setenv("DEPLOYMENT_MODE", "client")
    monkeypatch.setenv("WEBSITE_URL", "http://127.0.0.1:5200")
    assert get_website_url() == OFFICIAL_WEBSITE_URL


def test_website_url_allows_localhost_on_standalone(monkeypatch):
    from deployment_config import get_website_url

    monkeypatch.setenv("DEPLOYMENT_MODE", "standalone")
    monkeypatch.delenv("UAT_DESKTOP_MODE", raising=False)
    monkeypatch.setenv("WEBSITE_URL", "http://127.0.0.1:5200")
    assert get_website_url() == "http://127.0.0.1:5200"
