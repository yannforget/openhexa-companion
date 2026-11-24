"""SQL queries and saved queries, with a fake OpenHEXA and on the demo workspace."""

import uuid
from typing import Any

import httpx2
import pytest

from openhexa.companion import Client, InvalidInputError, NotFoundError, PermissionDeniedError


def _client(data: dict[str, Any]) -> Client:
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(200, json={"data": data}))
    return Client("https://openhexa.test", "ws", "token", http_transport=transport)


def _executed(**result: Any) -> dict[str, Any]:
    return {"workspace": {"database": {"executeSQL": result}}}


def test_query_returns_rows_in_column_order():
    client = _client(
        _executed(
            success=True,
            errors=[],
            errorMessage=None,
            columns=["district", "cases"],
            rows=[{"cases": 3, "district": "A"}],
            truncated=True,
            durationMs=4,
        )
    )

    result = client.database.query("select district, cases from cases", max_rows=1)

    assert result.columns == ["district", "cases"]
    assert result.rows == [["A", 3]]
    assert result.truncated


def test_query_refuses_duplicate_columns():
    client = _client(
        _executed(
            success=True,
            errors=[],
            errorMessage=None,
            columns=["id", "id"],
            rows=[{"id": 2}],
            truncated=False,
            durationMs=1,
        )
    )

    with pytest.raises(InvalidInputError, match="several columns named 'id'"):
        client.database.query("select 1 as id, 2 as id")


@pytest.mark.parametrize(
    ("code", "error"),
    [("QUERY_ERROR", InvalidInputError), ("PERMISSION_DENIED", PermissionDeniedError)],
)
def test_query_errors_keep_postgres_message(code: str, error: type[Exception]):
    client = _client(_executed(success=False, errors=[code], errorMessage="column x is bad"))

    with pytest.raises(error, match="PostgreSQL said: column x is bad"):
        client.database.query("select x")


def test_saved_query_of_another_workspace_is_not_found():
    query = {
        "slug": "cases",
        "name": "Cases",
        "description": None,
        "visibility": "WORKSPACE",
        "createdBy": None,
        "updatedAt": "2026-11-24T08:00:00+00:00",
        "content": "select 1",
        "workspace": {"slug": "other"},
    }
    page = {"savedQueries": {"totalPages": 1, "items": []}}
    client = _client({"savedQueryBySlug": query, "workspace": page})

    with pytest.raises(NotFoundError, match="'cases' not found in workspace 'ws'"):
        client.saved_queries.get("cases")


@pytest.mark.workspace
def test_query_demo_database(client: Client):
    result = client.database.query(
        "select n, n * 2 as doubled from generate_series(1, 5) as n", max_rows=3
    )

    assert result.columns == ["n", "doubled"]
    assert result.rows == [[1, 2], [2, 4], [3, 6]]
    assert result.truncated
    with pytest.raises(InvalidInputError, match="does not exist"):
        client.database.query("select no_such_column")


@pytest.mark.workspace
def test_save_and_read_query(client: Client, saved_query_slugs: list[str]):
    name = f"companion test {uuid.uuid4().hex[:8]}"
    created = client.saved_queries.create(name, "select 1 as one", description="Temporary")
    saved_query_slugs.append(created.slug)

    assert not created.shared
    assert client.saved_queries.get(created.slug) == created
    found = client.saved_queries.list(search=name)
    assert [query.slug for query in found] == [created.slug]
    assert client.database.query(created.sql).rows == [[1]]

    updated = client.saved_queries.update(created.slug, sql="select 2 as two", shared=True)
    assert (updated.slug, updated.name, updated.sql, updated.shared) == (
        created.slug,
        name,
        "select 2 as two",
        True,
    )
    assert client.saved_queries.delete(created.slug) == updated
    saved_query_slugs.remove(created.slug)
    with pytest.raises(NotFoundError):
        client.saved_queries.get(created.slug)
