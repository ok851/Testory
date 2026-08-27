"""Hermes Agent 内嵌客户端与配置。"""
import json
from unittest.mock import MagicMock, patch

from modules.hermes.hermes_config import build_hermes_env_lines, ensure_hermes_home, hermes_skills_dir
from modules.hermes.hermes_gateway_client import HermesGatewayClient, _clip_tool_result


def test_build_hermes_env_contains_api_server(monkeypatch):
    monkeypatch.setenv("HERMES_API_SERVER_KEY", "test-key-123")
    text = build_hermes_env_lines()
    assert "API_SERVER_ENABLED=true" in text
    assert "test-key-123" in text


def test_build_hermes_env_excludes_skills_terminal(monkeypatch):
    monkeypatch.setenv("HERMES_API_SERVER_KEY", "test-key-123")
    text = build_hermes_env_lines()
    assert "browser" in text
    assert '"skills"' not in text and "'skills'" not in text
    assert "terminal" not in text.split("toolsets=", 1)[-1].split("\n", 1)[0]


def test_ensure_api_server_toolsets_drops_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from modules.hermes.hermes_config import ensure_hermes_api_server_toolsets, hermes_home_dir

    home = hermes_home_dir()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "platform_toolsets:\n  api_server:\n    - hermes-api-server\n",
        encoding="utf-8",
    )
    out = ensure_hermes_api_server_toolsets()
    assert out.get("ok")
    assert out.get("changed")
    text = (home / "config.yaml").read_text(encoding="utf-8")
    assert "browser" in text
    assert "skills" not in text
    assert "hermes-api-server" not in text



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
    with patch("modules.hermes.hermes_gateway_client.requests.post", return_value=mock_resp) as post:
        out = client.execute_user_instruction("explore login")
    assert "login" in out
    post.assert_called_once()
    assert "/v1/chat/completions" in post.call_args[0][0]


def test_clip_tool_result():
    long = "x" * 5000
    clipped = _clip_tool_result(long, max_chars=4500)
    assert len(clipped) <= 4500
    assert "truncated" in clipped
