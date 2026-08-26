# -*- coding: utf-8 -*-
"""python -m browser_runtime"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

try:
    from dotenv import load_dotenv

    try:
        from modules.core.install_paths import resolve_install_root

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

logging.basicConfig(level=os.environ.get("BROWSER_RUNTIME_LOG_LEVEL", os.environ.get("EMBEDDED_BROWSER_LOG_LEVEL", "INFO")))


def main() -> None:
    host = os.environ.get("BROWSER_RUNTIME_LISTEN", os.environ.get("EMBEDDED_BROWSER_GATE_LISTEN", "0.0.0.0"))
    port = int(os.environ.get("BROWSER_RUNTIME_PORT", os.environ.get("EMBEDDED_BROWSER_GATE_PORT", "8765")))
    uvicorn.run(
        "browser_runtime.main:app",
        host=host,
        port=port,
        log_level=os.environ.get("UVICORN_LOG_LEVEL", "info"),
        ws_ping_interval=20,
        ws_ping_timeout=25,
    )


if __name__ == "__main__":
    main()
