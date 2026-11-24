"""The MCP connector through its HTTP interface: OAuth, the connect page and tools."""

import base64
import hashlib
import html
import io
import json
import re
import secrets
import uuid
import zipfile
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from mcp import Client as MCPClient
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult, TextContent

from openhexa.companion import Client
from openhexa.companion.server import gateway
from openhexa.companion.server.app import build_app
from openhexa.companion.server.config import Config
from openhexa.companion.server.store import Store

PUBLIC_URL = "https://companion.test"
# Small dataset of the demo workspace, read by the end-to-end test.
DEMO_DATASET = "demo-monthly-sales-parquet"
REDIRECT_URI = "https://client.test/callback"
# Tokens the fake OpenHEXA accepts: the user they belong to and the workspaces they open.
FAKE_TOKENS = {
    "alice-cases": ("alice", {"cases"}),
    "alice-stock": ("alice", {"stock"}),
    "bob-stock": ("bob", {"stock"}),
}


# The only dataset file of the fake OpenHEXA: `stats.csv` in dataset `stats` of `cases`.
FILE_CONTENT = b"district,cases\n" + b"".join(b"d%d,%d\n" % (i, i * 7) for i in range(500))
VERSION_ID = "6f1d2c1e-8c4b-4f6e-9a57-0d1f3b0b6a11"
STORAGE_URL = "https://storage.test/stats.csv"
# The only run of the fake OpenHEXA, of pipeline `etl` in `cases`, with a secret parameter.
RUN_ID = "0b3c0a52-3f4e-4d8c-9a0e-5c1b7e2d9f10"
PIPELINE_VERSION_ID = "9a7e6b2c-1d3f-4e5a-8b9c-0d1e2f3a4b5c"
RUN_LOGS = "".join(f"line {i}\n" for i in range(1000))
# Source of that version: too long to return at once, because of `data.txt`.
PIPELINE_SOURCE = {
    "pipeline.py": "from openhexa.sdk import pipeline\n\n\n@pipeline('etl')\ndef etl(): ...\n",
    "data.txt": "".join(f"row {i:05}\n" for i in range(6000)),
    "logo.png": b"\x89PNG\r\n\x1a\n\xff",
}


def _source_archive() -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in PIPELINE_SOURCE.items():
            archive.writestr(path, content)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def fake_openhexa(revoked: set[str]) -> httpx2.MockTransport:
    """An OpenHEXA instance that knows the users and workspaces of `FAKE_TOKENS`."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == STORAGE_URL:
            return _storage(request)
        token = request.headers["Authorization"].removeprefix("Bearer ")
        if token not in FAKE_TOKENS or token in revoked:
            return httpx2.Response(401)
        user, workspaces = FAKE_TOKENS[token]
        body = json.loads(request.content)
        if "me {" in body["query"]:
            return _data({"me": {"user": {"id": user, "email": f"{user}@example.org"}}})
        if "query Catalog" in body["query"]:
            return _catalog(body, workspaces)
        if "datasetLinkBySlug" in body["query"]:
            return _data({"datasetLinkBySlug": _dataset(body["variables"], workspaces)})
        if "pipelineRun(" in body["query"] or "pipelineVersion(" in body["query"]:
            return _data(_pipeline_object(body, workspaces))
        if "Pipeline(" in body["query"] or "pipelineByCode(" in body["query"]:
            return _data(_pipeline_change(body, workspaces))
        if "executeSQL(" in body["query"] or "createSavedQuery(" in body["query"]:
            return _data(_sql(body))
        if "updateWorkspace(" in body["query"]:
            slug, homepage = (
                body["variables"]["input"]["slug"],
                body["variables"]["input"]["description"],
            )
            return _data(
                {
                    "updateWorkspace": {
                        "success": True,
                        "errors": [],
                        "workspace": _workspace(slug, homepage),
                    }
                }
            )
        slug = body["variables"]["workspace"]
        return _data({"workspace": _workspace(slug, f"# {slug}") if slug in workspaces else None})

    return httpx2.MockTransport(handle)


def _workspace(slug: str, homepage: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": slug.title(),
        "description": homepage,
        "configuration": {"SECRET": "hidden"},
        "dockerImage": None,
    }


def _catalog(body: Mapping[str, Any], workspaces: set[str]) -> httpx2.Response:
    """List dataset `stats` and pipeline `etl` of `cases`; its database is unavailable."""
    if "database" in body["query"]:
        return httpx2.Response(200, json={"errors": [{"message": "database unavailable"}]})
    if "cases" not in workspaces:
        return _data({"workspace": None, "pipelines": {"items": []}, "webapps": {"items": []}})
    dataset = {
        "id": "dataset-id",
        "slug": "stats",
        "name": "District statistics",
        "description": "Monthly malaria cases by district.",
        "updatedAt": "2026-11-01T10:00:00+00:00",
        "workspace": {"slug": "cases"},
    }
    pipeline = {
        "id": "pipeline-id",
        "code": "etl",
        "name": "ETL",
        "description": "Loads cases from DHIS2.",
        "tags": [{"name": "malaria"}],
        "updatedAt": None,
    }
    return _data(
        {
            "workspace": {"slug": "cases", "datasets": {"items": [{"dataset": dataset}]}},
            "pipelines": {"items": [pipeline]},
            "webapps": {"items": []},
        }
    )


def _dataset(variables: Mapping[str, Any], workspaces: set[str]) -> dict[str, Any] | None:
    """The `stats` dataset with every field the library's dataset queries select."""
    if "cases" not in workspaces or (variables["owner"], variables["slug"]) != ("cases", "stats"):
        return None
    version: dict[str, Any] = {
        "id": VERSION_ID,
        "name": "v1",
        "changelog": None,
        "createdAt": "2026-11-01T10:00:00+00:00",
        "fileByName": None,
    }
    if variables.get("path") == "stats.csv":
        version["fileByName"] = {
            "id": "file-1",
            "uri": f"dataset-id/{VERSION_ID}/stats.csv",
            "size": len(FILE_CONTENT),
            "contentType": "text/csv",
            "downloadUrl": STORAGE_URL,
        }
    return {
        "dataset": {
            "id": "dataset-id",
            "slug": "stats",
            "workspace": {"slug": "cases"},
            "targetId": "dataset-id",
            "name": "District statistics",
            "description": None,
            "attributes": [],
            "latestVersion": version,
            "version": version if variables.get("version") == VERSION_ID else None,
        }
    }


