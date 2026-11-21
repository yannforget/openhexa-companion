"""Short-lived URLs that stream dataset files, so AI clients can download complete files.

A URL holds a signed, stateless token naming the grant, workspace, dataset version and
path. Fetching it checks the grant again and streams the bytes from storage through the
companion: a redirect to storage would need the storage domain allowlisted in the AI
client's sandbox, and would expose the storage URL.
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import anyio
import anyio.to_thread
import httpx2
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from openhexa.companion import (
    AuthenticationError,
    Client,
    DatasetFile,
    NotFoundError,
    OpenHexaError,
    PermissionDeniedError,
)
from openhexa.companion.server.config import Config
from openhexa.companion.server.store import Store

# Seconds a file URL can be used.
FILE_URL_LIFETIME = 60 * 60
# Matches the token in the path of a file URL, to redact it from logs.
TOKEN_IN_PATH = re.compile(r"/files/[^/\s?]+")
# A single byte range: `start-end`, `start-` or `-suffix_length`.
BYTE_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


@dataclass(frozen=True)
class FileGrant:
    """What a file URL gives access to.

    Attributes:
        grant_id: The grant the URL was issued under.
        workspace: The workspace whose token reads the file.
        dataset: The dataset, as `workspace/slug` of its owner.
        version_id: The exact version of the file.
        path: Path of the file in the version.
        expires_at: Unix time after which the URL no longer works.
    """

    grant_id: str
    workspace: str
    dataset: str
    version_id: str
    path: str
    expires_at: int


class RedactFileTokens(logging.Filter):
    """Replaces the token of file URLs in log records, such as uvicorn's access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                TOKEN_IN_PATH.sub("/files/<redacted>", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        record.msg = TOKEN_IN_PATH.sub("/files/<redacted>", str(record.msg))
        return True


class Gateway:
    """Issues file URLs and serves them."""

    def __init__(
        self, config: Config, store: Store, http_transport: httpx2.BaseTransport | None
    ) -> None:
        self._config = config
        self._store = store
        self._http_transport = http_transport
        self._key = config.derive_key("files")

    def routes(self) -> list[Route]:
        return [Route("/files/{token}/{filename}", self.serve, methods=["GET", "HEAD"])]

    def url(self, grant_id: str, workspace: str, file: DatasetFile) -> tuple[str, datetime]:
        """Return a URL to `file` for the grant, and when it expires."""
        expires_at = int(time.time()) + FILE_URL_LIFETIME
        grant = FileGrant(
            grant_id=grant_id,
            workspace=workspace,
            dataset=f"{file.workspace}/{file.dataset}",
            version_id=file.version_id,
            path=file.path,
            expires_at=expires_at,
        )
        filename = quote(file.path.rsplit("/", 1)[-1], safe="")
        url = f"{self._config.public_url}/files/{self._sign(grant)}/{filename}"
        return url, datetime.fromtimestamp(expires_at, UTC)

    async def serve(self, request: Request) -> Response:
        """Stream the file of a valid URL, or the single byte range requested.

        Any invalid, expired or revoked URL gets the same bare 404, so that probing
        reveals nothing.
        """
        grant = self._verify(request.path_params["token"])
        if grant is None:
            return _not_found()
        access = await anyio.to_thread.run_sync(
            self._store.workspace_access, grant.grant_id, grant.workspace
        )
        if access is None:
            return _not_found()
        stack = ExitStack()
        client = stack.enter_context(
            Client(
                self._config.openhexa_url,
                grant.workspace,
                access.token,
                http_transport=self._http_transport,
            )
        )
        try:
            response = await anyio.to_thread.run_sync(
                _open, client, grant, request.method, request.headers.get("range"), stack
            )
        except OpenHexaError as err:
            stack.close()
            if isinstance(err, AuthenticationError):
                await anyio.to_thread.run_sync(
                    self._store.set_token_rejected, grant.grant_id, grant.workspace, True
                )
            return _error_response(err)
        except BaseException:
            stack.close()
            raise
        if response.chunks is None:
            stack.close()
            return Response(status_code=response.status, headers=response.headers)
        return StreamingResponse(
            _stream(response.chunks, stack), status_code=response.status, headers=response.headers
        )

    def _sign(self, grant: FileGrant) -> str:
        payload = json.dumps(
            [
                grant.grant_id,
                grant.workspace,
                grant.dataset,
                grant.version_id,
                grant.path,
                grant.expires_at,
            ],
            separators=(",", ":"),
        ).encode()
        return f"{_b64encode(payload)}.{_b64encode(self._signature(payload))}"

    def _verify(self, token: str) -> FileGrant | None:
        """Return what a token grants, or `None` if it is forged, malformed or expired."""
        encoded, _, signature = token.partition(".")
        try:
            payload = _b64decode(encoded)
            valid = hmac.compare_digest(_b64decode(signature), self._signature(payload))
        except ValueError:
            return None
        if not valid:
            return None
        grant_id, workspace, dataset, version_id, path, expires_at = json.loads(payload)
        if expires_at <= time.time():
            return None
        return FileGrant(grant_id, workspace, dataset, version_id, path, expires_at)

    def _signature(self, payload: bytes) -> bytes:
        return hmac.digest(self._key, payload, hashlib.sha256)


