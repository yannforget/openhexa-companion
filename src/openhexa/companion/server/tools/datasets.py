"""Tools reading datasets, and issuing URLs to read their files."""

from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, JsonValue

from openhexa.companion import Client, Dataset, DatasetVersion
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
    join_notes,
    truncation_note,
)

# Versions listed with a dataset when no limit is given.
DEFAULT_VERSION_LIMIT = 10
# Files listed when no limit is given.
DEFAULT_FILE_LIMIT = 200
# URLs returned when no limit is given. Each is about 900 characters long.
DEFAULT_URL_LIMIT = 50
# Path of the README returned with a dataset, and the most bytes of it returned.
README_PATH = "README.md"
README_MAX_BYTES = 20_000


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
    readme: str | None = Field(
        description="Text of README.md in the version, or null if it has none. Usually "
        "documents the files, columns and caveats."
    )
    note: str | None = Field(
        description="Explains how to get more results or the rest of the readme, if truncated."
    )


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


class FileUrls(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    dataset: str = Field(description="The dataset.")
    version: str = Field(description="Name of the version.")
    version_id: str = Field(description="OpenHEXA ID of the version.")
    urls: dict[str, str] = Field(description="Signed storage URL of each file, by path.")
    host: str = Field(description="Host of the URLs, to allow in a sandbox.")
    total_files: int = Field(description="Number of files in the version, or of paths asked for.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


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
        version: VersionArg = None,
        version_limit: Annotated[
            int, Field(ge=1, description="Maximum number of versions to list, newest first.")
        ] = DEFAULT_VERSION_LIMIT,
    ) -> DatasetWithVersions:
        """Read an OpenHEXA dataset: its description, attributes, versions and README.

        The description is Markdown documenting the data, such as its sources, columns
        and caveats; it is empty if there is none. Versions are listed newest first; a
        version's files do not change once a newer version exists. The readme is the
        text of the README.md file at the root of the given version, cut after 20 KB.
        """

        def read(client: Client) -> tuple[Dataset, list[DatasetVersion], str | None, str | None]:
            item = client.datasets.get(dataset)
            versions = client.datasets.versions(dataset)
            if version is not None:
                selected = _find_version(client, dataset, version)
            elif versions:
                selected = versions[0]
            else:
                return item, versions, None, None
            return item, versions, *_readme(client, selected)

        item, versions, readme, readme_note = await access.run(workspace, read)
        return DatasetWithVersions(
            **_details(workspace, item).model_dump(),
            versions=[_version(v) for v in versions[:version_limit]],
            total_versions=len(versions),
            readme=readme,
            note=join_notes(
                truncation_note(len(versions), version_limit, "versions", "version_limit"),
                readme_note,
            ),
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
        name="openhexa_get_dataset_file_urls",
        title="Get URLs for the files of an OpenHEXA dataset version",
        annotations=READ_ONLY,
    )
    async def get_dataset_file_urls(
        workspace: WorkspaceArg,
        dataset: DatasetArg,
        version: VersionArg = None,
        paths: Annotated[
            list[str] | None,
            Field(
                min_length=1,
                description="Paths of the files, as listed by openhexa_list_dataset_files. "
                "All files if omitted.",
            ),
        ] = None,
        limit: Annotated[
            int, Field(ge=1, description="Maximum number of URLs to return.")
        ] = DEFAULT_URL_LIMIT,
    ) -> FileUrls:
        """Get temporary URLs serving the files of a version of an OpenHEXA dataset.

        The version is the latest by default. The response maps each file path to a
        signed URL, not to the content. The URLs point to the storage of OpenHEXA, need no
        authentication and support HTTP range requests, so Parquet files can be read in
        place. They expire 10 minutes to 1 hour after they are issued, depending on the
        storage; storage then answers HTTP 400 or 403, and this tool issues new ones.
        """

        def file_urls(client: Client) -> tuple[DatasetVersion, dict[str, str], int]:
            found = _find_version(client, dataset, version)
            if paths is not None:
                urls = client.datasets.file_urls(found, paths, limit=limit)
                return found, urls, len(set(paths))
            urls = client.datasets.file_urls(found, limit=limit + 1)
            total = len(urls) if len(urls) <= limit else client.datasets.file_count(found)
            return found, dict(list(urls.items())[:limit]), total

        found, urls, total = await access.run(workspace, file_urls)
        return FileUrls(
            workspace=workspace,
            dataset=dataset,
            version=found.name,
            version_id=found.id,
            urls=urls,
            host=next((urlsplit(url).hostname or "" for url in urls.values()), ""),
            total_files=total,
            note=truncation_note(total, limit, "files"),
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


def _readme(client: Client, version: DatasetVersion) -> tuple[str | None, str | None]:
    """Read the start of the README.md of `version`, if it has one, and a note if it is cut."""
    file = client.datasets.find_file(version, README_PATH)
    if file is None or file.size == 0:
        return None, None
    with client.datasets.read(file, length=min(file.size, README_MAX_BYTES)) as stream:
        text = stream.read().decode("utf-8", errors="replace")
    if file.size <= README_MAX_BYTES:
        return text, None
    return text, (
        f"The readme is cut after {README_MAX_BYTES} bytes. Read the whole of it through "
        f"openhexa_get_dataset_file_urls with paths=['{README_PATH}'] and the same version."
    )


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
