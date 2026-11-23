"""Tools reading the workspace database."""

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import READ_ONLY, WorkspaceArg


class TableSummary(BaseModel):
    name: str = Field(description="Name of the table.")
    rows: int | None = Field(description="Number of rows, if known.")


class TableList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    tables: list[TableSummary] = Field(description="Tables of the workspace database.")


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_list_database_tables",
        title="List tables of an OpenHEXA workspace database",
        annotations=READ_ONLY,
    )
    async def list_database_tables(workspace: WorkspaceArg) -> TableList:
        """List the tables of an OpenHEXA workspace's PostgreSQL database, with row counts."""
        items = await access.run(workspace, lambda client: client.database.tables())
        return TableList(
            workspace=workspace,
            tables=[TableSummary(name=t.name, rows=t.rows) for t in items],
        )
