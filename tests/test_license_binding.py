"""License 双层绑定字段测试。"""
from license_manager import LicenseManager, LicenseType


def test_generate_with_binding_fields():
    lm = LicenseManager()
    key = lm.generate_license(
        LicenseType.PROFESSIONAL,
        issued_to="Test Co",
        expires_days=30,
        license_id="lic_test123",
        binding_type="",
        binding_id="",
        seat_count=5,
    )
    result = lm.validate_license(key)
    assert result["valid"] is True
    info = result["info"]
    assert info.license_id == "lic_test123"
    assert info.seat_count == 5


def test_binding_mismatch():
    lm = LicenseManager()
    key = lm.generate_license(
        LicenseType.PROFESSIONAL,
        issued_to="Test Co",
        expires_days=30,
        binding_type="instance",
        binding_id="inst_fixed_id_not_local",
    )
    result = lm.validate_license(key)
    assert result["valid"] is False
    assert "绑定" in result["message"]
