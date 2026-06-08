# -*- coding: utf-8 -*-
"""
Deprecated compatibility shim — use browser_runtime instead.

Re-exports the Browser Runtime FastAPI app for legacy imports.
"""
from browser_runtime.main import *  # noqa: F401,F403
