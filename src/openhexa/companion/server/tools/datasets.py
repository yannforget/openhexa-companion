"""Tools reading datasets, and issuing URLs to download their files."""

from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, JsonValue

from openhexa.companion import Client, Dataset, DatasetFile, DatasetVersion
from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import (
    DEFAULT_LIST_LIMIT,
    DESTRUCTIVE,
    READ_ONLY,
    DatasetArg,
    ListLimitArg,
    VersionArg,
    WorkspaceArg,
    is_uuid,
    truncation_note,
)

# Versions listed with a dataset when no limit is given.
DEFAULT_VERSION_LIMIT = 10
# Files listed when no limit is given.
DEFAULT_FILE_LIMIT = 200


class DatasetSummary(BaseModel):
    dataset: str = Field(description="Identifies the dataset in other tools.")
    name: str = Field(description="Display name.")
    owner_workspace: str = Field(description="Slug of the workspace that owns the dataset.")
    latest_version: str | None = Field(description="Name of the latest version.")


class DatasetList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    datasets: list[DatasetSummary] = Field(
        description="Datasets of the workspace, including those shared with it."
    )
    total_datasets: int = Field(description="Number of datasets of the workspace.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


class DatasetDetails(DatasetSummary):
    workspace: str = Field(description="Slug of the workspace the dataset was read through.")
    description: str = Field(description="Documentation of the dataset in Markdown.")
    attributes: dict[str, Any] = Field(description="Custom metadata by key.")


class VersionSummary(BaseModel):
    version: str = Field(description="Name of the version, unique within the dataset.")
    version_id: str = Field(description="OpenHEXA ID of the version.")
    changelog: str | None = Field(description="What changed in this version.")
    created_at: datetime = Field(description="When the version was created.")


class DatasetWithVersions(DatasetDetails):
    versions: list[VersionSummary] = Field(description="Versions, newest first.")
    total_versions: int = Field(description="Number of versions of the dataset.")
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
    total_files: int = Field(description="Number of files in the version.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


class FileUrl(BaseModel):
    url: str = Field(description="Signed storage URL downloading the complete file.")
    host: str = Field(description="Host of the URL.")
    workspace: str = Field(description="Slug of the workspace.")
    dataset: str = Field(description="The dataset.")
    version: str = Field(description="Name of the version.")
    version_id: str = Field(description="OpenHEXA ID of the version.")
    path: str = Field(description="Path of the file within the version.")
    size: int = Field(description="Size in bytes.")
    content_type: str = Field(description="Media type declared when the file was uploaded.")


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_list_datasets",
        title="List OpenHEXA datasets",
        annotations=READ_ONLY,
    )
    async def list_datasets(
        workspace: WorkspaceArg, limit: ListLimitArg = DEFAULT_LIST_LIMIT
    ) -> DatasetList:
        """List the datasets of an OpenHEXA workspace, including those shared with it.

        A dataset is a set of files published in versions. Each dataset's identifier is
        its slug, or 'workspace/slug' for a dataset shared from another workspace.
        latest_version is the name of the latest version, or null if it has none yet.
        """
        items = await access.run(workspace, lambda client: client.datasets.list())
        return DatasetList(
            workspace=workspace,
            datasets=[_summary(workspace, d) for d in items[:limit]],
            total_datasets=len(items),
            note=truncation_note(len(items), limit, "datasets"),
        )

    @server.tool(
        name="openhexa_get_dataset",
        title="Get an OpenHEXA dataset",
        annotations=READ_ONLY,
    )
    async def get_dataset(
        workspace: WorkspaceArg,
        dataset: DatasetArg,
        version_limit: Annotated[
            int, Field(ge=1, description="Maximum number of versions to list, newest first.")
        ] = DEFAULT_VERSION_LIMIT,
    ) -> DatasetWithVersions:
        """Read an OpenHEXA dataset: its description, attributes and versions.

        The description is Markdown documenting the data, such as its sources, columns
        and caveats; it is empty if there is none. Versions are listed newest first; a
        version's files do not change once a newer version exists.
        """

        def read(client: Client) -> tuple[Dataset, list[DatasetVersion]]:
            return client.datasets.get(dataset), client.datasets.versions(dataset)

        item, versions = await access.run(workspace, read)
        return DatasetWithVersions(
            **_details(workspace, item).model_dump(),
            versions=[_version(v) for v in versions[:version_limit]],
            total_versions=len(versions),
            note=truncation_note(len(versions), version_limit, "versions", "version_limit"),
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
        """List the files of a version of an OpenHEXA dataset, with their size in bytes and type.

        The response names the version listed, the latest by default, and its ID.
        """

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
            total_files=len(items),
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
        """Get a temporary URL that downloads one file of an OpenHEXA dataset version.

        The response holds the URL, not the file's content. The URL points to the storage
        of OpenHEXA and serves the complete file of this version only. Anyone who has it
        can download the file without authentication until it expires, 10 minutes to 1
        hour after it was issued depending on the storage; storage then answers HTTP 400
        or 403, and this tool issues a new URL. The response also gives the URL's host,
        the version's name and ID, and the file's size in bytes and type.
        """

        def file_url(client: Client) -> tuple[DatasetFile, str]:
            file = client.datasets.file(_find_version(client, dataset, version), path)
            return file, client.datasets.download_url(file)

        file, url = await access.run(workspace, file_url)
        return FileUrl(
            url=url,
            host=urlsplit(url).hostname or "",
            workspace=workspace,
            dataset=dataset,
            version=file.version,
            version_id=file.version_id,
            path=file.path,
            size=file.size,
            content_type=file.content_type,
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
        version=version.name,
        version_id=version.id,
        changelog=version.changelog,
        created_at=version.created_at,
    )
