"""Files stored in the workspace bucket, the files shared by its pipelines and notebooks."""

import builtins
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from openhexa.companion._transport import PAGE_SIZE, ByteStream, Transport
from openhexa.companion._util import format_options, guess_content_type
from openhexa.companion.errors import NotFoundError
from openhexa.companion.workspace import workspace_not_found

OBJECT_FIELDS = """
  key
  type
  size
  updatedAt
"""

LIST_QUERY = f"""
query WorkspaceFiles($workspace: String!, $prefix: String, $page: Int!, $perPage: Int!) {{
  workspace(slug: $workspace) {{
    bucket {{
      objects(prefix: $prefix, page: $page, perPage: $perPage) {{
        hasNextPage
        items {{ {OBJECT_FIELDS} }}
      }}
    }}
  }}
}}
"""

GET_QUERY = f"""
query WorkspaceFile($workspace: String!, $key: String!) {{
  workspace(slug: $workspace) {{
    bucket {{
      object(key: $key) {{ {OBJECT_FIELDS} }}
    }}
  }}
}}
"""

DOWNLOAD_MUTATION = """
mutation PrepareObjectDownload($input: PrepareObjectDownloadInput!) {
  prepareObjectDownload(input: $input) {
    success
    errors
    downloadUrl
  }
}
"""

UPLOAD_MUTATION = """
mutation PrepareObjectUpload($input: PrepareObjectUploadInput!) {
  prepareObjectUpload(input: $input) {
    success
    errors
    uploadUrl
    headers
  }
}
"""

DELETE_MUTATION = """
mutation DeleteBucketObject($input: DeleteBucketObjectInput!) {
  deleteBucketObject(input: $input) {
    success
    errors
  }
}
"""

# Explanations of the error codes returned by file mutations.
FILE_ERRORS = {
    "INVALID_PATH": "the path is not a valid relative path",
    "NOT_FOUND": "the file does not exist",
    "PERMISSION_DENIED": "the token may not access the workspace files",
}


@dataclass(frozen=True)
class WorkspaceFile:
    """A file or directory in the workspace files.

    Attributes:
        path: Path from the root of the workspace files. Directory paths end with `/`.
        type: `file` or `directory`.
        size: Size in bytes; 0 for directories.
        updated_at: When the file was last written, if known.
    """

    path: str
    type: str
    size: int
    updated_at: datetime | None

    @property
    def is_directory(self) -> bool:
        return self.type == "directory"


