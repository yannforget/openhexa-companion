"""Assembles the companion's ASGI application: OAuth endpoints, connect page and MCP."""

from importlib.metadata import version

import httpx2
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Route

from openhexa.companion.server.config import Config
from openhexa.companion.server.oauth import Provider
from openhexa.companion.server.pages import Pages
from openhexa.companion.server.store import Store
from openhexa.companion.server.tools import Access, register_tools


def build_app(
    config: Config, store: Store, http_transport: httpx2.BaseTransport | None = None
) -> Starlette:
    """Build the application serving one OpenHEXA instance.

    The application's lifespan must run for `/mcp` to answer.

    Args:
        config: Settings of the deployment.
        store: Where clients, grants and tokens are kept.
        http_transport: Replaces the HTTP transport to OpenHEXA, for tests.
    """
    clients = ClientRegistrationOptions(enabled=True)
    revocation = RevocationOptions(enabled=True)
    # Given as strings, which `AuthSettings` keeps without a trailing slash: clients
    # compare the issuer with the `iss` of the connect page's redirect character by
    # character.
    auth = AuthSettings.model_validate(
        {
            "issuer_url": config.public_url,
            "resource_server_url": config.mcp_url,
            "validate_token_resource": True,
            "client_registration_options": clients,
            "revocation_options": revocation,
        }
    )
    server = MCPServer(
        name="openhexa-companion",
        title="OpenHEXA Companion",
        version=version("openhexa-companion"),
        website_url=config.public_url,
        auth_server_provider=Provider(config, store),
        auth=auth,
    )
    register_tools(server, Access(config, store, http_transport))
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        # Every MCP request needs an access token, which a page rebinding the
        # companion's domain cannot obtain, so Host and Origin are not restricted.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    metadata = build_metadata(auth.issuer_url, None, clients, revocation)
    # The connect page includes `iss` in its redirects (RFC 9207); the SDK does not
    # advertise it, so this route takes precedence over the SDK's.
    metadata.authorization_response_iss_parameter_supported = True
    app.router.routes.insert(
        0,
        Route(
            "/.well-known/oauth-authorization-server",
            endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        ),
    )
    app.router.routes.extend(Pages(config, store, http_transport).routes())
    return app
