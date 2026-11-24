"""Webapps of a workspace, especially static ones published from a directory of files."""

import base64
import builtins
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from openhexa.companion._transport import Transport
from openhexa.companion._util import format_options
from openhexa.companion.errors import InvalidInputError, NotFoundError

WEBAPP_FIELDS = """
  id
  slug
  name
  description
  type
  url
  isPublic
  allowedOperations
  source { __typename ... on GitSource { publishedVersion } }
"""

LIST_QUERY = f"""
query Webapps($workspace: String!, $page: Int!, $perPage: Int!) {{
  webapps(workspaceSlug: $workspace, page: $page, perPage: $perPage) {{
    totalPages
    items {{ {WEBAPP_FIELDS} }}
  }}
}}
"""

GET_QUERY = f"""
query Webapp($workspace: String!, $slug: String!) {{
  webapp(workspaceSlug: $workspace, slug: $slug) {{ {WEBAPP_FIELDS} }}
}}
"""

FILES_QUERY = """
query WebappFiles($workspace: String!, $slug: String!, $ref: String, $content: Boolean!) {
  webapp(workspaceSlug: $workspace, slug: $slug) {
    files(ref: $ref, includeBinaryContent: $content) {
      path
      type
      size
      encoding
      tooLarge
      content @include(if: $content)
    }
  }
}
"""

VERSIONS_QUERY = """
query WebappVersions($workspace: String!, $slug: String!, $page: Int!, $perPage: Int!) {
  webapp(workspaceSlug: $workspace, slug: $slug) {
    versions(page: $page, perPage: $perPage) {
      items { id message date authorName }
    }
  }
}
"""

CREATE_MUTATION = f"""
mutation CreateWebapp($input: CreateWebappInput!) {{
  createWebapp(input: $input) {{
    success
    errors
    webapp {{ {WEBAPP_FIELDS} }}
  }}
}}
"""

UPDATE_MUTATION = f"""
mutation UpdateWebapp($input: UpdateWebappInput!) {{
  updateWebapp(input: $input) {{
    success
    errors
    webapp {{ {WEBAPP_FIELDS} }}
  }}
}}
"""

DELETE_MUTATION = """
mutation DeleteWebapp($input: DeleteWebappInput!) {
  deleteWebapp(input: $input) {
    success
    errors
  }
}
"""

# Explanations of the error codes returned by webapp mutations.
WEBAPP_ERRORS = {
    "ALREADY_EXISTS": "a webapp with this name already exists in the workspace",
    "PERMISSION_DENIED": "the token may not manage webapps in this workspace; only "
    "workspace editors and admins can",
    "SAVE_FAILED": "OpenHEXA could not store the files",
    "TYPE_MISMATCH": "only static webapps hold files",
    "VERSION_NOT_FOUND": "the version does not exist",
    "WEBAPPS_NOT_CONFIGURED": "webapps are not enabled on this OpenHEXA instance",
    "WEBAPP_NOT_FOUND": "the webapp does not exist",
    "REQUEST_TOO_LARGE": "the files are larger than this OpenHEXA instance accepts; load "
    "large data from a dataset instead",
}
# Versions requested per page: OpenHEXA reads them from Forgejo, which silently caps
# larger pages (at 50 by default), so a short page reliably means the last one.
VERSIONS_PAGE_SIZE = 20
# Directories that are never part of a static site, even if not hidden.
EXCLUDED_DIRECTORIES = frozenset({"node_modules", "__pycache__"})


@dataclass(frozen=True)
class Webapp:
    """A webapp of the workspace.

    Attributes:
        slug: Identifies the webapp within the workspace.
        name: Display name.
        description: What the webapp shows, if documented.
        type: `STATIC` for sites published from files, `IFRAME` for an embedded URL,
            or `SUPERSET` for a dashboard.
        url: Where the webapp is served.
        public: Whether anyone can open it without signing in to OpenHEXA.
        scopes: OpenHEXA operations the webapp may perform for the person viewing it,
            such as `DATASETS_READ`.
        version: ID of the published version of a static webapp.
        id: OpenHEXA identifier.
    """

    slug: str
    name: str
    description: str
    type: str
    url: str
    public: bool
    scopes: builtins.list[str]
    version: str | None
    id: str


