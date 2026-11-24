"""Tools reading datasets, and issuing URLs to download their files."""

from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, JsonValue

from openhexa.companion import Client, Dataset, DatasetFile, DatasetVersion
from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import (
    DESTRUCTIVE,
    READ_ONLY,
    DatasetArg,
    VersionArg,
    WorkspaceArg,
    is_uuid,
    truncation_note,
)

# Versions listed when no limit is given.
DEFAULT_VERSION_LIMIT = 20
# Files listed when no limit is given.
DEFAULT_FILE_LIMIT = 200
# Note returned with each file URL.
FILE_URL_NOTE = (
    "The URL is a signed storage URL: it downloads the complete file without "
    "authentication, and expires after about 10 minutes. Save the file to disk from a "
    "code environment right away, then analyse the local copy rather than querying the "
    "URL again. Don't display the URL, and never embed it in an artifact, web app or "
    "saved code: an OpenHEXA web app must request `downloadUrl` from its own /graphql/ "
    "endpoint when it needs the file. If the download fails with HTTP 400 or 403, call "
    "this tool again for a new URL; if the code environment cannot reach the URL's "
    "host, the user must allow that host for it. A downloaded copy can be reused for "
    "later questions only while it matches the same version ID."
)


class DatasetSummary(BaseModel):
    dataset: str = Field(
        description="Identifies the dataset in other tools: its slug, or 'workspace/slug' "
        "for a dataset shared from another workspace."
    )
    name: str = Field(description="Display name.")
    owner_workspace: str = Field(description="Slug of the workspace that owns the dataset.")
    latest_version: str | None = Field(
        description="Name of the latest version, or null if it has none yet."
    )


class DatasetList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    datasets: list[DatasetSummary] = Field(
        description="Datasets of the workspace, including those shared with it."
    )


class DatasetDetails(DatasetSummary):
    workspace: str = Field(description="Slug of the workspace the dataset was read through.")
    description: str = Field(
        description="Documentation of the dataset in Markdown, such as its sources, "
        "columns and caveats. Empty if there is none."
    )
    attributes: dict[str, Any] = Field(description="Custom metadata by key.")


class VersionSummary(BaseModel):
    name: str = Field(description="Name of the version, unique within the dataset.")
    version_id: str = Field(description="OpenHEXA ID of the version.")
    changelog: str | None = Field(description="What changed in this version, if documented.")
    created_at: datetime = Field(description="When the version was created.")


