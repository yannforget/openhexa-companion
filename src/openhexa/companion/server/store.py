"""SQLite storage for clients, grants and tokens, with OpenHEXA tokens encrypted at rest.

Secrets the service issues (request IDs, codes, access and refresh tokens) are stored
only as SHA-256 hashes, so a copy of the database cannot be used to call the service.
"""

import hashlib
import secrets
import sqlite3
import threading
import time
import uuid
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet

# Seconds a pending authorization waits for the connect page to be submitted.
PENDING_AUTHORIZATION_LIFETIME = 10 * 60
# Seconds an authorization code can be exchanged.
AUTHORIZATION_CODE_LIFETIME = 5 * 60
# Seconds an access token is valid.
ACCESS_TOKEN_LIFETIME = 60 * 60
# Seconds a grant, its refresh token and a registered client survive without use.
IDLE_LIFETIME = 30 * 24 * 60 * 60
# Seconds between updates of a "last used" time, to avoid a write on every request.
LAST_USED_RESOLUTION = 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    info TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_authorizations (
    request_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients ON DELETE CASCADE,
    params TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    user_email TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE TABLE IF NOT EXISTS grant_workspaces (
    grant_id TEXT NOT NULL REFERENCES grants ON DELETE CASCADE,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    token TEXT NOT NULL,
    rejected_at INTEGER,
    PRIMARY KEY (grant_id, slug)
);
CREATE TABLE IF NOT EXISTS authorization_codes (
    code_hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL REFERENCES grants ON DELETE CASCADE,
    params TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);
CREATE TABLE IF NOT EXISTS access_tokens (
    token_hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL REFERENCES grants ON DELETE CASCADE,
    scopes TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL REFERENCES grants ON DELETE CASCADE,
    scopes TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
"""

# Selects an unexpired token of a grant that is neither revoked nor idle, by table.
TOKEN_QUERIES = {
    table: f"""
        SELECT tokens.grant_id, grants.client_id, tokens.scopes, tokens.expires_at
        FROM {table} AS tokens JOIN grants USING (grant_id)
        WHERE tokens.token_hash = :token_hash AND tokens.expires_at > :now
        AND grants.revoked_at IS NULL AND grants.last_used_at > :idle_since
    """  # noqa: S608 (the table names are constants)
    for table in ("access_tokens", "refresh_tokens")
}


@dataclass(frozen=True)
class Pending:
    """An authorization request waiting for the user to submit the connect page.

    Attributes:
        client_id: The client that sent the request.
        params: The validated request, as JSON written by the OAuth provider.
    """

    client_id: str
    params: str


@dataclass(frozen=True)
class NewWorkspace:
    """A workspace verified on the connect page, to add to a new grant."""

    slug: str
    name: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class Code:
    """An unused authorization code.

    Attributes:
        grant_id: The grant the code gives access to.
        client_id: The client the code was issued to.
        params: The authorization request, as JSON written by the OAuth provider.
        expires_at: Unix time after which the code cannot be exchanged.
    """

    grant_id: str
    client_id: str
    params: str
    expires_at: int


@dataclass(frozen=True)
class Token:
    """A valid access or refresh token of an active grant."""

    grant_id: str
    client_id: str
    scopes: list[str]
    expires_at: int


@dataclass(frozen=True)
class IssuedTokens:
    """A new access token and refresh token, in clear text for the client."""

    access_token: str
    refresh_token: str
    scopes: list[str]


@dataclass(frozen=True)
class GrantWorkspace:
    """A workspace a grant gives access to.

    Attributes:
        slug: Slug of the workspace.
        name: Name of the workspace when it was added.
        token_rejected: OpenHEXA rejected the stored token on its last use.
    """

    slug: str
    name: str
    token_rejected: bool


@dataclass(frozen=True)
class WorkspaceAccess:
    """What a grant stores to reach one workspace.

    Attributes:
        token: The decrypted OpenHEXA token.
        token_rejected: OpenHEXA rejected the token on its last use.
    """

    token: str = field(repr=False)
    token_rejected: bool


class Store:
    """Reads and writes the service's state in one SQLite database.

    Safe to share between threads: operations are serialised.
    """

    def __init__(self, path: Path | str, fernet_key: bytes) -> None:
        """Open the database at `path`, creating its tables if needed.

        Args:
            path: Path of the SQLite file, or `:memory:` for a temporary database.
            fernet_key: Key encrypting the OpenHEXA tokens.
        """
        self._db = sqlite3.connect(path, check_same_thread=False, autocommit=True)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._fernet = Fernet(fernet_key)

    def close(self) -> None:
        self._db.close()

    def save_client(self, client_id: str, info: str) -> None:
        """Save a client registration, given as the JSON of its client information."""
        now = int(time.time())
        with self._transaction() as db:
            db.execute("INSERT INTO clients VALUES (?, ?, ?, ?)", (client_id, info, now, now))

    def get_client(self, client_id: str) -> str | None:
        """Return the JSON of a client registration, recording that the client is in use."""
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute(
                "SELECT info FROM clients WHERE client_id = ?", (client_id,)
            ).fetchone()
            db.execute(
                "UPDATE clients SET last_used_at = ? WHERE client_id = ? AND last_used_at < ?",
                (now, client_id, now - LAST_USED_RESOLUTION),
            )
        return None if row is None else row["info"]

    def create_pending(self, client_id: str, params: str) -> str:
        """Save an authorization request and return the random ID that identifies it."""
        request_id = _random()
        with self._transaction() as db:
            db.execute(
                "INSERT INTO pending_authorizations VALUES (?, ?, ?, ?)",
                (
                    _hash(request_id),
                    client_id,
                    params,
                    int(time.time()) + PENDING_AUTHORIZATION_LIFETIME,
                ),
            )
        return request_id

    def get_pending(self, request_id: str) -> Pending | None:
        """Return an authorization request that has not expired."""
        with self._transaction() as db:
            row = db.execute(
                "SELECT client_id, params FROM pending_authorizations "
                "WHERE request_hash = ? AND expires_at > ?",
                (_hash(request_id), int(time.time())),
            ).fetchone()
        return None if row is None else Pending(row["client_id"], row["params"])

    def delete_pending(self, request_id: str) -> None:
        """Delete an authorization request the user declined."""
        with self._transaction() as db:
            db.execute(
                "DELETE FROM pending_authorizations WHERE request_hash = ?", (_hash(request_id),)
            )

    def complete_pending(
        self,
        request_id: str,
        *,
        user_id: str,
        user_email: str,
        workspaces: Sequence[NewWorkspace],
    ) -> str | None:
        """Create a grant for an authorization request and return a new code for it.

        The request is consumed, so it can be completed only once.

        Args:
            request_id: ID of the authorization request.
            user_id: The OpenHEXA user the workspace tokens belong to.
            user_email: Email address of that user.
            workspaces: The verified workspaces and their tokens.

        Returns:
            The authorization code, or `None` if the request expired or was already used.
        """
        now = int(time.time())
        code = _random()
        grant_id = str(uuid.uuid4())
        with self._transaction() as db:
            row = db.execute(
                "DELETE FROM pending_authorizations WHERE request_hash = ? AND expires_at > ? "
                "RETURNING client_id, params",
                (_hash(request_id), now),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "INSERT INTO grants VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (grant_id, row["client_id"], user_id, user_email, now, now),
            )
            db.executemany(
                "INSERT INTO grant_workspaces VALUES (?, ?, ?, ?, NULL)",
                [(grant_id, ws.slug, ws.name, self._encrypt(ws.token)) for ws in workspaces],
            )
            db.execute(
                "INSERT INTO authorization_codes VALUES (?, ?, ?, ?, NULL)",
                (_hash(code), grant_id, row["params"], now + AUTHORIZATION_CODE_LIFETIME),
            )
        return code

    def get_code(self, code: str) -> Code | None:
        """Return an unused authorization code of an active grant.

        A code presented a second time may have been stolen, so its grant is revoked,
        which also invalidates the tokens issued for the first use.
        """
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute(
                "SELECT codes.grant_id, grants.client_id, codes.params, codes.expires_at, "
                "codes.used_at, grants.revoked_at IS NULL AND grants.last_used_at > :idle_since "
                "AS active FROM authorization_codes AS codes JOIN grants USING (grant_id) "
                "WHERE codes.code_hash = :code_hash",
                {"code_hash": _hash(code), "idle_since": now - IDLE_LIFETIME},
            ).fetchone()
            if row is None:
                return None
            if row["used_at"] is not None:
                _revoke_grant(db, row["grant_id"], now)
                return None
        if not row["active"]:
            return None
        return Code(row["grant_id"], row["client_id"], row["params"], row["expires_at"])

    def exchange_code(self, code: str, scopes: list[str]) -> IssuedTokens | None:
        """Mark a code as used and issue the first tokens of its grant.

        Returns:
            The tokens, or `None` if the code was used meanwhile.
        """
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute(
                "UPDATE authorization_codes SET used_at = ? "
                "WHERE code_hash = ? AND used_at IS NULL RETURNING grant_id",
                (now, _hash(code)),
            ).fetchone()
            if row is None:
                return None
            return _issue_tokens(db, row["grant_id"], scopes, now)

    def get_access_token(self, token: str) -> Token | None:
        """Return a valid access token, recording that its grant is in use."""
        return self._get_token("access_tokens", token)

    def get_refresh_token(self, token: str) -> Token | None:
        """Return a valid refresh token of an active grant."""
        return self._get_token("refresh_tokens", token)

    def rotate_refresh_token(self, token: str, scopes: list[str]) -> IssuedTokens | None:
        """Replace a refresh token and the grant's access tokens with new ones.

        Returns:
            The new tokens, or `None` if the refresh token was used meanwhile.
        """
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute(
                "DELETE FROM refresh_tokens WHERE token_hash = ? RETURNING grant_id",
                (_hash(token),),
            ).fetchone()
            if row is None:
                return None
            db.execute("DELETE FROM access_tokens WHERE grant_id = ?", (row["grant_id"],))
            return _issue_tokens(db, row["grant_id"], scopes, now)

    def revoke_grant(self, grant_id: str) -> None:
        """Revoke a grant and delete its tokens."""
        with self._transaction() as db:
            _revoke_grant(db, grant_id, int(time.time()))

    def grant_workspaces(self, grant_id: str) -> list[GrantWorkspace]:
        """List the workspaces of a grant, by slug."""
        with self._transaction() as db:
            rows = db.execute(
                "SELECT slug, name, rejected_at FROM grant_workspaces "
                "WHERE grant_id = ? ORDER BY slug",
                (grant_id,),
            ).fetchall()
        return [
            GrantWorkspace(row["slug"], row["name"], row["rejected_at"] is not None) for row in rows
        ]

    def workspace_access(self, grant_id: str, slug: str) -> WorkspaceAccess | None:
        """Return the token of a workspace in a grant."""
        with self._transaction() as db:
            row = db.execute(
                "SELECT token, rejected_at FROM grant_workspaces WHERE grant_id = ? AND slug = ?",
                (grant_id, slug),
            ).fetchone()
        if row is None:
            return None
        token = self._fernet.decrypt(row["token"]).decode()
        return WorkspaceAccess(token, row["rejected_at"] is not None)

    def set_token_rejected(self, grant_id: str, slug: str, rejected: bool) -> None:
        """Record whether OpenHEXA rejected the token of a workspace in a grant."""
        with self._transaction() as db:
            db.execute(
                "UPDATE grant_workspaces SET rejected_at = ? WHERE grant_id = ? AND slug = ?",
                (int(time.time()) if rejected else None, grant_id, slug),
            )

    def purge(self) -> None:
        """Delete expired requests, codes and tokens, and idle grants and clients."""
        now = int(time.time())
        idle_since = now - IDLE_LIFETIME
        with self._transaction() as db:
            db.execute("DELETE FROM pending_authorizations WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM authorization_codes WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM access_tokens WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM refresh_tokens WHERE expires_at <= ?", (now,))
            db.execute(
                "DELETE FROM grants WHERE revoked_at IS NOT NULL OR last_used_at <= ?",
                (idle_since,),
            )
            db.execute("DELETE FROM clients WHERE last_used_at <= ?", (idle_since,))

    def _get_token(self, table: str, token: str) -> Token | None:
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute(
                TOKEN_QUERIES[table],
                {"token_hash": _hash(token), "now": now, "idle_since": now - IDLE_LIFETIME},
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE grants SET last_used_at = ? WHERE grant_id = ? AND last_used_at < ?",
                (now, row["grant_id"], now - LAST_USED_RESOLUTION),
            )
        return Token(row["grant_id"], row["client_id"], row["scopes"].split(), row["expires_at"])

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection]:
        """Run the block in one transaction, committed on success and rolled back on error."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode()).decode()


def _issue_tokens(
    db: sqlite3.Connection, grant_id: str, scopes: list[str], now: int
) -> IssuedTokens:
    access_token = _random()
    refresh_token = _random()
    joined = " ".join(scopes)
    db.execute(
        "INSERT INTO access_tokens VALUES (?, ?, ?, ?)",
        (_hash(access_token), grant_id, joined, now + ACCESS_TOKEN_LIFETIME),
    )
    db.execute(
        "INSERT INTO refresh_tokens VALUES (?, ?, ?, ?)",
        (_hash(refresh_token), grant_id, joined, now + IDLE_LIFETIME),
    )
    db.execute("UPDATE grants SET last_used_at = ? WHERE grant_id = ?", (now, grant_id))
    return IssuedTokens(access_token, refresh_token, scopes)


def _revoke_grant(db: sqlite3.Connection, grant_id: str, now: int) -> None:
    db.execute(
        "UPDATE grants SET revoked_at = ? WHERE grant_id = ? AND revoked_at IS NULL",
        (now, grant_id),
    )
    db.execute("DELETE FROM access_tokens WHERE grant_id = ?", (grant_id,))
    db.execute("DELETE FROM refresh_tokens WHERE grant_id = ?", (grant_id,))


def _random() -> str:
    return secrets.token_urlsafe(32)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
