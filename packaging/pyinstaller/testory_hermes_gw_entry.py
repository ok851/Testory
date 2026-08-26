# -*- coding: utf-8 -*-
"""PyInstaller 入口：内嵌 Hermes Gateway（Testory AI）。"""
from __future__ import annotations

import os
import sys

from modules.core.install_paths import configure_install_root_env


def main() -> None:
    configure_install_root_env()
    try:
        from modules.hermes.hermes_config import ensure_hermes_home

        home = ensure_hermes_home()
        os.environ["HERMES_HOME"] = str(home.resolve())
    except Exception:
        pass
    os.environ.setdefault("API_SERVER_ENABLED", "true")
    sys.argv = ["hermes", "gateway"] + sys.argv[1:]
    from hermes_cli.main import main as hermes_main

    hermes_main()


if __name__ == "__main__":
    main()