class Files:
    """Reads and writes the workspace files, as pipelines and notebooks see them.

    Paths are relative to the root of the workspace files, such as `data/cases.csv`.
    """

    def __init__(self, transport: Transport, workspace: str) -> None:
        self._transport = transport
        self._workspace = workspace

    def list(self, directory: str = "", *, recursive: bool = False) -> builtins.list[WorkspaceFile]:
        """List the files and directories in `directory`, or the root by default.

        Hidden files, whose name starts with a dot, are not listed.

        Args:
            directory: Path of the directory.
            recursive: List the files of subdirectories too, instead of the
                subdirectories themselves.

        Raises:
            NotFoundError: `directory` does not exist.
        """
        prefix = _directory_prefix(directory)
        items = list(self._list(prefix))
        if prefix and not items and self._get(prefix) is None:
            raise NotFoundError(
                f"Directory '{directory}' not found in the files of workspace "
                f"'{self._workspace}'{self._siblings(directory)}"
            )
        if not recursive:
            return items
        found = [item for item in items if not item.is_directory]
        pending = [item.path for item in items if item.is_directory]
        while pending:
            for item in self._list(pending.pop()):
                if item.is_directory:
                    pending.append(item.path)
                else:
                    found.append(item)
        return sorted(found, key=lambda item: item.path)

    def get(self, path: str) -> WorkspaceFile:
        """Get the file or directory at `path`.

        Raises:
            NotFoundError: Nothing exists at `path`.
        """
        item = self._get(path) or self._get_directory(path)
        if item is None:
            raise NotFoundError(
                f"'{path}' not found in the files of workspace '{self._workspace}'"
                f"{self._siblings(path)}"
            )
        return item

    @contextmanager
    def read(
        self, path: str, *, offset: int = 0, length: int | None = None
    ) -> Generator[ByteStream]:
        """Stream the bytes of the file at `path`, or `length` bytes from `offset`.

        Raises:
            NotFoundError: There is no file at `path`.
            PermissionDeniedError: The token may not download workspace files.
        """
        result = self._transport.mutate(
            "prepareObjectDownload",
            DOWNLOAD_MUTATION,
            {
                "input": {
                    "workspaceSlug": self._workspace,
                    "objectKey": path,
                    "forceAttachment": False,
                }
            },
            action=f"read '{path}' from the files of workspace '{self._workspace}'",
            reasons=FILE_ERRORS,
        )
        what = f"'{path}' of the files of workspace '{self._workspace}'"
        with self._transport.download(
            result["downloadUrl"], what=what, offset=offset, length=length
        ) as stream:
            yield stream

    def upload(
        self, source: Path | bytes, path: str, content_type: str | None = None
    ) -> WorkspaceFile:
        """Write a file at `path`, replacing any file already there.

        Args:
            source: A local file, or the content of the file.
            path: Path of the file in the workspace files.
            content_type: Media type of the file. Guessed from `path` by default.

        Raises:
            PermissionDeniedError: The token may not write workspace files.
            APIError: The upload failed or `path` is invalid (code `INVALID_PATH`).
        """
        content_type = content_type or guess_content_type(path)
        what = f"'{path}' to the files of workspace '{self._workspace}'"
        result = self._transport.mutate(
            "prepareObjectUpload",
            UPLOAD_MUTATION,
            {
                "input": {
                    "workspaceSlug": self._workspace,
                    "objectKey": path,
                    "contentType": content_type,
                }
            },
            action=f"upload {what}",
            reasons=FILE_ERRORS,
        )
        self._transport.upload(
            result["uploadUrl"],
            source,
            what=what,
            headers=result["headers"] or {"Content-Type": content_type},
        )
        return self.get(path)

    def delete(self, path: str) -> None:
        """Delete the file at `path`, or the directory at `path` with all its content.

        Raises:
            NotFoundError: Nothing exists at `path`.
            PermissionDeniedError: The token may not delete workspace files.
        """
        key = self.get(path).path
        self._transport.mutate(
            "deleteBucketObject",
            DELETE_MUTATION,
            {"input": {"workspaceSlug": self._workspace, "objectKey": key}},
            action=f"delete '{path}' from the files of workspace '{self._workspace}'",
            reasons=FILE_ERRORS,
        )

    def _list(self, prefix: str) -> Iterator[WorkspaceFile]:
        """Yield the items directly under `prefix`, walking `hasNextPage` pages."""
        page = 1
        while True:
            variables = {
                "workspace": self._workspace,
                "prefix": prefix or None,
                "page": page,
                "perPage": PAGE_SIZE,
            }
            objects = self._bucket(LIST_QUERY, variables)["objects"]
            for item in objects["items"]:
                # GCS lists a directory's placeholder object under its own prefix.
                if item["key"] != prefix:
                    yield _file(item)
            if not objects["hasNextPage"]:
                return
            page += 1

    def _get_directory(self, path: str) -> WorkspaceFile | None:
        """Find the directory at `path`, which may only exist through the files it holds.

        Storage creates directories implicitly when a file is written under them, without
        an object of their own.
        """
        prefix = _directory_prefix(path)
        if not prefix:
            return None
        found = self._get(prefix)
        if found is None and next(self._list(prefix), None) is not None:
            found = WorkspaceFile(path=prefix, type="directory", size=0, updated_at=None)
        return found

    def _get(self, key: str) -> WorkspaceFile | None:
        data = self._bucket(GET_QUERY, {"workspace": self._workspace, "key": key})["object"]
        return _file(data) if data else None

    def _bucket(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        data = self._transport.query(query, variables)["workspace"]
        if data is None:
            raise workspace_not_found(self._transport, self._workspace)
        return data["bucket"]

    def _siblings(self, path: str) -> str:
        """List what the parent of `path` contains, to help find a mistyped path."""
        parent = str(PurePosixPath(path.rstrip("/")).parent)
        prefix = _directory_prefix("" if parent == "." else parent)
        names = [item.path.removeprefix(prefix) for item in self._list(prefix)]
        if prefix and not names:
            return f": directory '{parent}' does not exist either"
        return f". '{prefix or '/'}' contains: {format_options(names)}"


def _directory_prefix(path: str) -> str:
    path = path.strip("/")
    return f"{path}/" if path else ""


def _file(data: dict[str, Any]) -> WorkspaceFile:
    updated_at = data["updatedAt"]
    return WorkspaceFile(
        path=data["key"],
        type=data["type"].lower(),
        size=int(data["size"] or 0),
        updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
    )