@dataclass(frozen=True)
class Opened:
    """The response to a file request, with the open download if there is a body."""

    status: int
    headers: dict[str, str]
    chunks: Iterator[bytes] | None


def _open(
    client: Client, grant: FileGrant, method: str, range_header: str | None, stack: ExitStack
) -> Opened:
    """Find the file of `grant` and open the download, registering it on `stack`.

    Opening the download before answering means storage errors still get an error
    status instead of a truncated success.
    """
    version = client.datasets.version(grant.dataset, version_id=grant.version_id)
    file = client.datasets.file(version, grant.path)
    headers = _file_headers(file)
    byte_range = _parse_range(range_header, file.size)
    if byte_range == "unsatisfiable":
        return Opened(416, headers | {"Content-Range": f"bytes */{file.size}"}, None)
    status, offset, end = 200, 0, file.size - 1
    if isinstance(byte_range, tuple):
        status, (offset, end) = 206, byte_range
        headers["Content-Range"] = f"bytes {offset}-{end}/{file.size}"
    headers["Content-Length"] = str(end - offset + 1)
    if method == "HEAD" or file.size == 0:
        return Opened(status, headers, None)
    length = None if end == file.size - 1 else end - offset + 1
    stream = stack.enter_context(client.datasets.read(file, offset=offset, length=length))
    return Opened(status, headers, iter(stream))


async def _stream(chunks: Iterator[bytes], stack: ExitStack) -> AsyncIterator[bytes]:
    """Yield chunks read in worker threads, then close the download and client."""
    try:
        while (chunk := await anyio.to_thread.run_sync(next, chunks, None)) is not None:
            yield chunk
    finally:
        # Shielded so that a client disconnecting still releases the storage connection.
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(stack.close)


def _file_headers(file: DatasetFile) -> dict[str, str]:
    """Headers describing a file.

    Files are always attachments in a sandbox: an HTML or SVG dataset file opened in a
    browser must not run scripts on the companion's origin.
    """
    filename = file.path.rsplit("/", 1)[-1]
    fallback = filename.encode("ascii", "replace").decode().replace('"', "_").replace("?", "_")
    return {
        "Content-Type": file.content_type or "application/octet-stream",
        "Content-Disposition": (
            f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
        ),
        "Accept-Ranges": "bytes",
        "ETag": f'"{file.version_id}.{file.id}"',
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


def _parse_range(header: str | None, size: int) -> tuple[int, int] | str | None:
    """Return the first and last byte of a single range, `"unsatisfiable"` or `None`.

    Malformed and multiple ranges are ignored, so the whole file is sent, as RFC 9110
    allows.
    """
    match = BYTE_RANGE.fullmatch(header.strip()) if header else None
    if match is None or match.group(1) == match.group(2) == "":
        return None
    first, last = match.group(1), match.group(2)
    if first == "":
        suffix = int(last)
        if suffix == 0 or size == 0:
            return "unsatisfiable"
        return max(size - suffix, 0), size - 1
    start = int(first)
    end = size - 1 if last == "" else min(int(last), size - 1)
    if start >= size:
        return "unsatisfiable"
    if start > end:
        return None
    return start, end


def _error_response(err: OpenHexaError) -> Response:
    """A short plain-text error for a file that can no longer be read."""
    if isinstance(err, NotFoundError):
        status, message = 404, "This file no longer exists in OpenHEXA."
    elif isinstance(err, AuthenticationError | PermissionDeniedError):
        status, message = 403, "OpenHEXA no longer gives access to this file."
    else:
        status, message = 502, "OpenHEXA or its storage failed. Try again later."
    return PlainTextResponse(message, status_code=status, headers={"Cache-Control": "no-store"})


def _not_found() -> Response:
    return Response(status_code=404, headers={"Cache-Control": "no-store"})


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    """Decode unpadded URL-safe base64, raising `ValueError` on invalid input."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise ValueError("invalid base64")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
