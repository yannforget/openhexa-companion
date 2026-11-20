"""The PostgreSQL database of a workspace."""

import builtins
from dataclasses import dataclass, field
from typing import Any

from openhexa.companion._transport import Transport
from openhexa.companion.errors import PermissionDeniedError
from openhexa.companion.workspace import workspace_not_found

CREDENTIALS_FIELDS = """
  host
  port
  dbName
  username
  password
  url
"""

CREDENTIALS_QUERY = f"""
query DatabaseCredentials($workspace: String!) {{
  workspace(slug: $workspace) {{
    database {{
      credentials {{ {CREDENTIALS_FIELDS} }}
      readOnlyCredentials {{ {CREDENTIALS_FIELDS} }}
    }}
  }}
}}
"""

TABLES_QUERY = """
query DatabaseTables($workspace: String!, $page: Int!, $perPage: Int!) {
  workspace(slug: $workspace) {
    database {
      tables(page: $page, perPage: $perPage, withCounts: true) {
        totalPages
        items { name count }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class DatabaseCredentials:
    """Credentials to connect to the workspace database.

    All fields are secret: never show or log them outside the person's own machine.

    Attributes:
        host: Host name of the server.
        port: Port of the server.
        database: Name of the database.
        username: Name of the user.
        password: Password of the user.
        url: All of the above as a `postgresql://` URL.
    """

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    url: str = field(repr=False)


@dataclass(frozen=True)
class DatabaseTable:
    """A table of the workspace database.

    Attributes:
        name: Name of the table.
        rows: Number of rows, if known.
    """

    name: str
    rows: int | None


class Database:
    """Reads the tables and credentials of the workspace database."""

    def __init__(self, transport: Transport, workspace: str) -> None:
        self._transport = transport
        self._workspace = workspace

    def tables(self) -> builtins.list[DatabaseTable]:
        """List the tables of the workspace database."""

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            variables = {"workspace": self._workspace, "page": page, "perPage": per_page}
            return self._database(TABLES_QUERY, variables)["tables"]

        return [
            DatabaseTable(name=item["name"], rows=item["count"])
            for item in self._transport.paginate(fetch_page)
        ]

    def credentials(self, *, read_only: bool = False) -> DatabaseCredentials:
        """Get credentials to connect to the workspace database.

        Interfaces that act for a remote client must not return them.

        Args:
            read_only: Get the credentials of a user that may only read.

        Raises:
            PermissionDeniedError: The token may not see the credentials, which only
                workspace editors and admins can.
        """
        data = self._database(CREDENTIALS_QUERY, {"workspace": self._workspace})
        credentials = data["readOnlyCredentials" if read_only else "credentials"]
        if credentials is None:
            raise PermissionDeniedError(
                f"The token may not see the database credentials of workspace "
                f"'{self._workspace}': only workspace editors and admins can"
            )
        return DatabaseCredentials(
            host=credentials["host"],
            port=credentials["port"],
            database=credentials["dbName"],
            username=credentials["username"],
            password=credentials["password"],
            url=credentials["url"],
        )

    def _database(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        data = self._transport.query(query, variables)["workspace"]
        if data is None:
            raise workspace_not_found(self._transport, self._workspace)
        return data["database"]
