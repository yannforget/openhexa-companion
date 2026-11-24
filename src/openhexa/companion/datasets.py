"""Datasets of a workspace, their versions and files."""

import builtins
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from openhexa.companion._transport import ByteStream, Transport
from openhexa.companion._util import check_uuid, format_options, guess_content_type
from openhexa.companion.errors import InvalidInputError, NotFoundError, PermissionDeniedError
from openhexa.companion.workspace import workspace_not_found

DATASET_FIELDS = """
  id
  slug
  name
  description
  workspace { slug }
  latestVersion { name }
  attributes { key value system }
"""

VERSION_FIELDS = """
  id
  name
  changelog
  createdAt
"""

FILE_FIELDS = """
  id
  uri
  size
  contentType
"""

LIST_QUERY = f"""
query Datasets($workspace: String!, $page: Int!, $perPage: Int!) {{
  workspace(slug: $workspace) {{
    datasets(page: $page, perPage: $perPage) {{
      totalPages
      items {{ dataset {{ {DATASET_FIELDS} }} }}
    }}
  }}
}}
"""


# Datasets of the workspace searched when checking that a dataset is shared with it. The
# search matches slugs, names and descriptions, so it is set to the server maximum.
SEARCH_SIZE = 10_000


def _dataset_query(name: str, selection: str, variables: str = "") -> str:
    """Build a query reading `selection` from one dataset of the workspace.

    `datasetLinkBySlug` finds a dataset by the slug of its owner workspace. For a dataset
    owned by another workspace, the query also lists the matching datasets of the client's
    workspace so the caller can check that the dataset is shared with it.
    """
    return f"""
query {name}(
  $workspace: String!, $owner: String!, $slug: String!, $shared: Boolean!{variables}
) {{
  workspace(slug: $workspace) @include(if: $shared) {{
    datasets(query: $slug, perPage: {SEARCH_SIZE}) {{ items {{ dataset {{ id }} }} }}
  }}
  datasetLinkBySlug(workspaceSlug: $owner, datasetSlug: $slug) {{
    dataset {{
      id
      slug
      workspace {{ slug }}
      targetId
      {selection}
    }}
  }}
}}
"""


GET_QUERY = _dataset_query("Dataset", DATASET_FIELDS)

VERSIONS_QUERY = _dataset_query(
    "DatasetVersions",
    f"""
      versions(page: $page, perPage: $perPage) {{
        totalPages
        items {{ {VERSION_FIELDS} }}
      }}
    """,
    ", $page: Int!, $perPage: Int!",
)

LATEST_VERSION_QUERY = _dataset_query(
    "LatestDatasetVersion", f"latestVersion {{ {VERSION_FIELDS} }}"
)

VERSION_QUERY = _dataset_query(
    "DatasetVersion", f"version(id: $version) {{ {VERSION_FIELDS} }}", ", $version: ID!"
)

FILES_QUERY = _dataset_query(
    "DatasetFiles",
    f"""
      version(id: $version) {{
        files(page: $page, perPage: $perPage) {{
          totalPages
          items {{ {FILE_FIELDS} }}
        }}
      }}
    """,
    ", $version: ID!, $page: Int!, $perPage: Int!",
)

FILE_QUERY = _dataset_query(
    "DatasetFile",
    f"version(id: $version) {{ fileByName(name: $path) {{ {FILE_FIELDS} }} }}",
    ", $version: ID!, $path: String!",
)

DOWNLOAD_URL_QUERY = _dataset_query(
    "DatasetFileDownloadUrl",
    "version(id: $version) { fileByName(name: $path) { downloadUrl(attachment: false) } }",
    ", $version: ID!, $path: String!",
)

CREATE_MUTATION = f"""
mutation CreateDataset($input: CreateDatasetInput!) {{
  createDataset(input: $input) {{
    success
    errors
    dataset {{ {DATASET_FIELDS} }}
  }}
}}
"""

