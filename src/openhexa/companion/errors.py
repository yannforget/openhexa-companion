"""Exceptions raised by the library."""

from collections.abc import Sequence


class OpenHexaError(Exception):
    """Base class for errors raised when working with OpenHEXA.

    Attributes:
        codes: Error codes returned by an OpenHEXA mutation, if any.
    """

    def __init__(self, message: str, codes: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.codes = tuple(codes)


class AuthenticationError(OpenHexaError):
    """OpenHEXA rejected the token."""


class NotFoundError(OpenHexaError):
    """A resource does not exist, or the token cannot see it."""


class PermissionDeniedError(OpenHexaError):
    """The token may see a resource but not perform the operation."""


class APIError(OpenHexaError):
    """OpenHEXA or its storage failed or answered unexpectedly."""