def _pipeline_object(body: Mapping[str, Any], workspaces: set[str]) -> dict[str, Any]:
    """Answer the library's run and pipeline version queries with the run `RUN_ID`."""
    pipeline = {"code": "etl", "workspace": {"slug": "cases"}}
    if "pipelineVersion(" in body["query"]:
        found = "cases" in workspaces and body["variables"]["id"] == PIPELINE_VERSION_ID
        return {"pipelineVersion": _pipeline_version() if found else None}
    if "cases" not in workspaces or body["variables"]["id"] != RUN_ID:
        return {"pipelineRun": None}
    if "logs" in body["query"]:
        return {
            "pipelineRun": {
                "pipeline": pipeline,
                "logs": RUN_LOGS,
                "messages": [],
                "outputs": [],
                "datasetVersions": [
                    {"id": VERSION_ID, "name": "v1", "dataset": pipeline | {"slug": "stats"}}
                ],
            }
        }
    return {"pipelineRun": _run("failed", {"year": 2025, "api_key": "run-secret"})}


def _pipeline_change(body: Mapping[str, Any], workspaces: set[str]) -> dict[str, Any]:
    """Answer the pipeline lookup, start and stop that starting or stopping a run makes."""
    if "runPipeline(" in body["query"]:
        config = body["variables"]["input"]["config"]
        return {"runPipeline": {"success": True, "errors": [], "run": _run("queued", config)}}
    if "stopPipeline(" in body["query"]:
        return {"stopPipeline": {"success": True, "errors": []}}
    if "cases" not in workspaces or body["variables"]["code"] != "etl":
        return {"pipelineByCode": None}
    return {
        "pipelineByCode": {
            "id": "pipeline-id",
            "code": "etl",
            "name": "ETL",
            "description": "",
            "type": "zipFile",
            "schedule": None,
            "currentVersion": _pipeline_version(),
            "versions": {"totalPages": 1, "items": [_pipeline_version()]},
        }
    }


def _pipeline_version() -> dict[str, Any]:
    """The only version of pipeline `etl`, with a secret parameter `api_key`."""
    return {
        "id": PIPELINE_VERSION_ID,
        "name": None,
        "versionNumber": 1,
        "description": None,
        "createdAt": "2026-11-01T10:00:00+00:00",
        "timeout": None,
        "config": {"api_key": "configured-secret"},
        "zipfile": _source_archive(),
        "parameters": [
            {
                "code": code,
                "name": code,
                "type": kind,
                "required": True,
                "multiple": False,
                "default": None,
                "choices": None,
                "help": None,
            }
            for code, kind in (("year", "int"), ("api_key", "secret"))
        ],
        "pipeline": {"code": "etl", "workspace": {"slug": "cases"}},
    }


