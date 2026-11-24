"""Tools reading the workspace database and querying it with SQL."""

import json
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field, JsonValue

from openhexa.companion import QueryResult
from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import (
    DEFAULT_LIST_LIMIT,
    READ_ONLY,
    ListLimitArg,
    WorkspaceArg,
    join_notes,
    truncation_note,
)

# Rows returned when no maximum is given.
DEFAULT_MAX_ROWS = 100
# Largest maximum number of rows a query may ask for.
MAX_ROWS_LIMIT = 1_000
# Characters kept of a text value; longer ones are cut.
MAX_CELL_LENGTH = 500
# Characters of JSON rows returned at most; rows past it are dropped.
MAX_RESULT_LENGTH = 50_000
# Appended to a text value that was cut.
CUT_MARKER = "…[cut]"

TableArg = Annotated[
    str,
    Field(
        description="Name of the table, as given by openhexa_list_database_tables or "
        "openhexa_search."
    ),
]


class TableSummary(BaseModel):
    table: str = Field(description="Name of the table, which identifies it in other tools.")
    rows: int | None = Field(description="Number of rows, if known.")


class TableList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    tables: list[TableSummary] = Field(description="Tables of the workspace database, by name.")
    total_tables: int = Field(description="Number of tables in the database.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


class ColumnDetails(BaseModel):
    name: str = Field(description="Name of the column.")
    type: str = Field(description="PostgreSQL data type.")


class TableDetails(TableSummary):
    workspace: str = Field(description="Slug of the workspace.")
    columns: list[ColumnDetails] = Field(description="Columns of the table, in order.")


class QueryRows(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    columns: list[str] = Field(description="Names of the columns.")
    rows: list[list[JsonValue]] = Field(description="Rows, as values in column order.")
    row_count: int = Field(description="Number of rows returned.")
    truncated: bool = Field(description="Whether rows or values are missing.")
    note: str | None = Field(description="Explains what is missing, if truncated.")


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_list_database_tables",
        title="List tables of an OpenHEXA workspace database",
        annotations=READ_ONLY,
    )
    async def list_database_tables(
        workspace: WorkspaceArg, limit: ListLimitArg = DEFAULT_LIST_LIMIT
    ) -> TableList:
        """List the tables of an OpenHEXA workspace's PostgreSQL database, with row counts."""
        items = await access.run(workspace, lambda client: client.database.tables())
        return TableList(
            workspace=workspace,
            tables=[TableSummary(table=t.name, rows=t.rows) for t in items[:limit]],
            total_tables=len(items),
            note=truncation_note(len(items), limit, "tables"),
        )

    @server.tool(
        name="openhexa_get_database_table",
        title="Get a table of an OpenHEXA workspace database",
        annotations=READ_ONLY,
    )
    async def get_database_table(workspace: WorkspaceArg, table: TableArg) -> TableDetails:
        """Read the columns, PostgreSQL types and row count of an OpenHEXA database table."""
        item = await access.run(workspace, lambda client: client.database.table(table))
        return TableDetails(
            workspace=workspace,
            table=item.name,
            rows=item.rows,
            columns=[ColumnDetails(name=c.name, type=c.type) for c in item.columns or []],
        )

    @server.tool(
        name="openhexa_query_database",
        title="Query an OpenHEXA workspace database with SQL",
        annotations=READ_ONLY,
    )
    async def query_database(
        workspace: WorkspaceArg,
        sql: Annotated[
            str, Field(min_length=1, description="A single read-only PostgreSQL statement.")
        ],
        max_rows: Annotated[
            int,
            Field(ge=1, le=MAX_ROWS_LIMIT, description="Maximum number of rows to return."),
        ] = DEFAULT_MAX_ROWS,
    ) -> QueryRows:
        """Run a read-only SQL query on an OpenHEXA workspace's PostgreSQL database.

        Runs one statement with a read-only role and cancels it after 10 seconds. Returns
        at most max_rows rows and about 50,000 characters; text values longer than 500
        characters are cut. Rows are lists of values in column order; timestamps are ISO
        strings and numerics are strings, to keep their precision. truncated is true when
        rows or values are missing, and note says which. OpenHEXA records every query in
        its audit log under the user's name.
        """
        result = await access.run(
            workspace, lambda client: client.database.query(sql, max_rows=max_rows)
        )
        return _query_rows(workspace, result)


def _query_rows(workspace: str, result: QueryResult) -> QueryRows:
    """Fit `result` in the size budget, cutting long text values and then dropping rows."""
    cut_values = False
    rows: list[list[Any]] = []
    length = 0
    for row in result.rows:
        fitted = [_cut(value) for value in row]
        cut_values = cut_values or fitted != row
        length += len(json.dumps(fitted, ensure_ascii=False))
        if length > MAX_RESULT_LENGTH:
            break
        rows.append(fitted)
    dropped = len(rows) < len(result.rows)
    notes = []
    if dropped:
        notes.append(
            f"Showing {len(rows)} of the {len(result.rows)} rows returned: the result was too "
            "large. Selecting fewer columns or rows, or aggregating, returns the rest."
        )
    if result.truncated:
        notes.append(
            "The query has more rows than returned, so this result is incomplete. Aggregating "
            "in SQL or a higher max_rows returns more."
        )
    if cut_values:
        notes.append(
            f"Text values longer than {MAX_CELL_LENGTH} characters were cut and end with "
            f"'{CUT_MARKER}'. Selecting a substring reads the rest."
        )
    return QueryRows(
        workspace=workspace,
        columns=result.columns,
        rows=rows,
        row_count=len(rows),
        truncated=dropped or result.truncated or cut_values,
        note=join_notes(*notes),
    )


def _cut(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_CELL_LENGTH:
        return value[:MAX_CELL_LENGTH] + CUT_MARKER
    return value