class VersionList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    dataset: str = Field(description="The dataset.")
    versions: list[VersionSummary] = Field(description="Versions, newest first.")
    total: int = Field(description="Number of versions of the dataset.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


class FileSummary(BaseModel):
    path: str = Field(description="Path of the file within the version.")
    size: int = Field(description="Size in bytes.")
    content_type: str = Field(description="Media type declared when the file was uploaded.")


class FileList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    dataset: str = Field(description="The dataset.")
    version: str = Field(description="Name of the version.")
    version_id: str = Field(description="OpenHEXA ID of the version.")
    files: list[FileSummary] = Field(description="Files of the version, by path.")
    total: int = Field(description="Number of files in the version.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


class FileUrl(BaseModel):
    url: str = Field(description="Short-lived URL downloading the complete file.")
    workspace: str = Field(description="Slug of the workspace.")
    dataset: str = Field(description="The dataset.")
    version: str = Field(description="Name of the version.")
    version_id: str = Field(description="OpenHEXA ID of the version, which pins the content.")
    path: str = Field(description="Path of the file within the version.")
    size: int = Field(description="Size in bytes.")
    content_type: str = Field(description="Media type declared when the file was uploaded.")
    note: str = Field(description="How to use the URL.")


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_list_datasets",
        title="List OpenHEXA datasets",
        annotations=READ_ONLY,
    )
    async def list_datasets(
        workspace: WorkspaceArg,
        query: Annotated[
            str | None,
            Field(description="Only list datasets whose slug or name contains this text."),
        ] = None,
    ) -> DatasetList:
        """List the datasets of an OpenHEXA workspace, including those shared with it."""
        items = await access.run(workspace, lambda client: client.datasets.list())
        if query:
            needle = query.casefold()
            items = [d for d in items if needle in d.slug.casefold() or needle in d.name.casefold()]
        return DatasetList(workspace=workspace, datasets=[_summary(workspace, d) for d in items])

    @server.tool(
        name="openhexa_get_dataset",
        title="Get an OpenHEXA dataset",
        annotations=READ_ONLY,
    )
    async def get_dataset(workspace: WorkspaceArg, dataset: DatasetArg) -> DatasetDetails:
        """Read an OpenHEXA dataset: its description, attributes and latest version."""
        item = await access.run(workspace, lambda client: client.datasets.get(dataset))
        return _details(workspace, item)

    @server.tool(
        name="openhexa_list_dataset_versions",
        title="List versions of an OpenHEXA dataset",
        annotations=READ_ONLY,
    )
    async def list_dataset_versions(
        workspace: WorkspaceArg,
        dataset: DatasetArg,
        limit: Annotated[
            int, Field(ge=1, description="Maximum number of versions to return, newest first.")
        ] = DEFAULT_VERSION_LIMIT,
    ) -> VersionList:
        """List the versions of an OpenHEXA dataset, newest first, with their changelogs."""
        versions = await access.run(workspace, lambda client: client.datasets.versions(dataset))
        return VersionList(
            workspace=workspace,
            dataset=dataset,
            versions=[_version(v) for v in versions[:limit]],
            total=len(versions),
            note=truncation_note(len(versions), limit, "versions"),
        )

    @server.tool(
        name="openhexa_list_dataset_files",
        title="List files of an OpenHEXA dataset version",
        annotations=READ_ONLY,
    )
    async def list_dataset_files(
        workspace: WorkspaceArg,
        dataset: DatasetArg,
        version: VersionArg = None,
        limit: Annotated[
            int, Field(ge=1, description="Maximum number of files to return.")
        ] = DEFAULT_FILE_LIMIT,
    ) -> FileList:
        """List the files of a version of an OpenHEXA dataset, with their size and type."""

        def files(client: Client) -> tuple[DatasetVersion, list[FileSummary]]:
            found = _find_version(client, dataset, version)
            return found, [
                FileSummary(path=f.path, size=f.size, content_type=f.content_type)
                for f in client.datasets.files(found)
            ]

        found, items = await access.run(workspace, files)
        return FileList(
            workspace=workspace,
            dataset=dataset,
            version=found.name,
            version_id=found.id,
            files=items[:limit],
            total=len(items),
            note=truncation_note(len(items), limit, "files"),
        )

    @server.tool(
        name="openhexa_get_dataset_file_url",
        title="Get a download URL for an OpenHEXA dataset file",
        annotations=READ_ONLY,
    )
    async def get_dataset_file_url(
        workspace: WorkspaceArg,
        dataset: DatasetArg,
        path: Annotated[
            str, Field(description="Path of the file, as listed by openhexa_list_dataset_files.")
        ],
        version: VersionArg = None,
    ) -> FileUrl:
        """Get a short-lived URL that downloads a complete file of an OpenHEXA dataset.

        The URL points to the storage of OpenHEXA, is pinned to one dataset version and
        works without authentication for about 10 minutes, so code can download and
        analyse the whole file.
        """

        def file_url(client: Client) -> tuple[DatasetFile, str]:
            file = client.datasets.file(_find_version(client, dataset, version), path)
            return file, client.datasets.download_url(file)

        file, url = await access.run(workspace, file_url)
        return FileUrl(
            url=url,
            workspace=workspace,
            dataset=dataset,
            version=file.version,
            version_id=file.version_id,
            path=file.path,
            size=file.size,
            content_type=file.content_type,
            note=FILE_URL_NOTE,
        )


def register_writes(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_update_dataset",
        title="Update an OpenHEXA dataset",
        annotations=DESTRUCTIVE,
    )
    async def update_dataset(
        workspace: WorkspaceArg,
        dataset: DatasetArg,
        name: Annotated[
            str | None,
            Field(min_length=1, description="New display name. Unchanged if omitted."),
        ] = None,
        description: Annotated[
            str | None,
            Field(
                description="New description in Markdown, replacing the whole current one. "
                "Unchanged if omitted."
            ),
        ] = None,
    ) -> DatasetDetails:
        """Rename an OpenHEXA dataset or replace its description.

        The slug, which identifies the dataset in tools, does not change with the name.
        """
        if name is None and description is None:
            raise ToolError("Give a new name, a new description or both")
        item = await access.run(
            workspace,
            lambda client: client.datasets.update(dataset, name=name, description=description),
        )
        return _details(workspace, item)

    @server.tool(
        name="openhexa_set_dataset_attribute",
        title="Set an attribute of an OpenHEXA dataset",
        annotations=DESTRUCTIVE,
    )
    async def set_dataset_attribute(
        workspace: WorkspaceArg,
        dataset: DatasetArg,
        key: Annotated[
            str, Field(min_length=1, description="Name of the attribute, such as 'source'.")
        ],
        value: Annotated[
            JsonValue, Field(description="Any JSON value, such as text, a number or a list.")
        ],
    ) -> DatasetDetails:
        """Set a custom metadata attribute of an OpenHEXA dataset, replacing any previous value."""
        item = await access.run(
            workspace, lambda client: client.datasets.set_attribute(dataset, key, value)
        )
        return _details(workspace, item)

    @server.tool(
        name="openhexa_delete_dataset_attribute",
        title="Delete an attribute of an OpenHEXA dataset",
        annotations=DESTRUCTIVE,
    )
    async def delete_dataset_attribute(
        workspace: WorkspaceArg,
        dataset: DatasetArg,
        key: Annotated[str, Field(description="Name of the attribute to remove.")],
    ) -> DatasetDetails:
        """Remove a custom metadata attribute from an OpenHEXA dataset."""
        item = await access.run(
            workspace, lambda client: client.datasets.delete_attribute(dataset, key)
        )
        return _details(workspace, item)


def _find_version(client: Client, dataset: str, version: str | None) -> DatasetVersion:
    """Find a version by ID if `version` is one, by name otherwise, or the latest one."""
    if version is None:
        return client.datasets.version(dataset)
    if is_uuid(version):
        return client.datasets.version(dataset, version_id=version)
    return client.datasets.version(dataset, version)


def _summary(workspace: str, dataset: Dataset) -> DatasetSummary:
    return DatasetSummary(
        dataset=dataset.slug if dataset.workspace == workspace else dataset.reference,
        name=dataset.name,
        owner_workspace=dataset.workspace,
        latest_version=dataset.latest_version,
    )


def _details(workspace: str, dataset: Dataset) -> DatasetDetails:
    return DatasetDetails(
        **_summary(workspace, dataset).model_dump(),
        workspace=workspace,
        description=dataset.description,
        attributes=dataset.attributes,
    )


def _version(version: DatasetVersion) -> VersionSummary:
    return VersionSummary(
        name=version.name,
        version_id=version.id,
        changelog=version.changelog,
        created_at=version.created_at,
    )
