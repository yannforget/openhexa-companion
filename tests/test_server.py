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

from openhexa.companion.server.app import build_app
from openhexa.companion.server.config import Config
from openhexa.companion.server.store import Store

PUBLIC_URL = "https://companion.test"
REDIRECT_URI = "https://client.test/callback"
# Tokens the fake OpenHEXA accepts: the user they belong to and the workspaces they open.
FAKE_TOKENS = {
    "alice-cases": ("alice", {"cases"}),
    "alice-stock": ("alice", {"stock"}),
    "bob-stock": ("bob", {"stock"}),
}


def fake_openhexa(revoked: set[str]) -> httpx2.MockTransport:
    """An OpenHEXA instance that knows the users and workspaces of `FAKE_TOKENS`."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        if token not in FAKE_TOKENS or token in revoked:
            return httpx2.Response(401)
        user, workspaces = FAKE_TOKENS[token]
        body = json.loads(request.content)
        if "me {" in body["query"]:
            return _data({"me": {"user": {"id": user, "email": f"{user}@example.org"}}})
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

    assert [ws["slug"] for ws in listed.structured_content["workspaces"]] == [
        env["OPENHEXA_WORKSPACE"]
    ]
    assert not workspace.is_error, text(workspace)
    assert workspace.structured_content["workspace"] == env["OPENHEXA_WORKSPACE"]
