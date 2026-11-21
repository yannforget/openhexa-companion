"""Browser pages of the companion: the connect page that completes OAuth authorization."""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from html import escape
from importlib.resources import files
from string import Template
from urllib.parse import urlsplit

import anyio.to_thread
import httpx2
from mcp.server.auth.provider import AuthorizationParams, construct_redirect_uri
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from openhexa.companion import Client, OpenHexaError, User
from openhexa.companion.server.config import Config
from openhexa.companion.server.store import PENDING_AUTHORIZATION_LIFETIME, NewWorkspace, Store

# Most workspaces the connect form accepts at once.
MAX_WORKSPACES = 20
# Headers of every page: no framing (clickjacking), no caching of pages holding tokens,
# and scripts limited to the page's own nonce-tagged script.
PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
TEMPLATES = files("openhexa.companion.server") / "templates"


class FormError(Exception):
    """The submitted form cannot be accepted; the message is shown to the user."""


@dataclass(frozen=True)
class Row:
    """One workspace row of the connect form."""

    slug: str
    token: str


class Pages:
    """Serves the connect page, verifying workspace tokens against OpenHEXA."""

    def __init__(
        self, config: Config, store: Store, http_transport: httpx2.BaseTransport | None
    ) -> None:
        self._config = config
        self._store = store
        self._http_transport = http_transport
        self._csrf_key = config.derive_key("csrf")

    def routes(self) -> list[Route]:
        return [Route("/connect", self.connect, methods=["GET", "POST"])]

    async def connect(self, request: Request) -> Response:
        """Show the connect form, or verify it and send the user back to the client."""
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            return await self._show(request_id, rows=[Row("", "")], error=None)
        form = await request.form()
        request_id = str(form.get("request", ""))
        if not self._valid_csrf(request_id, str(form.get("csrf", ""))):
            return _message_page(
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
            Row(str(slug).strip(), str(token).strip())
            for slug, token in zip(form.getlist("slug"), form.getlist("token"), strict=False)
        ]
        try:
            user, workspaces = await anyio.to_thread.run_sync(self._verify, rows)
        except FormError as err:
            # Tokens are never sent back to the browser: the user enters them again.
            return await self._show(
                request_id, rows=[Row(row.slug, "") for row in rows], error=str(err)
            )
        code = await anyio.to_thread.run_sync(
            lambda: self._store.complete_pending(
                request_id, user_id=user.id, user_email=user.email, workspaces=workspaces
            )
        )
        if code is None:
            return _expired_page()
        return self._redirect(params, code=code)

    async def _show(self, request_id: str, *, rows: list[Row], error: str | None) -> Response:
        pending = await anyio.to_thread.run_sync(self._store.get_pending, request_id)
        if pending is None:
            return _expired_page()
        client_info = await anyio.to_thread.run_sync(self._store.get_client, pending.client_id)
        if client_info is None:
            return _expired_page()
        client = OAuthClientInformationFull.model_validate_json(client_info)
        params = AuthorizationParams.model_validate_json(pending.params)
        nonce = secrets.token_urlsafe(16)
        rows_html = "\n".join(
            '<div class="row">'
            f'<input name="slug" value="{escape(row.slug)}" placeholder="Workspace slug" '
            'autocomplete="off" aria-label="Workspace slug" required>'
            '<input name="token" type="password" placeholder="Workspace token" '
            'autocomplete="off" aria-label="Workspace token" required>'
            "</div>"
            for row in rows or [Row("", "")]
        )
        body = _template("connect.html").substitute(
            client_name=escape(client.client_name or "An unnamed application"),
            redirect_host=escape(urlsplit(str(params.redirect_uri)).netloc),
            openhexa_host=escape(urlsplit(self._config.openhexa_url).netloc),
            error=f'<p class="error" role="alert">{escape(error)}</p>' if error else "",
            action=escape(f"{self._config.public_url}/connect"),
            request_id=escape(request_id),
            csrf=self._csrf_token(request_id),
            rows=rows_html,
            nonce=nonce,
        )
        return _page("Connect to OpenHEXA", body, nonce=nonce)

    def _verify(self, rows: list[Row]) -> tuple[User, list[NewWorkspace]]:
        """Check each workspace token with OpenHEXA and that all belong to one user.

        Raises:
            FormError: A row is incomplete or repeated, a token does not give access to
                its workspace, or the tokens belong to different users.
        """
        rows = [row for row in rows if row.slug or row.token]
        if not rows:
            raise FormError("Enter at least one workspace slug and token.")
        if len(rows) > MAX_WORKSPACES:
            raise FormError(f"Connect at most {MAX_WORKSPACES} workspaces at once.")
        slugs = [row.slug for row in rows]
        if len(set(slugs)) != len(slugs):
            raise FormError("Each workspace can only be entered once.")
        user: User | None = None
        workspaces = []
        for row in rows:
            if not row.slug or not row.token:
                raise FormError("Enter both a slug and a token for each workspace.")
            with Client(
                self._config.openhexa_url, row.slug, row.token, http_transport=self._http_transport
            ) as client:
                try:
                    info = client.get_workspace()
                    owner = client.get_user()
                except OpenHexaError as err:
                    raise FormError(f"Workspace '{row.slug}': {err}") from err
            if user is not None and owner.id != user.id:
                raise FormError(
                    f"The token for workspace '{row.slug}' belongs to another OpenHEXA user "
                    "than the other tokens. All tokens must be your own."
                )
            user = owner
            workspaces.append(NewWorkspace(info.slug, info.name, row.token))
        if user is None:
            raise FormError("Enter at least one workspace slug and token.")
        return user, workspaces

    def _redirect(self, params: AuthorizationParams, **result: str) -> Response:
        """Send the user back to the client with the outcome and our issuer (RFC 9207)."""
        url = construct_redirect_uri(
            str(params.redirect_uri), **result, state=params.state, iss=self._config.public_url
        )
        return RedirectResponse(url, status_code=303, headers=PAGE_HEADERS)

    def _csrf_token(self, request_id: str) -> str:
        """Sign the request ID with an expiry, so only our form can submit it."""
        expires = int(time.time()) + PENDING_AUTHORIZATION_LIFETIME
        return f"{expires}.{self._csrf_signature(request_id, expires)}"

    def _valid_csrf(self, request_id: str, token: str) -> bool:
        expires, _, signature = token.partition(".")
        if not expires.isdigit() or int(expires) < time.time():
            return False
        return hmac.compare_digest(signature, self._csrf_signature(request_id, int(expires)))

    def _csrf_signature(self, request_id: str, expires: int) -> str:
        message = f"connect:{request_id}:{expires}".encode()
        return hmac.new(self._csrf_key, message, hashlib.sha256).hexdigest()


def _template(name: str) -> Template:
    return Template((TEMPLATES / name).read_text(encoding="utf-8"))


def _page(title: str, body: str, *, status: int = 200, nonce: str | None = None) -> Response:
    script = f"'nonce-{nonce}'" if nonce else "'none'"
    headers = PAGE_HEADERS | {
        "Content-Security-Policy": (
            # No form-action: browsers apply it to the redirect back to the client too.
            f"default-src 'none'; style-src 'unsafe-inline'; script-src {script}; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
    }
    html = _template("page.html").substitute(title=escape(title), body=body)
    return HTMLResponse(html, status_code=status, headers=headers)


def _message_page(title: str, message: str, status: int) -> Response:
    return _page(title, f"<p>{escape(message)}</p>", status=status)


def _expired_page() -> Response:
    return _message_page(
        "Request expired",
        "This connection request is unknown or has expired. Go back to your AI client "
        "and connect again.",
        404,
    )
