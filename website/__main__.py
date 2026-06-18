"""Compatibility shim — see projects/testory-website/."""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
_proj = _ROOT / "projects" / "testory-website"
for _p in (_proj, _ROOT / "packages"):
    _ps = str(_p)
    if _p.is_dir() and _ps not in sys.path:
        sys.path.insert(0, _ps)

from app import main  # noqa: E402

if __name__ == "__main__":
    main()