CREATE_VERSION_MUTATION = f"""
mutation CreateDatasetVersion($input: CreateDatasetVersionInput!) {{
  createDatasetVersion(input: $input) {{
    success
    errors
    version {{ {VERSION_FIELDS} }}
  }}
}}
"""

UPLOAD_URL_MUTATION = """
mutation GenerateDatasetUploadUrl($input: GenerateDatasetUploadUrlInput!) {
  generateDatasetUploadUrl(input: $input) {
    success
    errors
    uploadUrl
    headers
  }
}
"""

CREATE_FILE_MUTATION = f"""
mutation CreateDatasetVersionFile($input: CreateDatasetVersionFileInput!) {{
  createDatasetVersionFile(input: $input) {{
    success
    errors
    file {{ {FILE_FIELDS} }}
  }}
}}
"""

UPDATE_MUTATION = f"""
mutation UpdateDataset($input: UpdateDatasetInput!) {{
  updateDataset(input: $input) {{
    success
    errors
    dataset {{ {DATASET_FIELDS} }}
  }}
}}
"""

UPDATE_VERSION_MUTATION = f"""
mutation UpdateDatasetVersion($input: UpdateDatasetVersionInput!) {{
  updateDatasetVersion(input: $input) {{
    success
    errors
    version {{ {VERSION_FIELDS} }}
  }}
}}
"""

DELETE_VERSION_MUTATION = """
mutation DeleteDatasetVersion($input: DeleteDatasetVersionInput!) {
  deleteDatasetVersion(input: $input) {
    success
    errors
  }
}
"""

SET_ATTRIBUTE_MUTATION = """
mutation SetDatasetAttribute($input: SetMetadataAttributeInput!) {
  setMetadataAttribute(input: $input) {
    success
    errors
  }
}
"""

DELETE_ATTRIBUTE_MUTATION = """
mutation DeleteDatasetAttribute($input: DeleteMetadataAttributeInput!) {
  deleteMetadataAttribute(input: $input) {
    success
    errors
  }
}
"""

DELETE_MUTATION = """
mutation DeleteDataset($input: DeleteDatasetInput!) {
  deleteDataset(input: $input) {
    success
    errors
  }
}
"""

# Explanations of the error codes returned when adding a file to a version.
FILE_ERRORS = {
    "ALREADY_EXISTS": "a file with this path already exists in the version",
    "INVALID_URI": "the path is not a valid file path",
    "LOCKED_VERSION": "only the latest version of a dataset accepts new files",
    "PERMISSION_DENIED": "the token may not add files to this dataset",
    "VERSION_NOT_FOUND": "the version does not exist",
}


@dataclass(frozen=True)
class Dataset:
    """A dataset available in the workspace.

    Attributes:
        slug: Identifies the dataset within the workspace that owns it.
        workspace: Slug of the workspace that owns the dataset, which differs from the
            client's workspace for datasets shared with it.
        name: Display name.
        description: Documentation of the dataset, in Markdown.
        latest_version: Name of the latest version, or `None` if there is none yet.
        attributes: Custom metadata by key. Values are any JSON value, such as text, a
            number or a list.
        id: OpenHEXA identifier.
    """

    slug: str
    workspace: str
    name: str
    description: str
    latest_version: str | None
    attributes: dict[str, Any]
    id: str

    @property
    def reference(self) -> str:
        """Identifies the dataset unambiguously, as `workspace/slug`."""
        return f"{self.workspace}/{self.slug}"


@dataclass(frozen=True)
class DatasetVersion:
    """An immutable set of files published under a name.

    Attributes:
        dataset: Slug of the dataset.
        workspace: Slug of the workspace that owns the dataset.
        name: Name of the version, unique within the dataset.
        changelog: What changed in this version, if documented.
        created_at: When the version was created.
        id: OpenHEXA identifier.
    """

    dataset: str
    workspace: str
    name: str
    changelog: str | None
    created_at: datetime
    id: str


