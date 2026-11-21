"""The OAuth authorization server that AI clients use to get access to the companion.

The SDK handles the protocol (registration, PKCE, token requests); this provider stores
its state and sends the user to the connect page, which completes authorization.
"""

from typing import override
from urllib.parse import urlsplit

import anyio.to_thread
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from openhexa.companion.server.config import Config
from openhexa.companion.server.store import ACCESS_TOKEN_LIFETIME, IssuedTokens, Store


class Provider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Stores the state of the OAuth flows and sends users to the connect page.

    The `subject` of codes and tokens is the grant ID, which tools use to find the
    workspaces they may access. Every token is issued for the MCP endpoint, the only
    resource the companion protects.
    """

    def __init__(self, config: Config, store: Store) -> None:
        self._config = config
        self._store = store

    @override
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = await anyio.to_thread.run_sync(self._store.get_client, client_id)
        return None if info is None else OAuthClientInformationFull.model_validate_json(info)

    @override
    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await anyio.to_thread.run_sync(
            self._store.save_client, client_info.client_id, client_info.model_dump_json()
        )

    @override
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Save the request and return the URL of the connect page that completes it.

        Raises:
            AuthorizeError: The client asked for a token for another resource.
        """
        if params.resource is not None and not _same_url(params.resource, self._config.mcp_url):
            raise AuthorizeError(
                "invalid_target", f"This server only issues tokens for {self._config.mcp_url}"
            )
        await anyio.to_thread.run_sync(self._store.purge)
        request_id = await anyio.to_thread.run_sync(
            self._store.create_pending, client.client_id, params.model_dump_json()
        )
        return f"{self._config.public_url}/connect?request={request_id}"

    @override
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = await anyio.to_thread.run_sync(self._store.get_code, authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        params = AuthorizationParams.model_validate_json(code.params)
        return AuthorizationCode(
            code=authorization_code,
            scopes=params.scopes or [],
            expires_at=code.expires_at,
            client_id=code.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self._config.mcp_url,
            subject=code.grant_id,
        )

    @override
    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        tokens = await anyio.to_thread.run_sync(
            self._store.exchange_code, authorization_code.code, authorization_code.scopes
        )
        if tokens is None:
            raise TokenError("invalid_grant", "authorization code was already used")
        return _oauth_token(tokens)

    @override
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token = await anyio.to_thread.run_sync(self._store.get_refresh_token, refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=token.client_id,
            scopes=token.scopes,
            expires_at=token.expires_at,
            resource=self._config.mcp_url,
            subject=token.grant_id,
        )

    @override
    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        tokens = await anyio.to_thread.run_sync(
            self._store.rotate_refresh_token, refresh_token.token, scopes
        )
        if tokens is None:
            raise TokenError("invalid_grant", "refresh token was already used")
        return _oauth_token(tokens)

    @override
    async def load_access_token(self, token: str) -> AccessToken | None:
        access = await anyio.to_thread.run_sync(self._store.get_access_token, token)
        if access is None:
            return None
        return AccessToken(
            token=token,
            client_id=access.client_id,
            scopes=access.scopes,
            expires_at=access.expires_at,
            resource=self._config.mcp_url,
            subject=access.grant_id,
        )

    @override
    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke the whole grant, so that neither token of the pair keeps working."""
        if token.subject is not None:
            await anyio.to_thread.run_sync(self._store.revoke_grant, token.subject)


def _oauth_token(tokens: IssuedTokens) -> OAuthToken:
    return OAuthToken(
        access_token=tokens.access_token,
        expires_in=ACCESS_TOKEN_LIFETIME,
        scope=" ".join(tokens.scopes) or None,
        refresh_token=tokens.refresh_token,
    )


def _same_url(a: str, b: str) -> bool:
    """Compare URLs ignoring the case of the scheme and host and a trailing slash."""
    pa, pb = urlsplit(a), urlsplit(b)
    return (
        pa.scheme.lower() == pb.scheme.lower()
        and pa.netloc.lower() == pb.netloc.lower()
        and pa.path.rstrip("/") == pb.path.rstrip("/")
    )
