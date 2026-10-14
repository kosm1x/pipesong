"""Pipesong MCP server (stdio). Run: `PYTHONPATH=src python -m pipesong.mcp.server`.

Wraps the existing REST API (roadmap B.1). Configured ONLY from the process env (no
`.env` is read on this path):
- PIPESONG_API_URL (required): base URL of the running API. Unset = refuse to start.
- PIPESONG_API_KEY (optional): set it to the API's API_KEY value when the API enforces one;
  sent as `Authorization: Bearer <key>`.

mcp 2.x renamed FastMCP to `MCPServer` (`mcp.server.fastmcp` is a stub that raises).
"""
import logging
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from pipesong.mcp import tools
from pipesong.mcp.client import api_url_from_env

INSTRUCTIONS = (
    "Manage Pipesong voice agents and place test phone calls. Start with `health`. "
    "An agent's voice, prompt and Flux end-of-turn tuning live on the agent record "
    "(agent_create / agent_update); call_start dials a REAL phone number; follow a call "
    "with call_status. agent_delete deactivates (soft delete, undo with agent_update). "
    "`health` is auth-exempt, so it does not validate PIPESONG_API_KEY: an HTTP 401 on any "
    "other tool means that key is wrong or unset."
)

_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)

mcp = MCPServer("pipesong", instructions=INSTRUCTIONS)
mcp.add_tool(tools.health, annotations=_READ)
mcp.add_tool(tools.agent_create, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                                             idempotent_hint=False, open_world_hint=False))
mcp.add_tool(tools.agent_update, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                                             idempotent_hint=True, open_world_hint=False))
mcp.add_tool(tools.agent_list, annotations=_READ)
mcp.add_tool(tools.agent_get, annotations=_READ)
mcp.add_tool(tools.agent_delete, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                                             idempotent_hint=True, open_world_hint=False))
mcp.add_tool(tools.call_start, annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                                           idempotent_hint=False, open_world_hint=True))
mcp.add_tool(tools.call_status, annotations=_READ)


def main() -> None:
    # stdout carries the MCP protocol; logging.basicConfig writes to stderr.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # Fail fast here, not at import: importing the module stays side-effect free for tests.
    try:
        api_url_from_env()
    except ToolError as e:
        sys.exit(f"pipesong MCP server not started: {e}")
    mcp.run("stdio")


if __name__ == "__main__":
    main()
