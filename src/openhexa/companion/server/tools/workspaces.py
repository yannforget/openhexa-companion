"""Tools listing and reading the workspaces of a grant."""

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from openhexa.companion import Workspace
from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import DESTRUCTIVE, READ_ONLY, WorkspaceArg


class WorkspaceSummary(BaseModel):
    slug: str = Field(description="Identifies the workspace in other tools.")
    name: str = Field(description="Display name of the workspace.")
    token_rejected: bool = Field(
        description="OpenHEXA rejected the stored token on its last use, so tools for this "
        "workspace may fail until the user replaces it."
    )


class WorkspaceList(BaseModel):
    workspaces: list[WorkspaceSummary] = Field(description="Workspaces this connector can access.")


class WorkspaceDetails(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    name: str = Field(description="Display name of the workspace.")
    homepage: str = Field(
        description="The workspace homepage in Markdown, where members document the "
        "workspace and its data. Empty if the workspace has none."
    )
    docker_image: str | None = Field(
        description="Docker image running pipelines and notebooks, or null for the default image."
    )


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_list_workspaces",
        title="List OpenHEXA workspaces",
        annotations=READ_ONLY,
    )
    async def list_workspaces() -> WorkspaceList:
        """List the OpenHEXA workspaces this connector can access, by slug and name."""
        return WorkspaceList(
            workspaces=[
                WorkspaceSummary(slug=ws.slug, name=ws.name, token_rejected=ws.token_rejected)
                for ws in await access.workspaces()
            ]
        )

    @server.tool(
        name="openhexa_get_workspace",
        title="Get an OpenHEXA workspace",
        annotations=READ_ONLY,
    )
    async def get_workspace(workspace: WorkspaceArg) -> WorkspaceDetails:
        """Read an OpenHEXA workspace: its name, Docker image and homepage.

        The homepage is Markdown written by workspace members, often describing the
        workspace's purpose, data sources and conventions.
        """
        return _details(await access.run(workspace, lambda client: client.get_workspace()))


def register_writes(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_update_workspace_homepage",
        title="Replace the homepage of an OpenHEXA workspace",
        annotations=DESTRUCTIVE,
    )
    async def update_workspace_homepage(
        workspace: WorkspaceArg,
        homepage: Annotated[
            str,
            Field(min_length=1, description="The complete new homepage, in Markdown."),
        ],
    ) -> WorkspaceDetails:
        """Replace the whole homepage of an OpenHEXA workspace with new Markdown.

        The previous content is overwritten, not merged, and OpenHEXA keeps no history of
        it. Only workspace editors and admins may change the homepage.
        """
        return _details(
            await access.run(workspace, lambda client: client.update_homepage(homepage))
        )


def _details(info: Workspace) -> WorkspaceDetails:
    return WorkspaceDetails(
        workspace=info.slug,
        name=info.name,
        homepage=info.description,
        docker_image=info.docker_image,
    )