@dataclass(frozen=True)
class WebappFile:
    """A file of a static webapp.

    Attributes:
        path: Path from the root of the site, such as `assets/app.js`.
        size: Size in bytes.
        content: The bytes of the file, or `None` if not requested or too large to
            read through the API.
    """

    path: str
    size: int
    content: bytes | None = None


@dataclass(frozen=True)
class WebappVersion:
    """A published version of a static webapp: one change to its files.

    Attributes:
        id: Identifies the version.
        message: Description of the change.
        created_at: When the version was published.
        author: Who published it.
    """

    id: str
    message: str
    created_at: datetime
    author: str


@dataclass(frozen=True)
class Publication:
    """The result of publishing a static webapp.

    Attributes:
        webapp: The webapp after publishing.
        uploaded: Paths of the files sent.
        deleted: Paths of the files removed because they are no longer in the source.
    """

    webapp: Webapp
    uploaded: builtins.list[str]
    deleted: builtins.list[str]


class Webapps:
    """Reads, publishes and configures the webapps of a workspace."""

    def __init__(self, transport: Transport, workspace: str) -> None:
        self._transport = transport
        self._workspace = workspace

    def list(self, *, limit: int | None = None) -> builtins.list[Webapp]:
        """List the webapps of the workspace.

        Args:
            limit: Maximum number of webapps, or `None` for all of them.
        """

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            variables = {"workspace": self._workspace, "page": page, "perPage": per_page}
            return self._transport.query(LIST_QUERY, variables)["webapps"]

        return [_webapp(item) for item in self._transport.paginate(fetch_page, limit)]

    def get(self, slug: str) -> Webapp:
        """Get the webapp `slug`.

        Raises:
            NotFoundError: The workspace has no webapp `slug`.
        """
        return _webapp(self._find(GET_QUERY, slug))

    def files(
        self, slug: str, *, version: str | None = None, content: bool = False
    ) -> builtins.list[WebappFile]:
        """List the files of a static webapp.

        Args:
            slug: Slug of the webapp.
            version: ID of a version, or `None` for the one the webapp serves, which is
                the latest unless an earlier one was served again with `update`.
            content: Include the bytes of each file. Files of 10 MB or more are not
                readable through the API and have no content.

        Raises:
            NotFoundError: The workspace has no webapp `slug`.
        """
        return self._files(slug, version or self.get(slug).version, content=content)

    def _files(self, slug: str, ref: str | None, *, content: bool) -> builtins.list[WebappFile]:
        """List the files at `ref`, a version ID, or at the latest version if `None`."""
        data = self._find(FILES_QUERY, slug, ref=ref, content=content)["files"] or []
        return [_file(item) for item in data if item["type"] == "file"]

    def versions(self, slug: str, *, limit: int | None = 20) -> builtins.list[WebappVersion]:
        """List the versions of a static webapp, newest first.

        Args:
            slug: Slug of the webapp.
            limit: Maximum number of versions, or `None` for all of them.

        Raises:
            NotFoundError: The workspace has no webapp `slug`.
        """
        items: builtins.list[dict[str, Any]] = []
        page = 1
        while limit is None or len(items) < limit:
            data = self._find(VERSIONS_QUERY, slug, page=page, perPage=VERSIONS_PAGE_SIZE)
            batch = data["versions"]["items"] if data["versions"] else []
            items.extend(batch)
            if len(batch) < VERSIONS_PAGE_SIZE:
                break
            page += 1
        return [
            WebappVersion(
                id=item["id"],
                message=item["message"],
                created_at=datetime.fromisoformat(item["date"]),
                author=item["authorName"],
            )
            for item in items[:limit]
        ]

    def create(
        self,
        name: str,
        source: Path,
        *,
        description: str | None = None,
        public: bool = False,
        scopes: builtins.list[str] | None = None,
    ) -> Publication:
        """Create a static webapp from the files in the directory `source`.

        See `publish` for the files sent. OpenHEXA derives the slug from `name`.

        Args:
            name: Name of the webapp.
            source: Directory with `index.html` at its root, such as a build output.
            description: What the webapp shows.
            public: Let anyone open it without signing in to OpenHEXA.
            scopes: OpenHEXA operations the webapp may perform for the person viewing
                it, such as `DATASETS_READ`.

        Raises:
            InvalidInputError: `source` has no `index.html`.
            PermissionDeniedError: The token may not create webapps.
            APIError: OpenHEXA rejected the webapp, with the reason in the message.
        """
        files = package_site(source)
        result = self._transport.mutate(
            "createWebapp",
            CREATE_MUTATION,
            {
                "input": {
                    "workspaceSlug": self._workspace,
                    "name": name,
                    # OpenHEXA fails, reporting ALREADY_EXISTS, on a null description.
                    "description": description or "",
                    "isPublic": public,
                    "allowedOperations": scopes or [],
                    "source": {"static": files},
                }
            },
            action=f"create webapp '{name}' in workspace '{self._workspace}'",
            reasons=WEBAPP_ERRORS,
        )
        return Publication(
            webapp=_webapp(result["webapp"]),
            uploaded=[file["path"] for file in files],
            deleted=[],
        )

    def publish(self, slug: str, source: Path, *, message: str | None = None) -> Publication:
        """Publish the files in the directory `source` as the new content of a webapp.

        The webapp ends up with exactly the files of `source`: files no longer in
        `source` are removed. Hidden files and directories (such as `.env` and `.git`),
        `node_modules` and `__pycache__` are not sent. The change is published at once
        and becomes a new version, which the webapp serves even if it served an earlier
        one.

        Args:
            slug: Slug of a static webapp.
            source: Directory with `index.html` at its root, such as a build output.
            message: Description of the change.

        Raises:
            InvalidInputError: `source` has no `index.html`.
            NotFoundError: The workspace has no webapp `slug`.
            PermissionDeniedError: The token may not update the webapp.
            APIError: OpenHEXA rejected the files, with the reason in the message.
        """
        files = package_site(source)
        webapp = self.get(slug)
        sent = {file["path"] for file in files}
        # Publishing adds a version after the latest one, even if an earlier one is served.
        latest = self._files(slug, None, content=False)
        deleted = sorted(file.path for file in latest if file.path not in sent)
        result = self._transport.mutate(
            "updateWebapp",
            UPDATE_MUTATION,
            {
                "input": {
                    "id": webapp.id,
                    "files": files,
                    "filesToDelete": deleted,
                    "commitMessage": message,
                }
            },
            action=f"publish webapp '{slug}' in workspace '{self._workspace}'",
            reasons=WEBAPP_ERRORS,
        )
        return Publication(
            webapp=_webapp(result["webapp"]),
            uploaded=[file["path"] for file in files],
            deleted=deleted,
        )

    def update(
        self,
        slug: str,
        *,
        name: str | None = None,
        description: str | None = None,
        public: bool | None = None,
        scopes: builtins.list[str] | None = None,
        version: str | None = None,
    ) -> Webapp:
        """Change a webapp's settings or the version it serves; `None` keeps a field.

        Args:
            slug: Slug of the webapp. It does not change with the name.
            name: The new display name.
            description: What the webapp shows.
            public: Let anyone open it without signing in to OpenHEXA, or only workspace
                members.
            scopes: Replace the OpenHEXA operations the webapp may perform for the person
                viewing it, such as `DATASETS_READ`; an empty list removes them all.
            version: Serve this version of a static webapp: its ID, or the start of it,
                as listed by `versions`. Newer versions are kept, so a later call can
                serve them again.

        Raises:
            InvalidInputError: No change is given, or `version` matches several versions.
            NotFoundError: The workspace has no webapp `slug`, or it has no such version.
            PermissionDeniedError: The token may not update the webapp: only workspace
                editors and admins can.
            APIError: OpenHEXA rejected the change, such as a version for a webapp that
                is not static (code `TYPE_MISMATCH`).
        """
        changes: dict[str, Any] = {
            "name": name,
            "description": description,
            "isPublic": public,
            "allowedOperations": scopes,
        }
        changes = {key: value for key, value in changes.items() if value is not None}
        if not changes and version is None:
            raise InvalidInputError(f"No change given for webapp '{slug}'")
        webapp_id = self.get(slug).id
        # OpenHEXA drops the other changes of a request that also changes the served
        # version, so the version is changed in a request of its own.
        requests = [changes] if changes else []
        if version is not None:
            requests.append({"publishedVersionId": self._version_id(slug, version)})
        result: dict[str, Any] = {}
        for request in requests:
            result = self._transport.mutate(
                "updateWebapp",
                UPDATE_MUTATION,
                {"input": {"id": webapp_id, **request}},
                action=f"update webapp '{slug}' in workspace '{self._workspace}'",
                reasons=WEBAPP_ERRORS,
            )
        return _webapp(result["webapp"])

    def delete(self, slug: str) -> None:
        """Delete a webapp.

        Raises:
            NotFoundError: The workspace has no webapp `slug`.
            PermissionDeniedError: The token may not delete the webapp.
        """
        webapp_id = self.get(slug).id
        self._transport.mutate(
            "deleteWebapp",
            DELETE_MUTATION,
            {"input": {"id": webapp_id}},
            action=f"delete webapp '{slug}' in workspace '{self._workspace}'",
            reasons=WEBAPP_ERRORS,
        )

    def _version_id(self, slug: str, version: str) -> str:
        """The full ID of the version of `slug` whose ID is or starts with `version`."""
        versions = self.versions(slug, limit=None)
        matches = [item.id for item in versions if item.id.startswith(version)]
        if not version or not matches:
            raise NotFoundError(
                f"Webapp '{slug}' in workspace '{self._workspace}' has no version '{version}'. "
                f"Versions, newest first: {format_options(item.id for item in versions)}"
            )
        if len(matches) > 1:
            raise InvalidInputError(
                f"Version '{version}' of webapp '{slug}' is ambiguous: give more of the ID "
                f"among {format_options(matches)}"
            )
        return matches[0]

    def _find(self, query: str, slug: str, **variables: Any) -> dict[str, Any]:
        """Run a query on the webapp `slug` and return the webapp object."""
        data = self._transport.query(
            query, {"workspace": self._workspace, "slug": slug, **variables}
        )["webapp"]
        if data is None:
            slugs = (webapp.slug for webapp in self.list())
            raise NotFoundError(
                f"Webapp '{slug}' not found in workspace '{self._workspace}'. "
                f"Webapps: {format_options(slugs)}"
            )
        return data


