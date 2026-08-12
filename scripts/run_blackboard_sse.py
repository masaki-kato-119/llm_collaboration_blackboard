import os
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("BLACKBOARD_ROOT", os.environ.get("BLACKBOARD_ROOT", os.path.join(ROOT, "demo_blackboard")))

import blackboard.server as server  # noqa: E402

if __name__ == "__main__":
    server.mcp.run(transport="sse")
