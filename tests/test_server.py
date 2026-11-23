"""The MCP connector through its HTTP interface: OAuth, the connect page and tools."""

import base64
import hashlib
import json
import re
import secrets
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
        if "datasetLinkBySlug" in body["query"]:
            return _data({"datasetLinkBySlug": _dataset(body["variables"], workspaces)})
        if "pipelineRun(" in body["query"] or "pipelineVersion(" in body["query"]:
            return _data(_pipeline_object(body, workspaces))
        slug = body["variables"]["workspace"]
        workspace = None
        if slug in workspaces:
            workspace = {
                "slug": slug,
                "name": slug.title(),
                "description": f"# {slug}",
                "configuration": {"SECRET": "hidden"},
                "dockerImage": None,
            }
        return _data({"workspace": workspace})

    return httpx2.MockTransport(handle)


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
            "latestVersion": version,
            "version": version if variables.get("version") == VERSION_ID else None,
        }
    }


def _pipeline_object(body: Mapping[str, Any], workspaces: set[str]) -> dict[str, Any]:
    """Answer the library's run and pipeline version queries with the run `RUN_ID`."""
    pipeline = {"code": "etl", "workspace": {"slug": "cases"}}
    if "pipelineVersion(" in body["query"]:
        found = "cases" in workspaces and body["variables"]["id"] == PIPELINE_VERSION_ID
        version = {
            "id": PIPELINE_VERSION_ID,
            "name": None,
            "versionNumber": 1,
            "description": None,
            "createdAt": "2026-11-01T10:00:00+00:00",
            "timeout": None,
            "config": {"api_key": "configured-secret"},
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
            "pipeline": pipeline,
        }
        return {"pipelineVersion": version if found else None}
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
    return {
        "pipelineRun": {
            "id": RUN_ID,
            "status": "failed",
            "executionDate": "2026-11-02T10:00:00+00:00",
            "duration": 12,
            "triggerMode": "manual",
            "progress": 100,
            "config": {"year": 2025, "api_key": "run-secret"},
            "user": None,
            "version": {"id": PIPELINE_VERSION_ID, "name": None, "versionNumber": 1},
            "pipeline": pipeline,
        }
    }


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
        assert "Workspace &#x27;stock&#x27; not found" in wrong.text
        assert "alice-cases" not in mixed.text + wrong.text
        assert unknown.headers["X-Frame-Options"] == "DENY"


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
        assert info["parameters"] == {"year": 2025, "api_key": "<secret>"}
        assert "run-secret" not in text(run)
        assert info["published_dataset_versions"][0]["version_id"] == VERSION_ID
        assert tail.structured_content["logs"] == RUN_LOGS[-100:]
        assert f"offset={start}" in tail.structured_content["note"]
        assert earlier.structured_content["logs"] == RUN_LOGS[start : start + 100]


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


async def _call_remaining_reads(mcp: MCPClient, workspace: str) -> list[CallToolResult]:
    """Call the pipeline, run, database and webapp tools on whatever the workspace holds."""
    results = [
        await mcp.call_tool("openhexa_list_database_tables", {"workspace": workspace}),
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
            break
    return results
