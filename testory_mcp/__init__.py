"""Testory MCP servers.

传输模式：
  stdio  — 原有行 JSON stdio（web.py / desktop.py / mobile.py）
  http   — JSON-RPC 2.0 Streamable HTTP（transport.py）

用法：
  python -m testory_mcp              → stdio 模式（默认 web）
  python -m testory_mcp.transport    → HTTP 模式（独立服务器）
"""
from __future__ import annotations