def _run(status: str, config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": RUN_ID,
        "status": status,
        "executionDate": "2026-11-02T10:00:00+00:00",
        "duration": 12,
        "triggerMode": "manual",
        "progress": 100,
        "config": config,
        "user": None,
        "timeout": 3600,
        "stoppedBy": None,
        "version": {"id": PIPELINE_VERSION_ID, "name": None, "versionNumber": 1},
        "pipeline": {"code": "etl", "workspace": {"slug": "cases"}},
    }


def _sql(body: Mapping[str, Any]) -> dict[str, Any]:
    """Answer a query with `maxRows` rows of 200 characters, the first one of 600."""
    if "createSavedQuery(" in body["query"]:
        query = body["variables"]["input"]
        saved = {
            "slug": "cases",
            "name": query["name"],
            "description": query["description"],
            "visibility": query["visibility"],
            "createdBy": {"displayName": "Alice"},
            "updatedAt": "2026-11-24T08:00:00+00:00",
            "content": query["content"],
        }
        return {"createSavedQuery": {"success": True, "errors": [], "savedQuery": saved}}
    rows = [
        {"n": i, "label": "x" * (600 if i == 0 else 200)}
        for i in range(body["variables"]["maxRows"])
    ]
    result = {
        "success": True,
        "errors": [],
        "errorMessage": None,
        "columns": ["n", "label"],
        "rows": rows,
        "truncated": True,
        "durationMs": 3,
    }
    return {"workspace": {"database": {"executeSQL": result}}}


def _storage(request: httpx2.Request) -> httpx2.Response:
    """Serve `FILE_CONTENT`, honouring `bytes=start-end` ranges like cloud storage.

    The body is an iterator so that it is streamed like a real response, not pre-read.
    """
    if "Range" not in request.headers:
        return httpx2.Response(200, content=iter([FILE_CONTENT]))
    start, end = request.headers["Range"].removeprefix("bytes=").split("-")
    content = FILE_CONTENT[int(start) : int(end) + 1 if end else None]
    return httpx2.Response(206, content=iter([content]))


def _data(data: Mapping[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, json={"data": data})


@dataclass
class Connector:
    """A running companion and an HTTP client for it."""

    http: httpx2.AsyncClient
    app: Any
    revoked: set[str]


@asynccontextmanager
async def running_connector(
    openhexa_url: str = "https://openhexa.test", transport: httpx2.BaseTransport | None = None
) -> AsyncGenerator[Connector]:
    revoked: set[str] = set()
    config = Config(
        public_url=PUBLIC_URL,
        openhexa_url=openhexa_url,
        secret_key="s" * 32,
        database=":memory:",
    )
    store = Store(config.database, config.fernet_key())
    app = build_app(config, store, transport or fake_openhexa(revoked))
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app), base_url=PUBLIC_URL) as http,
    ):
        yield Connector(http, app, revoked)
    store.close()


@dataclass
class Registration:
    client_id: str
    client_secret: str


async def register(http: httpx2.AsyncClient) -> Registration:
    response = await http.post(
        "/register", json={"redirect_uris": [REDIRECT_URI], "client_name": "Test Client"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return Registration(body["client_id"], body["client_secret"])


async def authorize(http: httpx2.AsyncClient, client: Registration) -> tuple[str, str]:
    """Start authorization and return the connect page URL and the PKCE verifier."""
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    response = await http.get(
        "/authorize",
        params={
            "client_id": client.client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge.decode().rstrip("="),
            "code_challenge_method": "S256",
            "state": "xyz",
            "resource": f"{PUBLIC_URL}/mcp",
        },
    )
    assert response.status_code == 302, response.text
    return response.headers["location"], verifier


async def submit_connect(
    http: httpx2.AsyncClient, connect_url: str, workspaces: list[tuple[str, str]]
) -> httpx2.Response:
    """Load the connect page and submit it with `(slug, token)` rows."""
    page = await http.get(connect_url)
    assert page.status_code == 200, page.text
    fields = dict(re.findall(r'name="(request|csrf)" value="([^"]*)"', page.text))
    form = [
        ("request", fields["request"]),
        ("csrf", fields["csrf"]),
        ("action", "connect"),
        *[pair for slug, token in workspaces for pair in (("slug", slug), ("token", token))],
    ]
    return await http.post(
        "/connect",
        content=urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


async def exchange(
    http: httpx2.AsyncClient, client: Registration, code: str, verifier: str
) -> httpx2.Response:
    return await http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        },
    )


async def connect(
    http: httpx2.AsyncClient, workspaces: list[tuple[str, str]]
) -> tuple[Registration, dict[str, Any]]:
    """Run the whole authorization flow and return the client and its tokens."""
    client = await register(http)
    connect_url, verifier = await authorize(http, client)
    response = await submit_connect(http, connect_url, workspaces)
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    metadata = (await http.get("/.well-known/oauth-authorization-server")).json()
    assert query["iss"] == [metadata["issuer"]]
    assert query["state"] == ["xyz"]
    code = query["code"][0]
    tokens = await exchange(http, client, code, verifier)
    assert tokens.status_code == 200, tokens.text
    return client, tokens.json()


@asynccontextmanager
async def mcp_session(connector: Connector, access_token: str) -> AsyncGenerator[MCPClient]:
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(connector.app),
        headers={"Authorization": f"Bearer {access_token}"},
    )
    async with (
        http,
        MCPClient(streamable_http_client(f"{PUBLIC_URL}/mcp", http_client=http)) as mcp,
    ):
        yield mcp


