"""The connect page that completes OAuth authorization, and helpers shared by pages."""

import hashlib
import hmac
import time
from importlib.metadata import version
from typing import Any
from urllib.parse import urlsplit

import anyio.to_thread
import httpx2
from jinja2 import Environment, PackageLoader, select_autoescape
from mcp.server.auth.provider import AuthorizationParams, construct_redirect_uri
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from openhexa.companion import Client, OpenHexaError, User
from openhexa.companion.server.config import Config
from openhexa.companion.server.store import PENDING_AUTHORIZATION_LIFETIME, NewWorkspace, Store

# Most workspaces the connect form accepts at once.
MAX_WORKSPACES = 20
# Headers of every page: no framing (clickjacking), no caching of pages holding tokens,
# and styles, scripts and images only from the companion's own static files.
PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        # No form-action: browsers apply it to the redirect back to the client too.
        "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
}
TEMPLATES = Environment(
    loader=PackageLoader("openhexa.companion.server"),
    autoescape=select_autoescape(),
    trim_blocks=True,
    lstrip_blocks=True,
)


class FormError(Exception):
    """The submitted form cannot be accepted; the message is shown to the user."""


class Pages:
    """Serves the connect page, verifying workspace tokens against OpenHEXA."""

    def __init__(
        self, config: Config, store: Store, http_transport: httpx2.BaseTransport | None
    ) -> None:
        self._config = config
        self._store = store
        self._http_transport = http_transport
        self._csrf_key = config.derive_key("csrf")

    def routes(self) -> list[Route | Mount]:
        return [
            Route("/connect", self.connect, methods=["GET", "POST"]),
            Mount(
                "/static",
                StaticFiles(packages=[("openhexa.companion.server", "static")]),
                name="static",
            ),
        ]

    async def connect(self, request: Request) -> Response:
        """Show the connect form, or verify it and send the user back to the client."""
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            return await self._show(request_id, slugs=[""], error=None)
        form = await request.form()
        request_id = str(form.get("request", ""))
        if not check_csrf(self._csrf_key, f"connect:{request_id}", str(form.get("csrf", ""))):
            return message_page(
                "Request expired", "Go back to your AI client and connect again.", 400
            )
        pending = await anyio.to_thread.run_sync(self._store.get_pending, request_id)
        if pending is None:
            return _expired_page()
        params = AuthorizationParams.model_validate_json(pending.params)
        if form.get("action") == "cancel":
            await anyio.to_thread.run_sync(self._store.delete_pending, request_id)
            return self._redirect(params, error="access_denied")
        rows = [
            (str(slug).strip(), str(token).strip())
            for slug, token in zip(form.getlist("slug"), form.getlist("token"), strict=False)
        ]
        try:
            user, workspaces = await anyio.to_thread.run_sync(self._verify, rows)
        except FormError as err:
            # Tokens are never sent back to the browser: the user enters them again.
            slugs = [slug for slug, token in rows if slug or token] or [""]
            return await self._show(request_id, slugs=slugs, error=str(err), status=400)
        code = await anyio.to_thread.run_sync(
            lambda: self._store.complete_pending(
                request_id, user_id=user.id, user_email=user.email, workspaces=workspaces
            )
        )
        if code is None:
            return _expired_page()
        return self._redirect(params, code=code)

    async def _show(
        self, request_id: str, *, slugs: list[str], error: str | None, status: int = 200
    ) -> Response:
        pending = await anyio.to_thread.run_sync(self._store.get_pending, request_id)
        if pending is None:
            return _expired_page()
        client_info = await anyio.to_thread.run_sync(self._store.get_client, pending.client_id)
        if client_info is None:
            return _expired_page()
        client = OAuthClientInformationFull.model_validate_json(client_info)
        params = AuthorizationParams.model_validate_json(pending.params)
        return page(
            "connect.html",
            "Connect to OpenHEXA",
            status=status,
            client_name=client.client_name or "An unnamed application",
            redirect_host=urlsplit(str(params.redirect_uri)).netloc,
            openhexa_host=urlsplit(self._config.openhexa_url).netloc,
            openhexa_url=self._config.openhexa_url,
            error_message=error,
            action=f"{self._config.public_url}/connect",
            request_id=request_id,
            csrf=sign_csrf(self._csrf_key, f"connect:{request_id}", PENDING_AUTHORIZATION_LIFETIME),
            slugs=slugs,
        )

    def _verify(self, rows: list[tuple[str, str]]) -> tuple[User, list[NewWorkspace]]:
        """Check each workspace token with OpenHEXA and that all belong to one user.

        Raises:
            FormError: A row is incomplete or repeated, a token does not give access to
                its workspace, or the tokens belong to different users.
        """
        rows = [(slug, token) for slug, token in rows if slug or token]
        if not rows:
            raise FormError("Enter at least one workspace slug and token.")
        if len(rows) > MAX_WORKSPACES:
            raise FormError(f"Connect at most {MAX_WORKSPACES} workspaces at once.")
        slugs = [slug for slug, _token in rows]
        if len(set(slugs)) != len(slugs):
            raise FormError("Each workspace can only be entered once.")
        user: User | None = None
        workspaces = []
        for slug, token in rows:
            owner, workspace = verify_token(self._config, self._http_transport, slug, token)
            if user is not None and owner.id != user.id:
                raise FormError(
                    f"The token for workspace '{slug}' belongs to another OpenHEXA user "
                    "than the other tokens. All tokens must be your own."
                )
            user = owner
            workspaces.append(workspace)
        if user is None:
            raise FormError("Enter at least one workspace slug and token.")
        return user, workspaces

    def _redirect(self, params: AuthorizationParams, **result: str) -> Response:
        """Send the user back to the client with the outcome and our issuer (RFC 9207)."""
        url = construct_redirect_uri(
            str(params.redirect_uri), **result, state=params.state, iss=self._config.public_url
        )
        return RedirectResponse(url, status_code=303, headers=PAGE_HEADERS)


