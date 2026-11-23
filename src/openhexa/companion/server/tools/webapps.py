"""Tools reading the webapps of a workspace."""

from datetime import datetime
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from openhexa.companion import Client, Webapp, WebappFile, WebappVersion
from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import (
    READ_ONLY,
    WorkspaceArg,
    join_notes,
    truncation_note,
)

# Versions listed when no limit is given.
DEFAULT_VERSION_LIMIT = 10
# Files listed when no limit is given.
DEFAULT_FILE_LIMIT = 200
# Type of the webapps published from files, the only ones with files and versions.
STATIC_TYPE = "STATIC"


class WebappSummary(BaseModel):
    slug: str = Field(description="Identifies the webapp in other tools.")
    name: str = Field(description="Display name.")
    type: str = Field(
        description="'STATIC' for sites published from files, 'IFRAME' for an embedded "
        "URL, or 'SUPERSET' for a dashboard."
    )
    url: str = Field(description="Where the webapp is served.")
    public: bool = Field(description="Whether anyone can open it without signing in.")


class WebappList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    webapps: list[WebappSummary] = Field(description="Webapps of the workspace.")


class WebappFileInfo(BaseModel):
    path: str = Field(description="Path from the root of the site, such as 'assets/app.js'.")
    size: int = Field(description="Size in bytes.")


class WebappVersionInfo(BaseModel):
    version_id: str = Field(description="Identifies the version.")
    message: str = Field(description="Description of the change.")
    created_at: datetime = Field(description="When the version was published.")
    author: str = Field(description="Who published it.")


class WebappDetails(WebappSummary):
    workspace: str = Field(description="Slug of the workspace.")
    description: str = Field(description="What the webapp shows. Empty if undocumented.")
    scopes: list[str] = Field(
        description="OpenHEXA operations the webapp may perform for the person viewing "
        "it, such as 'DATASETS_READ'."
    )
    published_version: str | None = Field(
        description="ID of the published version, for static webapps."
    )
    files: list[WebappFileInfo] = Field(
        description="Files of the published version, for static webapps."
    )
    total_files: int = Field(description="Number of files of the published version.")
    versions: list[WebappVersionInfo] = Field(
        description="Versions of a static webapp, newest first."
    )
    note: str | None = Field(description="Explains how to get more results, if truncated.")


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_list_webapps",
        title="List OpenHEXA webapps",
        annotations=READ_ONLY,
    )
    async def list_webapps(workspace: WorkspaceArg) -> WebappList:
        """List the webapps of an OpenHEXA workspace, with their type and URL."""
        items = await access.run(workspace, lambda client: client.webapps.list())
        return WebappList(workspace=workspace, webapps=[_summary(w) for w in items])

    @server.tool(
        name="openhexa_get_webapp",
        title="Get an OpenHEXA webapp",
        annotations=READ_ONLY,
    )
    async def get_webapp(
        workspace: WorkspaceArg,
        webapp: Annotated[
            str, Field(description="Slug of the webapp, as listed by openhexa_list_webapps.")
        ],
        file_limit: Annotated[
            int, Field(ge=1, description="Maximum number of files to return.")
        ] = DEFAULT_FILE_LIMIT,
        version_limit: Annotated[
            int, Field(ge=1, description="Maximum number of versions to return, newest first.")
        ] = DEFAULT_VERSION_LIMIT,
    ) -> WebappDetails:
        """Read an OpenHEXA webapp: its description and, for static webapps, files and versions."""

        def read(client: Client) -> tuple[Webapp, list[WebappFile], list[WebappVersion]]:
            found = client.webapps.get(webapp)
            if found.type != STATIC_TYPE:
                return found, [], []
            files = client.webapps.files(webapp)
            # One more than needed, to tell whether there are older versions.
            versions = client.webapps.versions(webapp, limit=version_limit + 1)
            return found, files, versions

        found, files, versions = await access.run(workspace, read)
        return WebappDetails(
            **_summary(found).model_dump(),
            workspace=workspace,
            description=found.description,
            scopes=found.scopes,
            published_version=found.version,
            files=[WebappFileInfo(path=f.path, size=f.size) for f in files[:file_limit]],
            total_files=len(files),
            versions=[
                WebappVersionInfo(
                    version_id=v.id, message=v.message, created_at=v.created_at, author=v.author
                )
                for v in versions[:version_limit]
            ],
            note=join_notes(
                truncation_note(len(files), file_limit, "files", "file_limit"),
                f"Showing the latest {version_limit} versions; there are older ones. Call "
                "again with a higher version_limit to see them."
                if len(versions) > version_limit
                else None,
            ),
        )


def _summary(webapp: Webapp) -> WebappSummary:
    return WebappSummary(
        slug=webapp.slug,
        name=webapp.name,
        type=webapp.type,
        url=webapp.url,
        public=webapp.public,
    )
