"""HTTP access to the OpenHEXA GraphQL API and to the storage behind signed URLs."""

from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx2

from openhexa.companion.errors import (
    APIError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
)

# Bytes per chunk when streaming downloads.
CHUNK_SIZE = 1024 * 1024
# Items requested per page when walking paginated lists.
PAGE_SIZE = 100


class ByteStream:
    """The bytes of a file being downloaded, exactly as stored."""

    def __init__(self, response: httpx2.Response) -> None:
        self._response = response

    def __iter__(self) -> Iterator[bytes]:
        return self._response.iter_raw(CHUNK_SIZE)

    def read(self) -> bytes:
        """Read the remaining bytes at once."""
        return b"".join(self)


class Transport:
    """Sends GraphQL requests to one OpenHEXA instance and transfers files to its storage.

    Storage requests use a separate HTTP client so that the token never reaches signed
    URLs. Neither client follows redirects, which could forward the token elsewhere.
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = 60,
        http_transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.host = httpx2.URL(self.url).host or self.url
        self._api = httpx2.Client(
            base_url=self.url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=http_transport,
        )
        self._storage = httpx2.Client(timeout=timeout, transport=http_transport)

    def close(self) -> None:
        self._api.close()
        self._storage.close()

    def query(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Send a GraphQL request and return its `data`.

        Raises:
            AuthenticationError: OpenHEXA rejected the token.
            APIError: The request failed, or OpenHEXA returned GraphQL errors.
        """
        try:
            response = self._api.post(
                "/graphql/", json={"query": query, "variables": dict(variables or {})}
            )
        except httpx2.TransportError as err:
            raise APIError(f"Could not reach OpenHEXA at {self.host}: {err}") from err
        if response.status_code in {401, 403}:
            raise self._authentication_error()
        try:
            body = response.json()
        except ValueError:
            raise APIError(
                f"OpenHEXA at {self.host} returned HTTP {response.status_code} "
                "without a GraphQL response"
            ) from None
        errors = body.get("errors") or []
        if any(error.get("extensions", {}).get("code") == "UNAUTHENTICATED" for error in errors):
            raise self._authentication_error()
        if errors:
            messages = "; ".join(error.get("message", "unknown error") for error in errors)
            raise APIError(f"OpenHEXA at {self.host} rejected a request: {messages}")
        if response.status_code != 200 or body.get("data") is None:
            raise APIError(f"OpenHEXA at {self.host} returned HTTP {response.status_code}")
        return body["data"]

    def mutate(
        self,
        field: str,
        query: str,
        variables: Mapping[str, Any],
        *,
        action: str,
        reasons: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send a mutation and return its result, raising if OpenHEXA reports failure.

        OpenHEXA reports mutation failures as error codes in the result rather than as
        GraphQL errors. The raised exception keeps those codes.

        Args:
            field: Name of the mutation field holding the result.
            query: The GraphQL mutation.
            variables: Variables of the mutation.
            action: What the mutation does, completing "Could not ...".
            reasons: Explanations of error codes, used in the message instead of the code.

        Raises:
            NotFoundError: An error code ends with `NOT_FOUND`.
            PermissionDeniedError: The error codes include `PERMISSION_DENIED`.
            APIError: Any other error code.
        """
        result = self.query(query, variables)[field]
        if result["success"]:
            return result
        codes: list[str] = result.get("errors") or []
        explanations = [(reasons or {}).get(code, code) for code in codes]
        message = f"Could not {action}: {'; '.join(explanations) or 'no reason given'}"
        if "PERMISSION_DENIED" in codes:
            raise PermissionDeniedError(message, codes)
        if any(code.endswith("NOT_FOUND") for code in codes):
            raise NotFoundError(message, codes)
        raise APIError(message, codes)

    @staticmethod
    def paginate(fetch_page: Callable[[int, int], Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
        """Yield the items of every page.

        Args:
            fetch_page: Returns the page object (`items` and `totalPages`) for a page
                number and page size.
        """
        page = 1
        while True:
            result = fetch_page(page, PAGE_SIZE)
            yield from result["items"]
            if page >= result["totalPages"]:
                return
            page += 1

    @contextmanager
    def download(
        self, url: str, *, what: str, offset: int = 0, length: int | None = None
    ) -> Generator[ByteStream]:
        """Stream a signed storage URL, or the byte range starting at `offset`.

        Errors never include the URL, which grants access to the file.

        Args:
            url: The signed URL.
            what: Describes the file in error messages.
            offset: Index of the first byte to read.
            length: Number of bytes to read, or `None` to read to the end.

        Raises:
            APIError: Storage failed or did not honour the byte range.
        """
        headers = {}
        if offset or length is not None:
            end = "" if length is None else offset + length - 1
            headers["Range"] = f"bytes={offset}-{end}"
        try:
            with self._storage.stream("GET", url, headers=headers) as response:
                if response.status_code not in {200, 206}:
                    raise APIError(f"Storage returned HTTP {response.status_code} for {what}")
                if headers and response.status_code != 206:
                    raise APIError(f"Storage does not support reading part of {what}")
                yield ByteStream(response)
        except httpx2.TransportError as err:
            # Chaining would carry the request, and therefore the signed URL.
            raise APIError(f"Could not read {what} from storage: {type(err).__name__}") from None

    def upload(
        self, url: str, source: Path | bytes, *, what: str, headers: Mapping[str, str]
    ) -> None:
        """Upload `source` to a signed storage URL with a PUT request.

        Raises:
            APIError: Storage failed or rejected the upload.
        """
        try:
            if isinstance(source, Path):
                with source.open("rb") as content:
                    response = self._storage.put(url, content=content, headers=headers)
            else:
                response = self._storage.put(url, content=source, headers=headers)
        except httpx2.TransportError as err:
            # Chaining would carry the request, and therefore the signed URL.
            raise APIError(f"Could not upload {what} to storage: {type(err).__name__}") from None
        if response.is_error:
            raise APIError(f"Storage returned HTTP {response.status_code} for {what}")

    def _authentication_error(self) -> AuthenticationError:
        return AuthenticationError(
            f"OpenHEXA at {self.host} rejected the token: it may be invalid, expired or revoked"
        )