def package_site(source: Path) -> builtins.list[dict[str, str]]:
    """Read the files of the site in `source` for a publication request.

    Text files are sent as is, others in base64.

    Raises:
        InvalidInputError: `source` has no `index.html`.
    """
    if not (source / "index.html").is_file():
        raise InvalidInputError(f"No index.html at the root of {source}")
    files = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not path.is_file() or not _is_site_file(relative.parts):
            continue
        raw = path.read_bytes()
        try:
            item = {"path": relative.as_posix(), "content": raw.decode("utf-8"), "encoding": "TEXT"}
        except UnicodeDecodeError:
            item = {
                "path": relative.as_posix(),
                "content": base64.b64encode(raw).decode("ascii"),
                "encoding": "BASE64",
            }
        files.append(item)
    return files


def _is_site_file(parts: tuple[str, ...]) -> bool:
    return not any(part.startswith(".") or part in EXCLUDED_DIRECTORIES for part in parts)


def _webapp(data: dict[str, Any]) -> Webapp:
    source = data["source"]
    return Webapp(
        slug=data["slug"],
        name=data["name"],
        description=data["description"] or "",
        type=data["type"],
        url=data["url"],
        public=data["isPublic"],
        scopes=data["allowedOperations"],
        version=source.get("publishedVersion") if source else None,
        id=data["id"],
    )


def _file(data: dict[str, Any]) -> WebappFile:
    content = data.get("content")
    if content is not None:
        content = base64.b64decode(content) if data["encoding"] == "BASE64" else content.encode()
    return WebappFile(path=data["path"], size=data["size"] or 0, content=content)
