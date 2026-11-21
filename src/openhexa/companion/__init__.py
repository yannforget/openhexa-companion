"""Python library for working with OpenHEXA workspaces."""

from openhexa.companion._transport import ByteStream
from openhexa.companion.client import Client
from openhexa.companion.connections import Connection, ConnectionField
from openhexa.companion.database import DatabaseCredentials, DatabaseTable
from openhexa.companion.datasets import Dataset, DatasetFile, DatasetVersion
from openhexa.companion.errors import (
    APIError,
    AuthenticationError,
    InvalidInputError,
    NotFoundError,
    OpenHexaError,
    PermissionDeniedError,
)
from openhexa.companion.files import WorkspaceFile
from openhexa.companion.pipelines import Pipeline, PipelineParameter, PipelineVersion
from openhexa.companion.runs import Run, RunDatasetVersion, RunDetails, RunMessage, RunOutput
from openhexa.companion.user import User
from openhexa.companion.webapps import Publication, Webapp, WebappFile, WebappVersion
from openhexa.companion.workspace import Workspace

__all__ = [
    "APIError",
    "AuthenticationError",
    "ByteStream",
    "Client",
    "Connection",
    "ConnectionField",
    "DatabaseCredentials",
    "DatabaseTable",
    "Dataset",
    "DatasetFile",
    "DatasetVersion",
    "InvalidInputError",
    "NotFoundError",
    "OpenHexaError",
    "PermissionDeniedError",
    "Pipeline",
    "PipelineParameter",
    "PipelineVersion",
    "Publication",
    "Run",
    "RunDatasetVersion",
    "RunDetails",
    "RunMessage",
    "RunOutput",
    "User",
    "Webapp",
    "WebappFile",
    "WebappVersion",
    "Workspace",
    "WorkspaceFile",
]
