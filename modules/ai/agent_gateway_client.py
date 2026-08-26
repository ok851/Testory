"""
Unified AI agent gateway client (Hermes default).

Environment:
- AI_AGENT_BACKEND — hermes (default)
- HERMES_GATEWAY_URL, HERMES_API_SERVER_KEY — see hermes_gateway_client.py
"""
from __future__ import annotations

from modules.hermes.hermes_gateway_client import HermesGatewayClient, hermes_tool_result_max_chars


def ai_agent_backend() -> str:
    return "hermes"


def get_agent_gateway_client() -> HermesGatewayClient:
    return HermesGatewayClient()


def agent_gateway_configured() -> bool:
    return get_agent_gateway_client().is_configured()


def agent_tool_result_max_chars() -> int:
    return hermes_tool_result_max_chars()