@dataclass(frozen=True)
class DatasetFile:
    """A file in a dataset version.

    Attributes:
        dataset: Slug of the dataset.
        workspace: Slug of the workspace that owns the dataset.
        version: Name of the version.
        path: Path of the file within the version, such as `data/cases.csv`.
        size: Size in bytes.
        content_type: Media type declared when the file was uploaded.
        id: OpenHEXA identifier.
        version_id: OpenHEXA identifier of the version.
    """

    dataset: str
    workspace: str
    version: str
    path: str
    size: int
    content_type: str
    id: str
    version_id: str


class Datasets:
    """Reads and publishes the datasets available in a workspace.

    Methods taking a `dataset` accept its slug, or `workspace/slug` to name a dataset
    shared from another workspace. A plain slug finds the workspace's own dataset first,
    then one shared with it.
    """

    def __init__(self, transport: Transport, workspace: str) -> None:
        self._transport = transport
        self._workspace = workspace

    def list(self, *, limit: int | None = None) -> builtins.list[Dataset]:
        """List the datasets of the workspace, including those shared with it.

        Args:
            limit: Maximum number of datasets, or `None` for all of them.

        Raises:
            NotFoundError: The workspace does not exist or the token cannot access it.
        """

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            data = self._transport.query(
                LIST_QUERY, {"workspace": self._workspace, "page": page, "perPage": per_page}
            )["workspace"]
            if data is None:
                raise workspace_not_found(self._transport, self._workspace)
            return data["datasets"]

        items = self._transport.paginate(fetch_page, limit)
        return [_dataset(item["dataset"]) for item in items]

    def get(self, dataset: str) -> Dataset:
        """Get a dataset.

        Raises:
            NotFoundError: The workspace has no such dataset.
        """
        return _dataset(self._find(GET_QUERY, dataset))

    def versions(self, dataset: str, *, limit: int | None = None) -> builtins.list[DatasetVersion]:
        """List the versions of a dataset, newest first.

        Args:
            dataset: Slug of the dataset.
            limit: Maximum number of versions, or `None` for all of them.

        Raises:
            NotFoundError: The workspace has no such dataset.
        """
        found: dict[str, Any] = {}

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            data = self._find(VERSIONS_QUERY, dataset, page=page, perPage=per_page)
            found.setdefault("dataset", data)
            return data["versions"]

        versions = list(self._transport.paginate(fetch_page, limit))
        return [_version(item, found["dataset"]) for item in versions]

    def version(
        self, dataset: str, name: str | None = None, *, version_id: str | None = None
    ) -> DatasetVersion:
        """Get a version of a dataset: by name, by ID, or the latest one by default.

        Finding a version by name lists every version of the dataset; finding it by ID
        does not, so use `version_id` to find again a version found earlier.

        Raises:
            InvalidInputError: Both `name` and `version_id` are given, or `version_id` is not
                an OpenHEXA identifier.
            NotFoundError: The dataset or version does not exist.
        """
        if name is not None and version_id is not None:
            raise InvalidInputError("Give either a version name or a version ID, not both")
        if name is not None:
            versions = self.versions(dataset)
            for version in versions:
                if version.name == name:
                    return version
            raise NotFoundError(
                f"Dataset '{dataset}' in workspace '{self._workspace}' has no version "
                f"'{name}'. Versions, newest first: {format_options(v.name for v in versions)}"
            )
        if version_id is not None:
            check_uuid(version_id, "version ID")
            data = self._find(VERSION_QUERY, dataset, version=version_id)
            if data["version"] is None:
                raise NotFoundError(
                    f"Dataset '{dataset}' in workspace '{self._workspace}' has no version "
                    f"with ID '{version_id}'"
                )
            return _version(data["version"], data)
        data = self._find(LATEST_VERSION_QUERY, dataset)
        if data["latestVersion"] is None:
            raise NotFoundError(
                f"Dataset '{dataset}' in workspace '{self._workspace}' has no version yet"
            )
        return _version(data["latestVersion"], data)

    def files(
        self, version: DatasetVersion, *, limit: int | None = None
    ) -> builtins.list[DatasetFile]:
        """List the files of a dataset version.

        Args:
            version: The version.
            limit: Maximum number of files, or `None` for all of them.

        Raises:
            NotFoundError: The dataset or version no longer exists.
        """

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            data = self._find_version(FILES_QUERY, version, page=page, perPage=per_page)
            return data["files"]

        return [_file(item, version) for item in self._transport.paginate(fetch_page, limit)]

    def file(self, version: DatasetVersion, path: str) -> DatasetFile:
        """Get the file at `path` in a dataset version.

        Raises:
            NotFoundError: The version has no file at `path`.
        """
        data = self._find_version(FILE_QUERY, version, path=path)["fileByName"]
        if data is None:
            raise self._file_not_found(version, path)
        return _file(data, version)

    @contextmanager
    def read(
        self, file: DatasetFile, *, offset: int = 0, length: int | None = None
    ) -> Generator[ByteStream]:
        """Stream the bytes of a dataset file, or `length` bytes from `offset`.

        Each call asks OpenHEXA for a new storage URL, so a file can be read in several
        calls long after it was listed.

        Example:
            ```python
            with client.datasets.read(file, offset=0, length=1024) as stream:
                for chunk in stream:
                    ...
            ```

        Raises:
            InvalidInputError: `offset` is past the end of the file.
            NotFoundError: The file no longer exists.
            PermissionDeniedError: The token may not download this dataset.
        """
        if offset and offset >= file.size:
            raise InvalidInputError(
                f"Offset {offset} is past the end of {file.size}-byte '{file.path}'"
            )
        url = self.download_url(file)
        what = (
            f"'{file.path}' of dataset '{self._reference(file.workspace, file.dataset)}' "
            f"version '{file.version}'"
        )
        with self._transport.download(url, what=what, offset=offset, length=length) as stream:
            yield stream

    def download_url(self, file: DatasetFile) -> str:
        """Get a signed storage URL that downloads a dataset file without authentication.

        The URL grants access to the file until it expires, after 10 minutes to 1 hour
        depending on the storage. Treat it as a credential.

        Raises:
            NotFoundError: The file no longer exists.
            PermissionDeniedError: The token may not download this dataset.
        """
        dataset = self._reference(file.workspace, file.dataset)
        data = self._find(DOWNLOAD_URL_QUERY, dataset, version=file.version_id, path=file.path)
        if data["version"] is None or data["version"]["fileByName"] is None:
            raise NotFoundError(
                f"File '{file.path}' of dataset '{dataset}' version '{file.version}' no "
                f"longer exists in workspace '{self._workspace}'"
            )
        url = data["version"]["fileByName"]["downloadUrl"]
        if url is None:
            raise PermissionDeniedError(
                f"The token may not download files of dataset '{dataset}' in "
                f"workspace '{self._workspace}'"
            )
        return url

    def create(self, name: str, description: str = "") -> Dataset:
        """Create a dataset, with no version, in the workspace.

        OpenHEXA derives the slug from `name`, adding a suffix if it is already used.

        Raises:
            PermissionDeniedError: The token may not create datasets.
        """
        result = self._transport.mutate(
            "createDataset",
            CREATE_MUTATION,
            {"input": {"workspaceSlug": self._workspace, "name": name, "description": description}},
            action=f"create dataset '{name}' in workspace '{self._workspace}'",
            reasons={
                "PERMISSION_DENIED": "the token may not create datasets",
                "WORKSPACE_NOT_FOUND": "the workspace does not exist or the token cannot access it",
            },
        )
        return _dataset(result["dataset"])

    def create_version(
        self, dataset: str, name: str, changelog: str | None = None
    ) -> DatasetVersion:
        """Create an empty version of a dataset, which becomes its latest version.

        Only the latest version of a dataset accepts files: upload them with `upload`
        before creating another version.

        Raises:
            NotFoundError: The workspace has no such dataset.
            PermissionDeniedError: The token may not create versions of the dataset.
            APIError: The dataset already has a version `name` (code `DUPLICATE_NAME`).
        """
        found = self._find(GET_QUERY, dataset)
        result = self._transport.mutate(
            "createDatasetVersion",
            CREATE_VERSION_MUTATION,
            {"input": {"datasetId": found["id"], "name": name, "changelog": changelog}},
            action=f"create version '{name}' of dataset '{dataset}' in workspace "
            f"'{self._workspace}'",
            reasons={
                "DUPLICATE_NAME": "the dataset already has a version with this name",
                "PERMISSION_DENIED": "the token may not create versions of this dataset",
            },
        )
        return _version(result["version"], found)

    def upload(
        self,
        version: DatasetVersion,
        source: Path | bytes,
        path: str | None = None,
        content_type: str | None = None,
    ) -> DatasetFile:
        """Add a file to a dataset version.

        Args:
            version: The latest version of the dataset.
            source: A local file, or the content of the file.
            path: Path of the file within the version. Defaults to the name of `source`
                when it is a local file.
            content_type: Media type of the file. By default, guessed from the extension
                of `path`; compressed files get the type of their compression.

        Raises:
            PermissionDeniedError: The token may not add files to the dataset.
            APIError: The upload failed, `version` is not the latest version (code
                `LOCKED_VERSION`) or it already has a file at `path` (code `ALREADY_EXISTS`).
        """
        if path is None:
            if not isinstance(source, Path):
                raise InvalidInputError("`path` is required when uploading bytes")
            path = source.name
        content_type = content_type or guess_content_type(path)
        dataset = self._reference(version.workspace, version.dataset)
        target = f"'{path}' to dataset '{dataset}' version '{version.name}'"
        variables = {"input": {"versionId": version.id, "uri": path, "contentType": content_type}}
        prepared = self._transport.mutate(
            "generateDatasetUploadUrl",
            UPLOAD_URL_MUTATION,
            variables,
            action=f"upload {target}",
            reasons=FILE_ERRORS,
        )
        self._transport.upload(
            prepared["uploadUrl"],
            source,
            what=target,
            headers=prepared["headers"] or {"Content-Type": content_type},
        )
        result = self._transport.mutate(
            "createDatasetVersionFile",
            CREATE_FILE_MUTATION,
            variables,
            action=f"register {target} after uploading it",
            reasons=FILE_ERRORS,
        )
        return _file(result["file"], version)

    def update(
        self, dataset: str, *, name: str | None = None, description: str | None = None
    ) -> Dataset:
        """Change the name or description of a dataset; `None` leaves a field unchanged.

        The slug does not change with the name.

        Raises:
            NotFoundError: The workspace has no such dataset.
            PermissionDeniedError: The token may not update the dataset.
        """
        changes = {"name": name, "description": description}
        result = self._transport.mutate(
            "updateDataset",
            UPDATE_MUTATION,
            {
                "input": {
                    "datasetId": self._find(GET_QUERY, dataset)["id"],
                    **{key: value for key, value in changes.items() if value is not None},
                }
            },
            action=f"update dataset '{dataset}' in workspace '{self._workspace}'",
            reasons={"PERMISSION_DENIED": "the token may not update this dataset"},
        )
        return _dataset(result["dataset"])

    def update_version(
        self,
        version: DatasetVersion,
        *,
        name: str | None = None,
        changelog: str | None = None,
    ) -> DatasetVersion:
        """Rename a dataset version or change its changelog; `None` leaves a field unchanged.

        Raises:
            NotFoundError: The version no longer exists.
            PermissionDeniedError: The token may not update the dataset.
        """
        changes = {"name": name, "changelog": changelog}
        dataset = self._reference(version.workspace, version.dataset)
        result = self._transport.mutate(
            "updateDatasetVersion",
            UPDATE_VERSION_MUTATION,
            {
                "input": {
                    "versionId": version.id,
                    **{key: value for key, value in changes.items() if value is not None},
                }
            },
            action=f"update version '{version.name}' of dataset '{dataset}' in workspace "
            f"'{self._workspace}'",
            reasons={"PERMISSION_DENIED": "the token may not update this dataset"},
        )
        return replace(
            version,
            name=result["version"]["name"],
            changelog=result["version"]["changelog"] or None,
        )

    def delete_version(self, version: DatasetVersion) -> None:
        """Delete a dataset version and its files.

        If it was the latest version, the previous one becomes the latest.

        Raises:
            NotFoundError: The version no longer exists.
            PermissionDeniedError: The token may not delete versions of the dataset.
        """
        dataset = self._reference(version.workspace, version.dataset)
        self._transport.mutate(
            "deleteDatasetVersion",
            DELETE_VERSION_MUTATION,
            {"input": {"versionId": version.id}},
            action=f"delete version '{version.name}' of dataset '{dataset}' in workspace "
            f"'{self._workspace}'",
            reasons={"PERMISSION_DENIED": "the token may not delete versions of this dataset"},
        )

    def set_attribute(self, dataset: str, key: str, value: Any) -> Dataset:
        """Set the attribute `key` of a dataset to `value`, replacing any previous value.

        Args:
            dataset: Slug of the dataset, or `workspace/slug`.
            key: Name of the attribute, such as `source`.
            value: Any JSON value, such as text, a number or a list.

        Raises:
            NotFoundError: The workspace has no such dataset.
            PermissionDeniedError: The token may not update the dataset.
        """
        self._transport.mutate(
            "setMetadataAttribute",
            SET_ATTRIBUTE_MUTATION,
            {
                "input": {
                    "targetId": self._find(GET_QUERY, dataset)["targetId"],
                    "key": key,
                    "value": value,
                }
            },
            action=f"set attribute '{key}' of dataset '{dataset}' in workspace '{self._workspace}'",
            reasons={"PERMISSION_DENIED": "the token may not update this dataset"},
        )
        return self.get(dataset)

    def delete_attribute(self, dataset: str, key: str) -> Dataset:
        """Remove the attribute `key` of a dataset.

        Raises:
            NotFoundError: The workspace has no such dataset, or it has no attribute `key`.
            PermissionDeniedError: The token may not update the dataset.
        """
        found = self._find(GET_QUERY, dataset)
        current = _dataset(found)
        if key not in current.attributes:
            raise NotFoundError(
                f"Dataset '{dataset}' in workspace '{self._workspace}' has no attribute "
                f"'{key}'. Attributes: {format_options(current.attributes)}"
            )
        self._transport.mutate(
            "deleteMetadataAttribute",
            DELETE_ATTRIBUTE_MUTATION,
            {"input": {"targetId": found["targetId"], "key": key}},
            action=f"delete attribute '{key}' of dataset '{dataset}' in workspace "
            f"'{self._workspace}'",
            reasons={"PERMISSION_DENIED": "the token may not update this dataset"},
        )
        return self.get(dataset)

    def delete(self, dataset: str) -> None:
        """Delete a dataset with all its versions and files.

        Raises:
            NotFoundError: The workspace has no such dataset.
            PermissionDeniedError: The token may not delete the dataset.
        """
        dataset_id = self._find(GET_QUERY, dataset)["id"]
        self._transport.mutate(
            "deleteDataset",
            DELETE_MUTATION,
            {"input": {"id": dataset_id}},
            action=f"delete dataset '{dataset}' in workspace '{self._workspace}'",
            reasons={"PERMISSION_DENIED": "the token may not delete this dataset"},
        )

    def _find(self, query: str, dataset: str, **variables: Any) -> dict[str, Any]:
        """Run a query built by `_dataset_query` and return the dataset object.

        A dataset named `workspace/slug` must be owned by that workspace and, if it is not
        the client's workspace, shared with it.
        """
        owner, slug = _parse_reference(dataset)
        shared = owner is not None and owner != self._workspace
        data = self._transport.query(
            query,
            {
                "workspace": self._workspace,
                "owner": owner or self._workspace,
                "slug": slug,
                "shared": shared,
                **variables,
            },
        )
        link = data["datasetLinkBySlug"]
        found = link and link["dataset"]
        if found and owner is not None and found["workspace"]["slug"] != owner:
            found = None
        if found and shared:
            if data["workspace"] is None:
                raise workspace_not_found(self._transport, self._workspace)
            shared_ids = {item["dataset"]["id"] for item in data["workspace"]["datasets"]["items"]}
            if found["id"] not in shared_ids:
                found = None
        if not found:
            raise NotFoundError(
                f"Dataset '{dataset}' not found in workspace '{self._workspace}': it does not "
                "exist, is not shared with the workspace or the token cannot access it"
            )
        return found

    def _find_version(
        self, query: str, version: DatasetVersion, **variables: Any
    ) -> dict[str, Any]:
        """Run a query on the version `version` and return the version object."""
        dataset = self._reference(version.workspace, version.dataset)
        data = self._find(query, dataset, version=version.id, **variables)["version"]
        if data is None:
            raise NotFoundError(
                f"Version '{version.name}' of dataset '{dataset}' no longer exists in "
                f"workspace '{self._workspace}'"
            )
        return data

    def _reference(self, workspace: str, slug: str) -> str:
        """Name a dataset by its slug if the workspace owns it, `workspace/slug` otherwise."""
        return slug if workspace == self._workspace else f"{workspace}/{slug}"

    def _file_not_found(self, version: DatasetVersion, path: str) -> NotFoundError:
        paths = (file.path for file in self.files(version))
        return NotFoundError(
            f"Dataset '{self._reference(version.workspace, version.dataset)}' version "
            f"'{version.name}' in workspace '{self._workspace}' has no file '{path}'. "
            f"Files: {format_options(paths)}"
        )