async def mcp_status(connector: Connector, access_token: str) -> int:
    """Status of an MCP request made with `access_token`."""
    response = await connector.http.post(
        "/mcp",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    return response.status_code


def text(result: CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


@pytest.mark.anyio
async def test_connect_and_call_tools():
    async with running_connector() as connector:
        client, tokens = await connect(
            connector.http, [("cases", "alice-cases"), ("stock", "alice-stock")]
        )

        async with mcp_session(connector, tokens["access_token"]) as mcp:
            names = [tool.name for tool in (await mcp.list_tools()).tools]
            listed = await mcp.call_tool("openhexa_list_workspaces", {})
            workspace = await mcp.call_tool("openhexa_get_workspace", {"workspace": "stock"})

        assert names[:2] == ["openhexa_list_workspaces", "openhexa_get_workspace"]
        assert [ws["slug"] for ws in listed.structured_content["workspaces"]] == ["cases", "stock"]
        assert workspace.structured_content["homepage"] == "# stock"
        assert "SECRET" not in json.dumps(workspace.structured_content)

        refreshed = await connector.http.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client.client_id,
                "client_secret": client.client_secret,
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        assert await mcp_status(connector, tokens["access_token"]) == 401
        assert await mcp_status(connector, refreshed.json()["access_token"]) == 200

        await connector.http.post(
            "/revoke",
            data={
                "token": refreshed.json()["refresh_token"],
                "client_id": client.client_id,
                "client_secret": client.client_secret,
            },
        )
        assert await mcp_status(connector, refreshed.json()["access_token"]) == 401


@pytest.mark.anyio
async def test_reused_code_revokes_the_grant():
    async with running_connector() as connector:
        client = await register(connector.http)
        connect_url, verifier = await authorize(connector.http, client)
        response = await submit_connect(connector.http, connect_url, [("cases", "alice-cases")])
        code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]

        first = await exchange(connector.http, client, code, verifier)
        second = await exchange(connector.http, client, code, verifier)

        assert first.status_code == 200
        assert second.json()["error"] == "invalid_grant"
        assert await mcp_status(connector, first.json()["access_token"]) == 401


@pytest.mark.anyio
async def test_wrong_pkce_verifier_is_rejected():
    async with running_connector() as connector:
        client = await register(connector.http)
        connect_url, _verifier = await authorize(connector.http, client)
        response = await submit_connect(connector.http, connect_url, [("cases", "alice-cases")])
        code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]

        result = await exchange(connector.http, client, code, secrets.token_urlsafe(32))

        assert result.json()["error"] == "invalid_grant"


@pytest.mark.anyio
async def test_connect_page_rejects_bad_submissions():
    async with running_connector() as connector:
        client = await register(connector.http)
        connect_url, _verifier = await authorize(connector.http, client)
        request_id = parse_qs(urlsplit(connect_url).query)["request"][0]

        unknown = await connector.http.get("/connect?request=unknown")
        forged = await connector.http.post(
            "/connect",
            data={"request": request_id, "csrf": "1.forged", "slug": "cases", "token": "x"},
        )
        mixed = await submit_connect(
            connector.http, connect_url, [("cases", "alice-cases"), ("stock", "bob-stock")]
        )
        wrong = await submit_connect(connector.http, connect_url, [("stock", "alice-cases")])

        assert unknown.status_code == 404
        assert forged.status_code == 400
        assert "belongs to another OpenHEXA user" in mixed.text
        assert "Workspace 'stock' not found" in html.unescape(wrong.text)
        assert "alice-cases" not in mixed.text + wrong.text
        assert unknown.headers["X-Frame-Options"] == "DENY"
        assert "script-src 'self';" in unknown.headers["Content-Security-Policy"]


