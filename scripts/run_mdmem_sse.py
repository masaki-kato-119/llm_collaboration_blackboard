import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "markdown-hierarchical-memory", "src"))

# Avoid port collision with other MCP servers
os.environ.setdefault("FASTMCP_PORT", os.environ.get("FASTMCP_PORT", "8001"))
os.environ.setdefault("MDMEM_ROOT", os.environ.get("MDMEM_ROOT", os.path.join(ROOT, "markdown-hierarchical-memory", "memory")))
os.environ.setdefault("MDMEM_ACTOR", os.environ.get("MDMEM_ACTOR", "claude"))

import mdmem.server as server  # noqa: E402

if __name__ == "__main__":
    try:
        port = int(os.environ.get("FASTMCP_PORT", "8001"))
        server.mcp.settings.port = port
    except Exception:
        pass
    server.mcp.run(transport="sse")