def _parse_reference(dataset: str) -> tuple[str | None, str]:
    """Split `workspace/slug` into its parts; a plain slug has no workspace."""
    owner, separator, slug = dataset.rpartition("/")
    if not separator:
        return None, dataset
    if not owner or not slug or "/" in owner:
        raise InvalidInputError(f"Invalid dataset '{dataset}': expected 'slug' or 'workspace/slug'")
    return owner, slug


def _dataset(data: dict[str, Any]) -> Dataset:
    return Dataset(
        slug=data["slug"],
        workspace=data["workspace"]["slug"],
        name=data["name"],
        description=data["description"] or "",
        latest_version=data["latestVersion"]["name"] if data["latestVersion"] else None,
        # System attributes are written by OpenHEXA itself and are not editable.
        attributes={
            item["key"]: item["value"] for item in data["attributes"] if not item["system"]
        },
        id=data["id"],
    )


def _version(data: dict[str, Any], dataset: dict[str, Any]) -> DatasetVersion:
    """Build a version from its data and that of its dataset, from `_find`."""
    return DatasetVersion(
        dataset=dataset["slug"],
        workspace=dataset["workspace"]["slug"],
        name=data["name"],
        changelog=data["changelog"] or None,
        created_at=datetime.fromisoformat(data["createdAt"]),
        id=data["id"],
    )


def _file(data: dict[str, Any], version: DatasetVersion) -> DatasetFile:
    """Build a file, whose path is its URI without the dataset and version IDs."""
    _, _, path = data["uri"].split("/", 2)
    return DatasetFile(
        dataset=version.dataset,
        workspace=version.workspace,
        version=version.name,
        path=path,
        size=int(data["size"]),
        content_type=data["contentType"],
        id=data["id"],
        version_id=version.id,
    )
