"""The account page, where users choose which workspaces their connected clients reach.

Entering a workspace token identifies its OpenHEXA user, so it both signs in and stores
the token for that workspace. A session only manages grants and stored tokens: it never
gives access to tools.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from html import escape
from urllib.parse import urlsplit

import anyio.to_thread
import httpx2
from mcp.shared.auth import OAuthClientInformationFull
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from openhexa.companion.server.config import Config
from openhexa.companion.server.pages import (
    PAGE_HEADERS,
    FormError,
    check_csrf,
    message_page,
    page,
    sign_csrf,
    template,
    verify_token,
)
from openhexa.companion.server.store import (
    SESSION_LIFETIME,
    Session,
    Store,
    StoredWorkspace,
    UserGrant,
)

# Cookie holding the signed session ID.
SESSION_COOKIE = "companion_session"
# Cookie holding a random value that the account forms' CSRF tokens are bound to.
CSRF_COOKIE = "companion_csrf"
# Seconds a CSRF token of the account page is valid.
CSRF_LIFETIME = 60 * 60
# Actions of the forms of a signed-in user.
SESSION_ACTIONS = {"save", "forget", "disconnect", "logout"}


class Account:
    """Serves `/account`: sign-in, the workspace and client table, and sign-out."""

    def __init__(
        self, config: Config, store: Store, http_transport: httpx2.BaseTransport | None
    ) -> None:
        self._config = config
        self._store = store
        self._http_transport = http_transport
        self._session_key = config.derive_key("session")
        self._csrf_key = config.derive_key("account-csrf")

    def routes(self) -> list[Route]:
        return [Route("/account", self.account, methods=["GET", "POST"])]

    async def account(self, request: Request) -> Response:
        """Show the page, or apply a submitted form and redirect back to it."""
        browser = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        session_id = self._session_id(request)
        session = None
        if session_id is not None:
            session = await anyio.to_thread.run_sync(self._store.get_session, session_id)
        if request.method == "GET":
            response = await self._show(browser, session, error=None)
            self._set_cookie(response, CSRF_COOKIE, browser, max_age=None)
            return response
        form = await request.form()
        if CSRF_COOKIE not in request.cookies or not check_csrf(
            self._csrf_key, f"account:{browser}", str(form.get("csrf", ""))
        ):
            return message_page("Request expired", "Reload the account page and try again.", 400)
        action = str(form.get("action", ""))
        if action == "enter":
            return await self._enter(form, browser, session_id, session)
        if action not in SESSION_ACTIONS:
            return message_page("Unknown action", "Reload the account page and try again.", 400)
        if session_id is None or session is None:
            return message_page(
                "Signed out", "Your session has expired. Reload the page and sign in again.", 400
            )
        await anyio.to_thread.run_sync(self._apply, action, form, session_id, session)
        response = self._back()
        if action == "logout":
            response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax")
        return response

    async def _enter(
        self, form: FormData, browser: str, session_id: str | None, session: Session | None
    ) -> Response:
        """Verify an entered token, store it and sign its user in.

        A token of another user than the signed-in one signs the browser in as that user.
        """
        slug = str(form.get("slug", "")).strip()
        token = str(form.get("token", "")).strip()
        try:
            user, workspace = await anyio.to_thread.run_sync(
                verify_token, self._config, self._http_transport, slug, token
            )
        except FormError as err:
            return await self._show(browser, session, error=str(err), status=400)
        await anyio.to_thread.run_sync(self._store.save_workspace, user.id, workspace)
        response = self._back()
        if session_id is not None and session is not None and session.user_id == user.id:
            return response
        if session_id is not None:
            await anyio.to_thread.run_sync(self._store.delete_session, session_id)
        new_id = await anyio.to_thread.run_sync(self._store.create_session, user.id, user.email)
        self._set_cookie(response, SESSION_COOKIE, self._sign(new_id), max_age=SESSION_LIFETIME)
        return response

    def _apply(self, action: str, form: FormData, session_id: str, session: Session) -> None:
        """Apply a form of a signed-in user.

        Grants and workspaces that are not the user's change nothing: the page shown
        next reflects the current state.
        """
        match action:
            case "save":
                enabled = []
                for value in form.getlist("access"):
                    grant_id, _, slug = str(value).partition(":")
                    enabled.append((grant_id, slug))
                self._store.set_grant_workspaces(
                    session.user_id,
                    [str(value) for value in form.getlist("grant")],
                    [str(value) for value in form.getlist("slug")],
                    enabled,
                )
            case "forget":
                self._store.forget_workspace(session.user_id, str(form.get("slug", "")))
            case "disconnect":
                self._store.revoke_user_grant(session.user_id, str(form.get("grant", "")))
            case "logout":
                self._store.delete_session(session_id)

    async def _show(
        self, browser: str, session: Session | None, *, error: str | None, status: int = 200
    ) -> Response:
        csrf = sign_csrf(self._csrf_key, f"account:{browser}", CSRF_LIFETIME)
        hidden = f'<input type="hidden" name="csrf" value="{csrf}">'
        signed_in = ""
        if session is not None:
            workspaces, grants = await anyio.to_thread.run_sync(self._user_state, session)
            signed_in = _signed_in(session, workspaces, grants, hidden)
        body = template("account.html").substitute(
            openhexa_host=escape(urlsplit(self._config.openhexa_url).netloc),
            error=f'<p class="error" role="alert">{escape(error)}</p>' if error else "",
            action=escape(f"{self._config.public_url}/account"),
            csrf=hidden,
            enter_title="Sign in" if session is None else "Add a workspace or replace a token",
            enter_label="Sign in" if session is None else "Add",
            signed_in=signed_in,
        )
        return page("Your OpenHEXA Companion", body, status=status)

    def _user_state(self, session: Session) -> tuple[list[StoredWorkspace], list[UserGrant]]:
        return (
            self._store.user_workspaces(session.user_id),
            self._store.user_grants(session.user_id),
        )

    def _back(self) -> Response:
        """Redirect to the page after a form, so reloading does not submit it again."""
        return RedirectResponse(
            f"{self._config.public_url}/account", status_code=303, headers=PAGE_HEADERS
        )

    def _set_cookie(self, response: Response, name: str, value: str, max_age: int | None) -> None:
        response.set_cookie(
            name, value, max_age=max_age, secure=True, httponly=True, samesite="lax"
        )

    def _sign(self, session_id: str) -> str:
        return f"{session_id}.{self._signature(session_id)}"

    def _session_id(self, request: Request) -> str | None:
        """Return the session ID of the session cookie, if its signature is valid."""
        session_id, _, signature = request.cookies.get(SESSION_COOKIE, "").rpartition(".")
        if not session_id or not hmac.compare_digest(signature, self._signature(session_id)):
            return None
        return session_id

    def _signature(self, session_id: str) -> str:
        return hmac.new(self._session_key, session_id.encode(), hashlib.sha256).hexdigest()


def _signed_in(
    session: Session, workspaces: list[StoredWorkspace], grants: list[UserGrant], hidden: str
) -> str:
    return (
        f'<form method="post">{hidden}Signed in as <strong>{escape(session.user_email)}</strong>. '
        '<button type="submit" name="action" value="logout">Sign out</button></form>'
        f"{_access_table(workspaces, grants, hidden)}"
    )


def _access_table(workspaces: list[StoredWorkspace], grants: list[UserGrant], hidden: str) -> str:
    """The workspaces as rows and the clients as columns, with a checkbox per access.

    Forget and disconnect buttons submit their own small forms, linked with the `form`
    attribute because HTML forms cannot nest.
    """
    if not workspaces:
        return "<p>You have not given the companion any workspace token yet.</p>"
    actions = []
    header = []
    for index, grant in enumerate(grants):
        form_id = f"disconnect-{index}"
        actions.append(
            f'<form id="{form_id}" method="post">{hidden}'
            '<input type="hidden" name="action" value="disconnect">'
            f'<input type="hidden" name="grant" value="{escape(grant.grant_id)}"></form>'
        )
        header.append(
            f"<th>{_client_label(grant)}<br><small>Connected {_date(grant.created_at)}, "
            f"used {_date(grant.last_used_at)}</small><br>"
            f'<button type="submit" form="{form_id}">Disconnect</button></th>'
        )
    rows = []
    for index, workspace in enumerate(workspaces):
        form_id = f"forget-{index}"
        actions.append(
            f'<form id="{form_id}" method="post">{hidden}'
            '<input type="hidden" name="action" value="forget">'
            f'<input type="hidden" name="slug" value="{escape(workspace.slug)}"></form>'
        )
        warning = (
            '<br><span class="warning">OpenHEXA rejected this token: enter a new one.</span>'
            if workspace.token_rejected
            else ""
        )
        cells = "".join(
            '<td class="check"><input type="checkbox" name="access" '
            f'value="{escape(grant.grant_id)}:{escape(workspace.slug)}" '
            f'aria-label="{escape(workspace.slug)} for {escape(_client_name(grant))}"'
            f"{' checked' if workspace.slug in grant.workspaces else ''}></td>"
            for grant in grants
        )
        rows.append(
            f'<tr><th scope="row">{escape(workspace.name)} '
            f"(<code>{escape(workspace.slug)}</code>){warning}<br>"
            f'<button type="submit" form="{form_id}">Forget</button>'
            f'<input type="hidden" name="slug" value="{escape(workspace.slug)}"></th>'
            f"{cells}</tr>"
        )
    grant_fields = "".join(
        f'<input type="hidden" name="grant" value="{escape(grant.grant_id)}">' for grant in grants
    )
    no_clients = (
        "<p>No AI client is connected. Connect one from your AI client: it can then be "
        "given access to these workspaces here.</p>"
        if not grants
        else ""
    )
    save = (
        '<div class="actions"><button type="submit" class="primary">Save</button></div>'
        if grants
        else ""
    )
    return (
        "<h2>Workspaces and AI clients</h2>"
        "<p>Tick the workspaces each AI client may use. <em>Forget</em> deletes the "
        "companion's copy of a token, and <em>Disconnect</em> revokes a client's access "
        "entirely. Neither revokes the token in OpenHEXA: if a token may have leaked, "
        "revoke it in OpenHEXA too.</p>"
        f"{no_clients}"
        f'<form method="post">{hidden}<input type="hidden" name="action" value="save">'
        f'{grant_fields}<div class="table"><table><thead><tr><th scope="col">Workspace</th>'
        f"{''.join(header)}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        f"{save}</form>{''.join(actions)}"
    )


def _client_label(grant: UserGrant) -> str:
    client = OAuthClientInformationFull.model_validate_json(grant.client_info)
    hosts = sorted({urlsplit(str(uri)).netloc for uri in client.redirect_uris or []})
    return f"{escape(_client_name(grant))} <small>({escape(', '.join(hosts))})</small>"


def _client_name(grant: UserGrant) -> str:
    client = OAuthClientInformationFull.model_validate_json(grant.client_info)
    return client.client_name or "An unnamed application"


def _date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")
