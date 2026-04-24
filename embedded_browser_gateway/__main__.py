# -*- coding: utf-8 -*-
"""python -m embedded_browser_gateway"""

from __future__ import annotations

import logging
import os

import uvicorn

logging.basicConfig(level=os.environ.get("EMBEDDED_BROWSER_LOG_LEVEL", "INFO"))


def main() -> None:
    host = os.environ.get("EMBEDDED_BROWSER_GATE_LISTEN", "0.0.0.0")
    port = int(os.environ.get("EMBEDDED_BROWSER_GATE_PORT", "8765"))
    uvicorn.run(
        "embedded_browser_gateway.main:app",
        host=host,
        port=port,
        log_level=os.environ.get("UVICORN_LOG_LEVEL", "info"),
        ws_ping_interval=20,
        ws_ping_timeout=25,
    )


if __name__ == "__main__":
    main()
