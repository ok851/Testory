"""uat_desktop 在安装布局下应能解析保护版后端路径。"""
from packaging import uat_desktop as ud


def test_backend_exe_path_protected_layout(tmp_path):
    runtime = tmp_path / "runtime" / "testory_app"
    runtime.mkdir(parents=True)
    backend = runtime / "TestoryBackend.exe"
    backend.write_bytes(b"")
    found = ud._backend_exe_path(tmp_path)
    assert found is not None
    assert found.resolve() == backend.resolve()
