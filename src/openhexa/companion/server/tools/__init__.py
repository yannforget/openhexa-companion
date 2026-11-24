"""MCP tools, each a thin wrapper around a library operation."""

from mcp.server.mcpserver import MCPServer

from openhexa.companion.server.gateway import Gateway
from openhexa.companion.server.tools import (
    database,
    datasets,
    pipelines,
    saved_queries,
    search,
    webapps,
    workspaces,
)
from openhexa.companion.server.tools._access import Access


def register_tools(server: MCPServer, access: Access, gateway: Gateway) -> None:
    """Add every tool to `server`, in the order clients list them."""
    workspaces.register(server, access)
    search.register(server, access)
    datasets.register(server, access, gateway)
    pipelines.register(server, access)
    database.register(server, access)
    saved_queries.register(server, access)
    webapps.register(server, access)
    pipelines.register_writes(server, access)
    workspaces.register_writes(server, access)
    datasets.register_writes(server, access)
    saved_queries.register_writes(server, access)


__all__ = ["Access", "register_tools"]
