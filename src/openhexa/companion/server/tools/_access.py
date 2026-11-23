"""Workspace clients for the grant of the current MCP request, and tool error mapping."""

from collections.abc import Callable

import anyio.to_thread
import httpx2
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError

from openhexa.companion import AuthenticationError, Client, OpenHexaError
from openhexa.companion._util import format_options
from openhexa.companion.server.config import Config
from openhexa.companion.server.store import Store, StoredWorkspace


class Access:
    """Runs library operations with the OpenHEXA tokens of the caller's grant."""

    def __init__(
        self, config: Config, store: Store, http_transport: httpx2.BaseTransport | None
    ) -> None:
        self._config = config
        self._store = store
        self._http_transport = http_transport

    async def workspaces(self) -> list[StoredWorkspace]:
        """List the workspaces of the caller's grant."""
        return await anyio.to_thread.run_sync(self._store.grant_workspaces, grant_id())

    async def run[T](self, workspace: str, operation: Callable[[Client], T]) -> T:
        """Call `operation` with a client for `workspace`, in a worker thread.

        Raises:
            ToolError: The grant does not hold the workspace, or the library raised. The
                message is the library's, with a pointer to the account page when
                OpenHEXA rejected the token.
        """
        grant = grant_id()
        access = await anyio.to_thread.run_sync(self._store.workspace_access, grant, workspace)
        if access is None:
            slugs = [ws.slug for ws in await self.workspaces()]
            raise ToolError(
                f"Workspace '{workspace}' is not connected to this connector. Connected "
                f"workspaces: {format_options(slugs)}"
            )

        def call() -> T:
            with Client(
                self._config.openhexa_url,
                workspace,
                access.token,
                http_transport=self._http_transport,
            ) as client:
                return operation(client)

        try:
            result = await anyio.to_thread.run_sync(call)
        except AuthenticationError as err:
            if not access.token_rejected:
                await anyio.to_thread.run_sync(
                    self._store.set_token_rejected, grant, workspace, True
                )
            raise ToolError(
                f"{err}. The OpenHEXA token stored for workspace '{workspace}' no longer "
                f"works: replace it at {self._config.public_url}/account"
            ) from err
        except OpenHexaError as err:
            raise ToolError(str(err)) from err
        if access.token_rejected:
            await anyio.to_thread.run_sync(self._store.set_token_rejected, grant, workspace, False)
        return result


def grant_id() -> str:
    """Return the grant of the current MCP request, which the bearer middleware authenticated."""
    token = get_access_token()
    if token is None or token.subject is None:
        raise RuntimeError("Tool called without an authenticated grant")
    return token.subject
