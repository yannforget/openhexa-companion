"""Tools reading and creating the saved SQL queries of a workspace's Data Studio."""

from datetime import datetime
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from openhexa.companion import SavedQuery, SavedQuerySummary
from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import CREATE, READ_ONLY, WorkspaceArg


class SavedQueryItem(BaseModel):
    query: str = Field(description="Slug identifying the saved query in other tools.")
    name: str = Field(description="Display name.")
    description: str = Field(description="What the query is for. Empty if undocumented.")
    shared: bool = Field(description="Whether every workspace member can see the query.")
    author: str | None = Field(description="Who created the query.")
    updated_at: datetime = Field(description="When the query was last changed.")


class SavedQueryList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    queries: list[SavedQueryItem] = Field(description="Saved queries, most recently updated first.")


class SavedQueryDetails(SavedQueryItem):
    workspace: str = Field(description="Slug of the workspace.")
    sql: str = Field(description="The SQL of the query.")


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_list_saved_queries",
        title="List saved queries of an OpenHEXA workspace",
        annotations=READ_ONLY,
    )
    async def list_saved_queries(
        workspace: WorkspaceArg,
        search: Annotated[
            str | None,
            Field(description="Only list queries whose name or description contains this text."),
        ] = None,
    ) -> SavedQueryList:
        """List the SQL queries saved in an OpenHEXA workspace's Data Studio, without their SQL.

        Lists the queries shared with the workspace and the user's own private ones,
        most recently updated first. shared is false for a query only its author sees.
        """
        items = await access.run(workspace, lambda client: client.saved_queries.list(search))
        return SavedQueryList(workspace=workspace, queries=[_item(q) for q in items])

    @server.tool(
        name="openhexa_get_saved_query",
        title="Get a saved query of an OpenHEXA workspace",
        annotations=READ_ONLY,
    )
    async def get_saved_query(
        workspace: WorkspaceArg,
        query: Annotated[
            str,
            Field(
                description="Slug of the query, as given by openhexa_list_saved_queries or "
                "openhexa_search."
            ),
        ],
    ) -> SavedQueryDetails:
        """Read a saved query of an OpenHEXA workspace, with its SQL.

        openhexa_query_database runs the SQL.
        """
        item = await access.run(workspace, lambda client: client.saved_queries.get(query))
        return _details(workspace, item)


def register_writes(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_create_saved_query",
        title="Save a SQL query in an OpenHEXA workspace",
        annotations=CREATE,
    )
    async def create_saved_query(
        workspace: WorkspaceArg,
        name: Annotated[str, Field(min_length=1, description="Display name.")],
        sql: Annotated[
            str, Field(min_length=1, description="A single read-only PostgreSQL statement.")
        ],
        description: Annotated[
            str | None, Field(description="What the query is for and how to read its result.")
        ] = None,
        shared: Annotated[
            bool,
            Field(
                description="Whether every workspace member can see the query. By default "
                "only its author can."
            ),
        ] = False,
    ) -> SavedQueryDetails:
        """Save a SQL query in an OpenHEXA workspace's Data Studio.

        OpenHEXA saves the SQL without checking or running it. Each call saves a new
        query, even with the same name.
        """
        item = await access.run(
            workspace,
            lambda client: client.saved_queries.create(
                name, sql, description=description, shared=shared
            ),
        )
        return _details(workspace, item)


def _item(query: SavedQuerySummary) -> SavedQueryItem:
    return SavedQueryItem(
        query=query.slug,
        name=query.name,
        description=query.description,
        shared=query.shared,
        author=query.author,
        updated_at=query.updated_at,
    )


def _details(workspace: str, query: SavedQuery) -> SavedQueryDetails:
    return SavedQueryDetails(**_item(query).model_dump(), workspace=workspace, sql=query.sql)
