# -*- coding: utf-8 -*-
"""python -m embedded_browser_gateway"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

try:
    from dotenv import load_dotenv

    try:
        from install_paths import resolve_install_root

        _root = resolve_install_root()
    except ImportError:
        _root = Path(__file__).resolve().parent.parent
    _env = Path(os.environ.get("TESTORY_ENV_FILE") or (_root / ".env"))
    if not _env.is_file():
        _env = _root / ".env"
    if _env.is_file():
        load_dotenv(_env, encoding="utf-8-sig")
except ImportError:
    pass

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
