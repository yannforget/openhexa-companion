"""Python library for working with OpenHEXA workspaces."""

from openhexa.companion._transport import ByteStream
from openhexa.companion.client import Client
from openhexa.companion.datasets import Dataset, DatasetFile, DatasetVersion
from openhexa.companion.errors import (
    APIError,
    AuthenticationError,
    NotFoundError,
    OpenHexaError,
    PermissionDeniedError,
)
from openhexa.companion.workspace import Workspace

__all__ = [
    "APIError",
    "AuthenticationError",
    "ByteStream",
    "Client",
    "Dataset",
    "DatasetFile",
    "DatasetVersion",
    "NotFoundError",
    "OpenHexaError",
    "PermissionDeniedError",
    "Workspace",
]
