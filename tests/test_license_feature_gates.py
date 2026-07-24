"""License 企业能力门禁：开源核心不锁执行；商业强制时按档位拦截。"""
from __future__ import annotations

import pytest

from license_manager import LicenseManager, LicenseType


@pytest.fixture()
def lm(tmp_path, monkeypatch):
    monkeypatch.delenv("LICENSE_ENFORCE_FEATURES", raising=False)
    monkeypatch.delenv("UAT_OPEN_FEATURES", raising=False)
    monkeypatch.setenv("DEPLOYMENT_MODE", "server")
    path = tmp_path / "license.key"
    # 禁止把仓库根目录已有 license.key 拷进 tmp，避免「免费档」测到企业证
    monkeypatch.setattr(
        LicenseManager,
        "_migrate_legacy_license_file",
        lambda self: None,
    )
    manager = LicenseManager(license_file=str(path))
    manager._cached_license = manager._create_default_free_license()
    return manager


def test_open_core_execution_always_available(lm, monkeypatch):
    monkeypatch.setenv("LICENSE_ENFORCE_FEATURES", "1")
    monkeypatch.setenv("DEPLOYMENT_MODE", "server")
    assert lm.check_feature_available("test_execution") is True
    assert lm.check_feature_available("basic_report") is True
    assert lm.check_feature_available("project_management") is True


def test_standalone_unlocks_enterprise_features(monkeypatch, tmp_path):
    monkeypatch.delenv("LICENSE_ENFORCE_FEATURES", raising=False)
    monkeypatch.setenv("DEPLOYMENT_MODE", "standalone")
    monkeypatch.setattr(
        LicenseManager,
        "_migrate_legacy_license_file",
        lambda self: None,
    )
    manager = LicenseManager(license_file=str(tmp_path / "license.key"))
    manager._cached_license = manager._create_default_free_license()
    assert manager.features_unlocked_for_open_core() is True
    assert manager.check_feature_available("sso") is True
    assert manager.check_feature_available("audit_log") is True
    assert manager.check_feature_available("customer_audit_export") is True
    assert manager.check_feature_available("ci_integration") is True


def test_enforce_blocks_enterprise_on_free(lm, monkeypatch):
    monkeypatch.setenv("LICENSE_ENFORCE_FEATURES", "1")
    monkeypatch.setenv("DEPLOYMENT_MODE", "server")
    assert lm.features_unlocked_for_open_core() is False
    assert lm.get_current_license().license_type == "free"
    assert lm.check_feature_available("sso") is False
    assert lm.check_feature_available("audit_log") is False
    assert lm.check_feature_available("customer_audit_export") is False
    assert lm.check_feature_available("ci_integration") is False
    gate = lm.describe_feature_gate("sso")
    assert gate["available"] is False
    assert gate["min_tier"] == "enterprise"
    assert gate["enforce"] is True


def test_enforce_enterprise_license_allows(lm, monkeypatch):
    monkeypatch.setenv("LICENSE_ENFORCE_FEATURES", "1")
    monkeypatch.setenv("DEPLOYMENT_MODE", "server")
    key = lm.generate_license(
        LicenseType.ENTERPRISE,
        issued_to="Gate Co",
        expires_days=30,
        license_id="lic_gate_ent",
    )
    result = lm.validate_license(key)
    assert result["valid"] is True
    lm._cached_license = result["info"]
    assert lm.check_feature_available("sso") is True
    assert lm.check_feature_available("customer_audit_export") is True
    assert lm.check_feature_available("ci_integration") is True
    assert "customer_audit_export" in (result["info"].features or [])


def test_enforce_professional_no_sso(lm, monkeypatch):
    monkeypatch.setenv("LICENSE_ENFORCE_FEATURES", "1")
    monkeypatch.setenv("DEPLOYMENT_MODE", "server")
    key = lm.generate_license(
        LicenseType.PROFESSIONAL,
        issued_to="Team Co",
        expires_days=30,
        license_id="lic_gate_pro",
    )
    result = lm.validate_license(key)
    assert result["valid"] is True
    lm._cached_license = result["info"]
    assert lm.check_feature_available("cross_end") is True
    assert lm.check_feature_available("schedule") is True
    assert lm.check_feature_available("sso") is False
    assert lm.check_feature_available("ci_integration") is False


def test_get_limits_exposes_catalog_and_effective(lm, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "standalone")
    monkeypatch.delenv("LICENSE_ENFORCE_FEATURES", raising=False)
    limits = lm.get_limits()
    assert "feature_catalog" in limits
    assert "sso" in limits["feature_catalog"]
    assert limits["open_core_features_unlocked"] is True
    assert "sso" in limits["effective_features"]
