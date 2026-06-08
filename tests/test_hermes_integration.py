"""Hermes Agent 内嵌客户端与配置。"""
import json
from unittest.mock import MagicMock, patch

from hermes_config import build_hermes_env_lines, ensure_hermes_home, hermes_skills_dir
from hermes_gateway_client import HermesGatewayClient, _clip_tool_result


def test_build_hermes_env_contains_api_server(monkeypatch):
    monkeypatch.setenv("HERMES_API_SERVER_KEY", "test-key-123")
    text = build_hermes_env_lines()
    assert "API_SERVER_ENABLED=true" in text
    assert "test-key-123" in text


def test_ensure_hermes_home_creates_skills_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    home = ensure_hermes_home(force_env=True)
    assert home.is_dir()
    assert hermes_skills_dir().is_dir()
    assert (home / ".env").is_file()


def test_execute_user_instruction_calls_chat_completions(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://127.0.0.1:8642")
    monkeypatch.setenv("HERMES_API_SERVER_KEY", "k")
    client = HermesGatewayClient()
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.content = json.dumps(
        {"choices": [{"message": {"content": "explored login page"}}]}
    ).encode()
    mock_resp.json.return_value = json.loads(mock_resp.content.decode())
    with patch("hermes_gateway_client.requests.post", return_value=mock_resp) as post:
        out = client.execute_user_instruction("explore login")
    assert "login" in out
    post.assert_called_once()
    assert "/v1/chat/completions" in post.call_args[0][0]


def test_clip_tool_result():
    long = "x" * 5000
    clipped = _clip_tool_result(long, max_chars=4500)
    assert len(clipped) <= 4500
    assert "truncated" in clipped