@pytest.mark.anyio
async def test_pages_serve_their_static_files():
    async with running_connector() as connector:
        page = await connector.http.get("/connect?request=unknown")
        paths = re.findall(r'(?:href|src)="(/static/[^"?]+)', page.text)
        responses = [await connector.http.get(path) for path in paths]

    assert sorted(paths) == [
        "/static/favicon.png",
        "/static/logo.svg",
        "/static/pages.js",
        "/static/style.css",
    ]
    assert all(response.status_code == 200 for response in responses)


@pytest.mark.anyio
async def test_tool_errors_explain_what_to_do():
    async with running_connector() as connector:
        _client, tokens = await connect(connector.http, [("cases", "alice-cases")])
        connector.revoked.add("alice-cases")

        async with mcp_session(connector, tokens["access_token"]) as mcp:
            unknown = await mcp.call_tool("openhexa_get_workspace", {"workspace": "stock"})
            rejected = await mcp.call_tool("openhexa_get_workspace", {"workspace": "cases"})
            listed = await mcp.call_tool("openhexa_list_workspaces", {})

        assert unknown.is_error
        assert "Connected workspaces: 'cases'" in text(unknown)
        assert rejected.is_error
        assert f"{PUBLIC_URL}/account" in text(rejected)
        assert listed.structured_content["workspaces"][0]["token_rejected"] is True


@pytest.mark.anyio
async def test_file_urls_stream_files_until_revoked(monkeypatch: pytest.MonkeyPatch):
    async with running_connector() as connector:
        client, tokens = await connect(connector.http, [("cases", "alice-cases")])
        async with mcp_session(connector, tokens["access_token"]) as mcp:
            result = await mcp.call_tool(
                "openhexa_get_dataset_file_url",
                {"workspace": "cases", "dataset": "stats", "path": "stats.csv"},
            )
            monkeypatch.setattr(gateway, "FILE_URL_LIFETIME", -1)
            expired = await mcp.call_tool(
                "openhexa_get_dataset_file_url",
                {"workspace": "cases", "dataset": "stats", "path": "stats.csv"},
            )
        info = result.structured_content
        url = info["url"]
        token = urlsplit(url).path.split("/")[2]
        tampered = url.replace(token, token[:-2] + ("AA" if token[-2:] != "AA" else "BB"))

        whole = await connector.http.get(url)
        part = await connector.http.get(url, headers={"Range": "bytes=10-19"})
        tail = await connector.http.get(url, headers={"Range": "bytes=-5"})

        assert info["version_id"] == VERSION_ID
        assert "alice-cases" not in url
        assert whole.content == FILE_CONTENT
        assert whole.headers["Content-Disposition"].startswith('attachment; filename="stats.csv"')
        assert (part.status_code, part.content) == (206, FILE_CONTENT[10:20])
        assert part.headers["Content-Range"] == f"bytes 10-19/{len(FILE_CONTENT)}"
        assert tail.content == FILE_CONTENT[-5:]
        assert (await connector.http.get(tampered)).status_code == 404
        assert (await connector.http.get(expired.structured_content["url"])).status_code == 404

        await connector.http.post(
            "/revoke",
            data={
                "token": tokens["refresh_token"],
                "client_id": client.client_id,
                "client_secret": client.client_secret,
            },
        )
        assert (await connector.http.get(url)).status_code == 404


@pytest.mark.anyio
async def test_runs_hide_secrets_and_page_logs():
    async with running_connector() as connector:
        _client, tokens = await connect(connector.http, [("cases", "alice-cases")])
        async with mcp_session(connector, tokens["access_token"]) as mcp:
            run = await mcp.call_tool(
                "openhexa_get_pipeline_run", {"workspace": "cases", "run": RUN_ID}
            )
            tail = await mcp.call_tool(
                "openhexa_get_pipeline_run_logs",
                {"workspace": "cases", "run": RUN_ID, "length": 100},
            )
            start = tail.structured_content["offset"] - 100
            earlier = await mcp.call_tool(
                "openhexa_get_pipeline_run_logs",
                {"workspace": "cases", "run": RUN_ID, "offset": start, "length": 100},
            )

        info = run.structured_content
        assert info["timeout"] == 3600
        assert info["parameters"] == {"year": 2025, "api_key": "<secret>"}
        assert "run-secret" not in text(run)
        assert info["published_dataset_versions"][0]["version_id"] == VERSION_ID
        assert tail.structured_content["logs"] == RUN_LOGS[-100:]
        assert f"offset={start}" in tail.structured_content["note"]
        assert earlier.structured_content["logs"] == RUN_LOGS[start : start + 100]


