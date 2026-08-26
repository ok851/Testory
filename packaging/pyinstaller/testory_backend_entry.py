# -*- coding: utf-8 -*-
"""PyInstaller onedir 入口：在保护版安装包中启动 Flask 后端（无安装目录根下的 app.py）。"""
from __future__ import annotations

import os
import runpy
import sys

from modules.core.install_paths import configure_install_root_env


def main() -> None:
    root = configure_install_root_env()
    os.chdir(root)
    runpy.run_module("app", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
