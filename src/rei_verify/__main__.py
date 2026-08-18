"""python -m rei_verify entry point — MCP server 起動。"""
from __future__ import annotations

import sys

from .mcp import main

if __name__ == "__main__":
    sys.exit(main())
