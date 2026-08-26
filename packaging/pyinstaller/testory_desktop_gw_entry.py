# -*- coding: utf-8 -*-
"""PyInstaller 入口：desktop_automation_gateway 独立进程。"""
from __future__ import annotations

import os

from modules.core.install_paths import configure_install_root_env


def main() -> None:
    import os

    import uvicorn

    configure_install_root_env()
    port = int(os.environ.get("DESKTOP_AGENT_GATE_PORT", "8766"))
    uvicorn.run("desktop_automation_gateway.main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
