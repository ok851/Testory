# -*- coding: utf-8 -*-
"""PyInstaller 入口：Browser Runtime（画布 WS / inspect / run-steps）。"""
from __future__ import annotations

import os
import sys

from modules.core.install_paths import configure_install_root_env


def main() -> None:
    configure_install_root_env()
    from browser_runtime.__main__ import main as gw_main

    gw_main()


if __name__ == "__main__":
    main()
