"""Connections of a workspace: credentials for external systems such as DHIS2 or S3."""

import builtins
from dataclasses import dataclass, field
from typing import Any

from openhexa.companion._transport import Transport
from openhexa.companion._util import format_options
from openhexa.companion.errors import NotFoundError
from openhexa.companion.workspace import workspace_not_found

CONNECTION_FIELDS = """
  slug
  name
  type
  description
  fields { code secret value }
"""

LIST_QUERY = f"""
query Connections($workspace: String!) {{
  workspace(slug: $workspace) {{
    connections {{ {CONNECTION_FIELDS} }}
  }}
}}
"""

GET_QUERY = f"""
query Connection($workspace: String!, $slug: String!) {{
  connectionBySlug(workspaceSlug: $workspace, connectionSlug: $slug) {{ {CONNECTION_FIELDS} }}
}}
"""


@dataclass(frozen=True)
class ConnectionField:
    """A field of a connection.

    Attributes:
        code: Name of the field, such as `url` or `password`.
        secret: Whether the value is a secret. Never show or log secret values outside
            the person's own machine.
        value: The value, or `None` for a secret the token may not read: only workspace
            editors and admins can.
    """

    code: str
    secret: bool
    value: str | None = field(repr=False)


@dataclass(frozen=True)
class Connection:
    """Credentials for an external system, configured in the workspace.

    Attributes:
        slug: Identifies the connection, as pipelines name it.
        name: Display name.
        type: One of `CUSTOM`, `DHIS2`, `GCS`, `IASO`, `POSTGRESQL` or `S3`.
        description: What the connection is for, if documented.
        fields: Fields of the connection, including secret ones.
    """

    slug: str
    name: str
    type: str
    description: str
    fields: builtins.list[ConnectionField]

    def values(self, *, include_secrets: bool = False) -> dict[str, str | None]:
        """Return the field values by code, with secret values only if asked."""
        return {
            item.code: item.value if include_secrets or not item.secret else None
            for item in self.fields
        }


class Connections:
    """Reads the connections of a workspace.

    Results include secret values when the token may read them. Interfaces that act for
    a remote client must not return them.
    """

    def __init__(self, transport: Transport, workspace: str) -> None:
        self._transport = transport
        self._workspace = workspace

    def list(self) -> builtins.list[Connection]:
        """List the connections of the workspace."""
        data = self._transport.query(LIST_QUERY, {"workspace": self._workspace})["workspace"]
        if data is None:
            raise workspace_not_found(self._transport, self._workspace)
        return [_connection(item) for item in data["connections"]]

    def get(self, slug: str) -> Connection:
        """Get the connection `slug`.

        Raises:
            NotFoundError: The workspace has no connection `slug`.
        """
        variables = {"workspace": self._workspace, "slug": slug}
        data = self._transport.query(GET_QUERY, variables)["connectionBySlug"]
        if data is None:
            slugs = (item.slug for item in self.list())
            raise NotFoundError(
                f"Connection '{slug}' not found in workspace '{self._workspace}'. "
                f"Connections: {format_options(slugs)}"
            )
        return _connection(data)


def _connection(data: dict[str, Any]) -> Connection:
    return Connection(
        slug=data["slug"],
        name=data["name"],
        type=data["type"],
        description=data["description"] or "",
        fields=[
            ConnectionField(code=item["code"], secret=item["secret"], value=item["value"])
            for item in data["fields"]
        ],
    )
