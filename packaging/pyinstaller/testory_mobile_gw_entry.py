# -*- coding: utf-8 -*-
"""PyInstaller 入口：mobile_automation_gateway 独立进程。"""
from __future__ import annotations

import os

from install_paths import configure_install_root_env


def main() -> None:
    import uvicorn

    configure_install_root_env()
    port = int(os.environ.get("MOBILE_AGENT_GATE_PORT", "8777"))
    uvicorn.run("mobile_automation_gateway.main:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
