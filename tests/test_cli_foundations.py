"""Behaviour shared by every `hexa` command: exit codes, JSON output and errors, lists."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from openhexa.companion import Client
from openhexa.companion.cli import main
from openhexa.companion.cli._context import LazyClient

# Answers a GraphQL request body with the `data` of the response, or an HTTP response.
Handler = Callable[[dict[str, Any]], dict[str, Any] | httpx2.Response]


@pytest.fixture
def fake_openhexa(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], list[str]]:
    """Make the CLI talk to a fake OpenHEXA; return the queries it receives."""
    monkeypatch.delenv("HEXA_JSON", raising=False)

    def install(handler: Handler) -> list[str]:
        queries: list[str] = []

        def respond(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content)
            queries.append(body["query"])
            answer = handler(body)
            if isinstance(answer, httpx2.Response):
                return answer
            return httpx2.Response(200, json={"data": answer})

        transport = httpx2.MockTransport(respond)
        client = Client("https://openhexa.test", "ws", "token", http_transport=transport)
        monkeypatch.setattr(LazyClient, "get", lambda _self: client)
        return queries

    return install


def _invoke(*args: str, env: dict[str, str] | None = None) -> Result:
    return CliRunner().invoke(main, list(args), env=env)


def _missing_connection(body: dict[str, Any]) -> dict[str, Any]:
    if "connectionBySlug" in body["query"]:
        return {"connectionBySlug": None}
    return {"workspace": {"connections": []}}


def _homepage_refused(_body: dict[str, Any]) -> dict[str, Any]:
    return {"updateWorkspace": {"success": False, "errors": ["PERMISSION_DENIED"]}}


@pytest.mark.parametrize(
    ("args", "handler", "error_type", "exit_code"),
    [
        (["connections", "show", "dhis2"], _missing_connection, "NotFoundError", 3),
        (
            ["workspace", "homepage", "--edit", "-"],
            _homepage_refused,
            "PermissionDeniedError",
            4,
        ),
        (["datasets", "list"], lambda _body: httpx2.Response(401), "AuthenticationError", 4),
        (["datasets", "list"], lambda _body: httpx2.Response(502), "APIError", 1),
        (["datasets", "list", "--bogus"], lambda _body: {}, "UsageError", 2),
    ],
)
def test_errors_have_an_exit_code_and_json(
    fake_openhexa: Callable[[Handler], list[str]],
    args: list[str],
    handler: Handler,
    error_type: str,
    exit_code: int,
):
    fake_openhexa(handler)

    as_text = CliRunner().invoke(main, args, input="# Home\n")
    as_json = CliRunner().invoke(main, [*args, "--json"], input="# Home\n")

    assert as_text.exit_code == exit_code, as_text.output
    assert "Error: " in as_text.stderr
    assert as_json.exit_code == exit_code, as_json.output
    assert as_json.stdout == ""
    error = json.loads(as_json.stderr)["error"]
    assert error["type"] == error_type
    assert error["message"]


def test_hexa_json_turns_json_on(fake_openhexa: Callable[[Handler], list[str]]):
    fake_openhexa(_missing_connection)

    listed = _invoke("connections", "list", env={"HEXA_JSON": "1"})
    failed = _invoke("connections", "show", "dhis2", env={"HEXA_JSON": "1"})

    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout) == []
    assert "No connections in workspace ws" in listed.stderr
    assert json.loads(failed.stderr)["error"]["type"] == "NotFoundError"


def _dataset(slug: str) -> dict[str, Any]:
    return {
        "id": f"id-{slug}",
        "slug": slug,
        "name": slug.title(),
        "description": "",
        "workspace": {"slug": "ws"},
        "latestVersion": None,
        "attributes": [],
    }


def test_a_cut_list_says_so(fake_openhexa: Callable[[Handler], list[str]]):
    slugs = ["cases", "deaths", "population"]

    def datasets(body: dict[str, Any]) -> dict[str, Any]:
        per_page = body["variables"]["perPage"]
        items = [{"dataset": _dataset(slug)} for slug in slugs[:per_page]]
        return {"workspace": {"datasets": {"totalPages": 1, "items": items}}}

    fake_openhexa(datasets)

    cut = _invoke("datasets", "list", "-n", "2", "--json")
    complete = _invoke("datasets", "list", "--json")

    assert [item["slug"] for item in json.loads(cut.stdout)] == ["cases", "deaths"]
    assert "use -n" in cut.stderr
    assert len(json.loads(complete.stdout)) == 3
    assert complete.stderr == ""


def test_upload_refuses_to_replace_workspace_files(
    fake_openhexa: Callable[[Handler], list[str]], tmp_path: Path
):
    (tmp_path / "new.csv").write_text("a\n")
    (tmp_path / "cases.csv").write_text("a\n")

    def bucket(_body: dict[str, Any]) -> dict[str, Any]:
        item = {"key": "data/cases.csv", "type": "FILE", "size": 2, "updatedAt": None}
        objects = {"hasNextPage": False, "items": [item]}
        return {"workspace": {"bucket": {"objects": objects}}}

    queries = fake_openhexa(bucket)

    result = _invoke(
        "files", "upload", str(tmp_path / "new.csv"), str(tmp_path / "cases.csv"), "--to", "data"
    )

    assert result.exit_code == 1
    assert "data/cases.csv already exist" in result.stderr
    assert "--force" in result.stderr
    assert not any("prepareObjectUpload" in query for query in queries)
