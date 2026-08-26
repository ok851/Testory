"""桌面客户端 License API 应本地处理，不代理到团队服务器。"""
from modules.execution.execution_remote import is_local_client_api


def test_license_api_is_local_on_client():
    assert is_local_client_api("/api/license/info")
    assert is_local_client_api("/api/license/activate")
