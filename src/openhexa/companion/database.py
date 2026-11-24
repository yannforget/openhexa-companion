"""The PostgreSQL database of a workspace."""

import builtins
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from openhexa.companion._transport import Transport
from openhexa.companion._util import format_options
from openhexa.companion.errors import (
    APIError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)
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

TABLE_QUERY = """
query DatabaseTable($workspace: String!, $name: String!) {
  workspace(slug: $workspace) {
    database {
      table(name: $name) { name count columns { name type } }
    }
  }
}
"""

EXECUTE_QUERY = """
query ExecuteSQL($workspace: String!, $sql: String!, $maxRows: Int!) {
  workspace(slug: $workspace) {
    database {
      executeSQL(query: $sql, maxRows: $maxRows) {
        success
        errors
        errorMessage
        columns
        rows
        truncated
        durationMs
      }
    }
  }
}
"""

# Seconds OpenHEXA lets a query run before cancelling it.
QUERY_TIMEOUT = 10
# Explanations of the error codes of a failed query.
QUERY_ERRORS = {
    "QUERY_ERROR": "the query is invalid or not allowed",
    "QUERY_TIMEOUT": f"the query ran for more than {QUERY_TIMEOUT} seconds and was cancelled; "
    "filter or aggregate more, or query fewer rows",
    "MULTIPLE_STATEMENTS": "only a single SQL statement is allowed",
}


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
class TableColumn:
    """A column of a database table.

    Attributes:
        name: Name of the column.
        type: PostgreSQL data type, such as `integer` or `text`.
    """

    name: str
    type: str


@dataclass(frozen=True)
class DatabaseTable:
    """A table of the workspace database.

    Attributes:
        name: Name of the table.
        rows: Number of rows, if known.
        columns: Columns of the table, or `None` when not fetched, as in listings.
    """

    name: str
    rows: int | None
    columns: builtins.list[TableColumn] | None = None


@dataclass(frozen=True)
class QueryResult:
    """The result of a SQL query.

    Attributes:
        columns: Names of the columns.
        rows: Rows, each a list of values in the order of `columns`. Values are JSON:
            timestamps are ISO strings and numerics strings, to keep their precision.
        truncated: Whether the query returned more rows than `rows` holds.
        duration_ms: Time the query took in the database, in milliseconds.
    """

    columns: builtins.list[str]
    rows: builtins.list[builtins.list[Any]]
    truncated: bool
    duration_ms: int | None


class Database:
    """Reads the tables, data and credentials of the workspace database."""

    def __init__(self, transport: Transport, workspace: str) -> None:
        self._transport = transport
        self._workspace = workspace

    def tables(self, *, limit: int | None = None) -> builtins.list[DatabaseTable]:
        """List the tables of the workspace database.

        Args:
            limit: Maximum number of tables, or `None` for all of them.
        """

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            variables = {"workspace": self._workspace, "page": page, "perPage": per_page}
            return self._database(TABLES_QUERY, variables)["tables"]

        return [
            DatabaseTable(name=item["name"], rows=item["count"])
            for item in self._transport.paginate(fetch_page, limit)
        ]

    def table(self, name: str) -> DatabaseTable:
        """Get a table of the workspace database, with its columns.

        Raises:
            NotFoundError: The database has no table `name`.
        """
        variables = {"workspace": self._workspace, "name": name}
        data = self._database(TABLE_QUERY, variables)["table"]
        if data is None:
            names = (table.name for table in self.tables())
            raise NotFoundError(
                f"Table '{name}' not found in the database of workspace '{self._workspace}'. "
                f"Tables: {format_options(names)}"
            )
        return DatabaseTable(
            name=data["name"],
            rows=data["count"],
            columns=[TableColumn(name=c["name"], type=c["type"]) for c in data["columns"]],
        )

    def query(self, sql: str, *, max_rows: int = 50) -> QueryResult:
        """Run a read-only SQL query on the workspace database through OpenHEXA.

        OpenHEXA runs one PostgreSQL statement with a read-only role, cancels it after
        10 seconds and records it in its audit log under the token's user. Any workspace
        member may query, unlike `credentials`.

        Args:
            sql: A single SQL statement.
            max_rows: Maximum number of rows to return. OpenHEXA also caps it.

        Raises:
            InvalidInputError: `max_rows` is below 1, the query is invalid, timed out or
                has several statements, or the result has duplicate column names.
            PermissionDeniedError: The token may not query the database.
        """
        if max_rows < 1:
            raise InvalidInputError(f"Invalid max_rows {max_rows}: expected at least 1")
        variables = {"workspace": self._workspace, "sql": sql, "maxRows": max_rows}
        result = self._database(EXECUTE_QUERY, variables)["executeSQL"]
        codes: builtins.list[str] = result["errors"]
        if not result["success"]:
            explanations = "; ".join(QUERY_ERRORS.get(code, code) for code in codes)
            message = f"Could not query the database of workspace '{self._workspace}': "
            message += explanations or "no reason given"
            if result["errorMessage"]:
                message += f". PostgreSQL said: {result['errorMessage']}"
            if "PERMISSION_DENIED" in codes:
                raise PermissionDeniedError(message, codes)
            if codes and all(code in QUERY_ERRORS for code in codes):
                raise InvalidInputError(message, codes)
            raise APIError(message, codes)
        columns: builtins.list[str] = result["columns"] or []
        duplicates = [name for name, count in Counter(columns).items() if count > 1]
        if duplicates:
            # Rows arrive as objects keyed by column name, so duplicates lose values.
            raise InvalidInputError(
                f"The query returns several columns named {format_options(duplicates)}: "
                "give each column a distinct name with AS"
            )
        return QueryResult(
            columns=columns,
            rows=[[row[name] for name in columns] for row in result["rows"] or []],
            truncated=bool(result["truncated"]),
            duration_ms=result["durationMs"],
        )

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
