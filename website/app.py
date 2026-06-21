"""WSGI entry for monorepo — re-exports Flask app from projects/testory-website.

Do NOT copy this file alone to /opt/testory-website on the server.
Production deploy must use projects/testory-website/ (see docs/DEPLOY_CLOUD.md).
"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
_proj = _ROOT / "projects" / "testory-website"
for _p in (_proj, _ROOT / "packages"):
    _ps = str(_p)
    if _p.is_dir() and _ps not in sys.path:
        sys.path.insert(0, _ps)

from app import app  # noqa: E402

__all__ = ["app"]
