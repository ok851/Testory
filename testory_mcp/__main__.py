# -*- coding: utf-8 -*-
"""
python -m testory_mcp              → stdio JSON 模式（默认 web 平台）
python -m testory_mcp --http       → JSON-RPC 2.0 Streamable HTTP 模式
"""
import sys

if __name__ == "__main__":
    if "--http" in sys.argv:
        from testory_mcp.transport import main

        raise SystemExit(main() or 0)
    else:
        from testory_mcp.web import main

        raise SystemExit(main() or 0)
