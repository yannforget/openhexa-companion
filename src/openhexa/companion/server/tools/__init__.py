"""MCP tools, each a thin wrapper around a library operation."""

from mcp.server.mcpserver import MCPServer

from openhexa.companion.server.gateway import Gateway
from openhexa.companion.server.tools import datasets, workspaces
from openhexa.companion.server.tools._access import Access


def register_tools(server: MCPServer, access: Access, gateway: Gateway) -> None:
    """Add every tool to `server`, in the order clients list them."""
    workspaces.register(server, access)
    datasets.register(server, access, gateway)


__all__ = ["Access", "register_tools"]
