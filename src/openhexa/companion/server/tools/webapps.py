"""Tools reading the webapps of a workspace."""

from datetime import datetime
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from openhexa.companion import Client, Webapp, WebappFile, WebappVersion
from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import (
    DEFAULT_LIST_LIMIT,
    READ_ONLY,
    ListLimitArg,
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
    webapp: str = Field(description="Slug identifying the webapp in other tools.")
    name: str = Field(description="Display name.")
    type: str = Field(description="'STATIC', 'IFRAME' or 'SUPERSET'.")
    url: str = Field(description="Where the webapp is served.")
    public: bool = Field(description="Whether anyone can open it without signing in.")


class WebappList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    webapps: list[WebappSummary] = Field(description="Webapps of the workspace.")
    total_webapps: int = Field(description="Number of webapps of the workspace.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


class WebappFileInfo(BaseModel):
    path: str = Field(description="Path from the root of the site, such as 'assets/app.js'.")
    size: int = Field(description="Size in bytes.")


class WebappVersionInfo(BaseModel):
    version_id: str = Field(description="OpenHEXA ID of the version.")
    message: str = Field(description="Description of the change.")
    created_at: datetime = Field(description="When the version was published.")
    author: str = Field(description="Who published it.")


class WebappDetails(WebappSummary):
    workspace: str = Field(description="Slug of the workspace.")
    description: str = Field(description="What the webapp shows. Empty if undocumented.")
    scopes: list[str] = Field(description="OpenHEXA operations allowed to the webapp.")
    published_version_id: str | None = Field(description="ID of the version served.")
    files: list[WebappFileInfo] = Field(description="Files of the version served.")
    total_files: int = Field(description="Number of files of the version served.")
    versions: list[WebappVersionInfo] = Field(description="Versions, newest first.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_list_webapps",
        title="List OpenHEXA webapps",
        annotations=READ_ONLY,
    )
    async def list_webapps(
        workspace: WorkspaceArg, limit: ListLimitArg = DEFAULT_LIST_LIMIT
    ) -> WebappList:
        """List the webapps of an OpenHEXA workspace, with their type and URL.

        Types are 'STATIC' for sites published from files, 'IFRAME' for an embedded URL
        and 'SUPERSET' for a dashboard. public is true when anyone can open the webapp
        without signing in.
        """
        items = await access.run(workspace, lambda client: client.webapps.list())
        return WebappList(
            workspace=workspace,
            webapps=[_summary(w) for w in items[:limit]],
            total_webapps=len(items),
            note=truncation_note(len(items), limit, "webapps"),
        )

    @server.tool(
        name="openhexa_get_webapp",
        title="Get an OpenHEXA webapp",
        annotations=READ_ONLY,
    )
    async def get_webapp(
        workspace: WorkspaceArg,
        webapp: Annotated[
            str,
            Field(
                description="Slug of the webapp, as given by openhexa_list_webapps or "
                "openhexa_search."
            ),
        ],
        file_limit: Annotated[
            int, Field(ge=1, description="Maximum number of files to return.")
        ] = DEFAULT_FILE_LIMIT,
        version_limit: Annotated[
            int, Field(ge=1, description="Maximum number of versions to return, newest first.")
        ] = DEFAULT_VERSION_LIMIT,
    ) -> WebappDetails:
        """Read an OpenHEXA webapp: its description and, for static webapps, files and versions.

        scopes are the OpenHEXA operations the webapp may perform for the person viewing
        it, such as 'DATASETS_READ'. A static webapp serves the version
        published_version_id, which may be earlier than the latest one; files are that
        version's, with sizes in bytes, and versions are listed newest first. Other
        webapps have no files or versions.
        """

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
            published_version_id=found.version,
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
        webapp=webapp.slug,
        name=webapp.name,
        type=webapp.type,
        url=webapp.url,
        public=webapp.public,
    )
