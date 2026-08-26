"""License 双层绑定字段测试。"""
from modules.core.instance_identity import get_machine_id
from modules.auth.license_manager import LicenseManager, LicenseType

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


def test_activate_binds_machine_when_type_preset():
    lm = LicenseManager()
    key = lm.generate_license(
        LicenseType.PROFESSIONAL,
        issued_to="Test Co",
        expires_days=30,
        license_id="lic_bind1",
        binding_type="machine",
        binding_id="",
    )
    machine_id = get_machine_id()
    result = lm.activate_license_key(key, "machine", machine_id)
    assert result["valid"] is True
    info = result["info"]
    assert info.binding_type == "machine"
    assert info.binding_id == machine_id
    assert info.license_type == "professional"


def test_license_saved_under_uat_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "user_data"
    data_dir.mkdir()
    monkeypatch.setenv("UAT_DATA_DIR", str(data_dir))
    lm = LicenseManager()
    key = lm.generate_license(LicenseType.PROFESSIONAL, issued_to="Desktop", expires_days=30)
    assert lm.activate_license_key(key, "machine", get_machine_id())["valid"] is True
    assert (data_dir / "license.key").is_file()


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