def verify_token(
    config: Config, http_transport: httpx2.BaseTransport | None, slug: str, token: str
) -> tuple[User, NewWorkspace]:
    """Check with OpenHEXA that `token` opens workspace `slug`, and find its user.

    Raises:
        FormError: The slug or token is missing, or OpenHEXA refused them.
    """
    if not slug or not token:
        raise FormError("Enter both a slug and a token for each workspace.")
    with Client(config.openhexa_url, slug, token, http_transport=http_transport) as client:
        try:
            info = client.get_workspace()
            user = client.get_user()
        except OpenHexaError as err:
            raise FormError(f"Workspace '{slug}': {err}") from err
    return user, NewWorkspace(info.slug, info.name, token)


def sign_csrf(key: bytes, purpose: str, lifetime: int) -> str:
    """Sign `purpose` with an expiry, so only a form we served can submit it."""
    expires = int(time.time()) + lifetime
    return f"{expires}.{_csrf_signature(key, purpose, expires)}"


def check_csrf(key: bytes, purpose: str, token: str) -> bool:
    expires, _, signature = token.partition(".")
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(signature, _csrf_signature(key, purpose, int(expires)))


def _csrf_signature(key: bytes, purpose: str, expires: int) -> str:
    return hmac.new(key, f"{purpose}:{expires}".encode(), hashlib.sha256).hexdigest()


def page(template: str, title: str, *, status: int = 200, **context: Any) -> Response:
    """Render `template` from the templates folder as a page."""
    html = TEMPLATES.get_template(template).render(
        title=title, version=version("openhexa-companion"), **context
    )
    return HTMLResponse(html, status_code=status, headers=PAGE_HEADERS)


def message_page(title: str, message: str, status: int) -> Response:
    return page("message.html", title, status=status, message=message)


def _expired_page() -> Response:
    return message_page(
        "Request expired",
        "This connection request is unknown or has expired. Go back to your AI client "
        "and connect again.",
        404,
    )