@pytest.mark.anyio
async def test_pipeline_source_is_read_by_file():
    args = {"workspace": "cases", "pipeline": "etl", "version": PIPELINE_VERSION_ID}
    async with running_connector() as connector:
        _client, tokens = await connect(connector.http, [("cases", "alice-cases")])
        async with mcp_session(connector, tokens["access_token"]) as mcp:
            listed = await mcp.call_tool("openhexa_get_pipeline_source", args)
            code = await mcp.call_tool(
                "openhexa_get_pipeline_source", {**args, "path": "pipeline.py"}
            )
            missing = await mcp.call_tool(
                "openhexa_get_pipeline_source", {**args, "path": "main.py"}
            )

    assert listed.structured_content["version"] == "v1"
    assert [f["content"] for f in listed.structured_content["files"]] == [None, None, None]
    assert "path" in listed.structured_content["note"]
    [source] = code.structured_content["files"]
    assert source["content"] == PIPELINE_SOURCE["pipeline.py"]
    assert missing.is_error
    assert "'pipeline.py'" in text(missing)


async def account_form(http: httpx2.AsyncClient, *fields: tuple[str, str]) -> httpx2.Response:
    """Load the account page and submit a form of it with `fields` and its CSRF token."""
    page = await http.get("/account")
    csrf = re.search(r'name="csrf" value="([^"]*)"', page.text)
    assert csrf, page.text
    return await http.post(
        "/account",
        content=urlencode([("csrf", csrf[1]), *fields]),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


async def access_state(http: httpx2.AsyncClient) -> tuple[list[str], dict[str, bool]]:
    """The grant IDs of the account page, newest first, and its access checkboxes."""
    page = await http.get("/account")
    grants = list(dict.fromkeys(re.findall(r'name="grant" value="([^"]*)"', page.text)))
    boxes = re.findall(r'name="access" value="([^"]*)"[^>]*?( checked)?>', page.text)
    return grants, {value: bool(checked) for value, checked in boxes}


@pytest.mark.anyio
async def test_account_page_manages_access():
    async with running_connector() as connector:
        _client, tokens = await connect(connector.http, [("cases", "alice-cases")])
        _other, other_tokens = await connect(connector.http, [("cases", "alice-cases")])
        http = connector.http

        forged = await http.post("/account", data={"csrf": "1.forged", "action": "logout"})
        signed_out = await account_form(http, ("action", "forget"), ("slug", "cases"))
        await account_form(http, ("action", "enter"), ("slug", "stock"), ("token", "alice-stock"))
        (newest, oldest), boxes = await access_state(http)
        # Give the oldest client `stock` instead of `cases`.
        await account_form(
            http,
            ("action", "save"),
            *[("grant", grant) for grant in (newest, oldest)],
            *[("slug", slug) for slug in ("cases", "stock")],
            ("access", f"{newest}:cases"),
            ("access", f"{oldest}:stock"),
        )
        async with mcp_session(connector, tokens["access_token"]) as mcp:
            listed = await mcp.call_tool("openhexa_list_workspaces", {})
        await account_form(http, ("action", "forget"), ("slug", "stock"))
        await account_form(http, ("action", "disconnect"), ("grant", newest))
        async with mcp_session(connector, tokens["access_token"]) as mcp:
            forgotten = await mcp.call_tool("openhexa_list_workspaces", {})

        # Bob's token signs the browser in as Bob, who cannot touch Alice's grants.
        await account_form(http, ("action", "enter"), ("slug", "stock"), ("token", "bob-stock"))
        bob_page = await http.get("/account")
        await account_form(http, ("action", "disconnect"), ("grant", oldest))
        await account_form(http, ("action", "logout"))

        assert forged.status_code == 400
        assert signed_out.status_code == 400
        assert boxes == {
            f"{newest}:cases": True,
            f"{newest}:stock": False,
            f"{oldest}:cases": True,
            f"{oldest}:stock": False,
        }
        assert [ws["slug"] for ws in listed.structured_content["workspaces"]] == ["stock"]
        assert forgotten.structured_content["workspaces"] == []
        assert await mcp_status(connector, other_tokens["access_token"]) == 401
        assert "bob@example.org" in bob_page.text
        assert oldest not in bob_page.text
        assert await mcp_status(connector, tokens["access_token"]) == 200
        assert "Signed in" not in (await http.get("/account")).text
        assert "alice-stock" not in bob_page.text
        assert bob_page.headers["X-Frame-Options"] == "DENY"


@pytest.mark.anyio
async def test_write_tools():
    async with running_connector() as connector:
        _client, tokens = await connect(connector.http, [("cases", "alice-cases")])
        async with mcp_session(connector, tokens["access_token"]) as mcp:
            homepage = await mcp.call_tool(
                "openhexa_update_workspace_homepage",
                {"workspace": "cases", "homepage": "# New homepage"},
            )
            started = await mcp.call_tool(
                "openhexa_start_pipeline_run",
                {
                    "workspace": "cases",
                    "pipeline": "etl",
                    "version": "v1",
                    "parameters": {"year": 2026, "api_key": "typed-secret"},
                },
            )
            unknown = await mcp.call_tool(
                "openhexa_start_pipeline_run",
                {"workspace": "cases", "pipeline": "etl", "parameters": {"month": 1}},
            )
            stopped = await mcp.call_tool(
                "openhexa_stop_pipeline_run", {"workspace": "cases", "run": RUN_ID}
            )

        assert homepage.structured_content["homepage"] == "# New homepage"
        assert started.structured_content["run_id"] == RUN_ID
        assert started.structured_content["status"] == "queued"
        assert started.structured_content["parameters"] == {"year": 2026, "api_key": "<secret>"}
        assert "typed-secret" not in text(started)
        assert unknown.is_error
        assert "has no parameter 'month'" in text(unknown)
        assert not stopped.is_error, text(stopped)
        assert "run-secret" not in text(stopped)


@pytest.mark.anyio
async def test_sql_tools():
    async with running_connector() as connector:
        _client, tokens = await connect(connector.http, [("cases", "alice-cases")])
        async with mcp_session(connector, tokens["access_token"]) as mcp:
            few = await mcp.call_tool(
                "openhexa_query_database", {"workspace": "cases", "sql": "select", "max_rows": 2}
            )
            many = await mcp.call_tool(
                "openhexa_query_database",
                {"workspace": "cases", "sql": "select", "max_rows": 1000},
            )
            saved = await mcp.call_tool(
                "openhexa_create_saved_query",
                {"workspace": "cases", "name": "Cases", "sql": "select 1"},
            )

        assert few.structured_content["rows"][0][1].endswith("…[cut]")
        assert len(few.structured_content["rows"][0][1]) < 600
        assert "do not present this result as complete" in few.structured_content["note"]
        assert many.structured_content["truncated"]
        assert 0 < many.structured_content["row_count"] < 1000
        assert "the result was too large" in many.structured_content["note"]
        assert saved.structured_content["sql"] == "select 1"
        assert not saved.structured_content["shared"]


@pytest.mark.anyio
async def test_search_finds_items_for_the_read_tools():
    async with running_connector() as connector:
        _client, tokens = await connect(connector.http, [("cases", "alice-cases")])
        async with mcp_session(connector, tokens["access_token"]) as mcp:
            found = await mcp.call_tool(
                "openhexa_search", {"workspace": "cases", "query": "malaria district", "limit": 1}
            )
            hits = found.structured_content["hits"]
            dataset = await mcp.call_tool(
                "openhexa_get_dataset", {"workspace": "cases", "dataset": hits[0]["reference"]}
            )

        assert not found.is_error, text(found)
        assert hits[0]["type"] == "dataset"
        assert hits[0]["extract"] == "Monthly **malaria** cases by **district**."
        assert found.structured_content["matches"] == {
            "dataset": 1,
            "pipeline": 1,
            "webapp": 0,
            "table": 0,
        }
        assert "Could not search tables" in found.structured_content["note"]
        assert "Showing 1 of 2 matches" in found.structured_content["note"]
        assert not dataset.is_error, text(dataset)


@pytest.mark.workspace
@pytest.mark.anyio
async def test_end_to_end_with_demo_workspace(demo_env: dict[str, str]):
    env = demo_env
    async with running_connector(env["OPENHEXA_URL"], httpx2.HTTPTransport()) as connector:
        _client, tokens = await connect(
            connector.http, [(env["OPENHEXA_WORKSPACE"], env["OPENHEXA_TOKEN"])]
        )

        async with mcp_session(connector, tokens["access_token"]) as mcp:
            listed = await mcp.call_tool("openhexa_list_workspaces", {})
            workspace = await mcp.call_tool(
                "openhexa_get_workspace", {"workspace": env["OPENHEXA_WORKSPACE"]}
            )
            datasets = await mcp.call_tool(
                "openhexa_list_datasets", {"workspace": env["OPENHEXA_WORKSPACE"]}
            )
            files = await mcp.call_tool(
                "openhexa_list_dataset_files",
                {"workspace": env["OPENHEXA_WORKSPACE"], "dataset": DEMO_DATASET},
            )
            path = files.structured_content["files"][0]["path"]
            file_url = await mcp.call_tool(
                "openhexa_get_dataset_file_url",
                {"workspace": env["OPENHEXA_WORKSPACE"], "dataset": DEMO_DATASET, "path": path},
            )
            reads = await _call_remaining_reads(mcp, env["OPENHEXA_WORKSPACE"])
        url = file_url.structured_content["url"]
        whole = await connector.http.get(url)
        part = await connector.http.get(url, headers={"Range": "bytes=100-199"})

    with Client(env["OPENHEXA_URL"], env["OPENHEXA_WORKSPACE"], env["OPENHEXA_TOKEN"]) as library:
        file = library.datasets.file(library.datasets.version(DEMO_DATASET), path)
        with library.datasets.read(file) as stream:
            expected = stream.read()
    assert whole.content == expected
    assert part.content == expected[100:200]
    assert DEMO_DATASET in {d["dataset"] for d in datasets.structured_content["datasets"]}

    assert [ws["slug"] for ws in listed.structured_content["workspaces"]] == [
        env["OPENHEXA_WORKSPACE"]
    ]
    assert not workspace.is_error, text(workspace)
    assert workspace.structured_content["workspace"] == env["OPENHEXA_WORKSPACE"]
    for result in reads:
        assert not result.is_error, text(result)


@pytest.mark.workspace
@pytest.mark.anyio
async def test_dataset_write_tools_with_demo_workspace(demo_env: dict[str, str]):
    env = demo_env
    workspace = env["OPENHEXA_WORKSPACE"]
    with Client(env["OPENHEXA_URL"], workspace, env["OPENHEXA_TOKEN"]) as library:
        dataset = library.datasets.create(f"companion test {uuid.uuid4().hex[:8]}", "Temporary")
        try:
            async with running_connector(env["OPENHEXA_URL"], httpx2.HTTPTransport()) as connector:
                _client, tokens = await connect(
                    connector.http, [(workspace, env["OPENHEXA_TOKEN"])]
                )
                async with mcp_session(connector, tokens["access_token"]) as mcp:
                    args = {"workspace": workspace, "dataset": dataset.slug}
                    updated = await mcp.call_tool(
                        "openhexa_update_dataset",
                        {**args, "name": f"{dataset.name} renamed", "description": "# Docs"},
                    )
                    await mcp.call_tool(
                        "openhexa_set_dataset_attribute", {**args, "key": "a", "value": [1, 2]}
                    )
                    set_ = await mcp.call_tool(
                        "openhexa_set_dataset_attribute", {**args, "key": "b", "value": "x"}
                    )
                    deleted = await mcp.call_tool(
                        "openhexa_delete_dataset_attribute", {**args, "key": "a"}
                    )
        finally:
            library.datasets.delete(dataset.slug)

    assert not updated.is_error, text(updated)
    assert updated.structured_content["name"] == f"{dataset.name} renamed"
    assert updated.structured_content["description"] == "# Docs"
    assert set_.structured_content["attributes"] == {"a": [1, 2], "b": "x"}
    assert deleted.structured_content["attributes"] == {"b": "x"}


async def _call_remaining_reads(mcp: MCPClient, workspace: str) -> list[CallToolResult]:
    """Call the remaining read tools on whatever the workspace holds."""
    results = [
        await mcp.call_tool("openhexa_search", {"workspace": workspace, "query": "data"}),
        await mcp.call_tool("openhexa_list_database_tables", {"workspace": workspace}),
        await mcp.call_tool("openhexa_query_database", {"workspace": workspace, "sql": "select 1"}),
        await mcp.call_tool("openhexa_list_saved_queries", {"workspace": workspace}),
        webapps := await mcp.call_tool("openhexa_list_webapps", {"workspace": workspace}),
        pipelines := await mcp.call_tool("openhexa_list_pipelines", {"workspace": workspace}),
    ]
    for webapp in webapps.structured_content["webapps"][:1]:
        args = {"workspace": workspace, "webapp": webapp["slug"]}
        results.append(await mcp.call_tool("openhexa_get_webapp", args))
    for pipeline in pipelines.structured_content["pipelines"]:
        args = {"workspace": workspace, "pipeline": pipeline["code"]}
        results.append(await mcp.call_tool("openhexa_get_pipeline", args))
        runs = await mcp.call_tool("openhexa_list_pipeline_runs", {**args, "limit": 1})
        results.append(runs)
        if runs.structured_content["runs"]:
            run = {"workspace": workspace, "run": runs.structured_content["runs"][0]["run_id"]}
            results.append(await mcp.call_tool("openhexa_get_pipeline_run", run))
            results.append(await mcp.call_tool("openhexa_get_pipeline_run_logs", run))
            source = await mcp.call_tool("openhexa_get_pipeline_source", args)
            assert source.structured_content["files"][0]["content"], text(source)
            results.append(source)
            break
    return results
