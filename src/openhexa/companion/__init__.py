"""Python library for working with OpenHEXA workspaces."""

from openhexa.companion._transport import ByteStream
from openhexa.companion.client import Client
from openhexa.companion.connections import Connection, ConnectionField
from openhexa.companion.database import (
    DatabaseCredentials,
    DatabaseTable,
    QueryResult,
    TableColumn,
)
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
from openhexa.companion.pipelines import Pipeline, PipelineParameter, PipelineVersion, SourceFile
from openhexa.companion.runs import Run, RunDatasetVersion, RunDetails, RunMessage, RunOutput
from openhexa.companion.saved_queries import SavedQuery, SavedQuerySummary
from openhexa.companion.search import (
    Catalog,
    CatalogItem,
    ItemType,
    SearchHit,
    SearchResult,
    search,
)
from openhexa.companion.user import User
from openhexa.companion.webapps import Publication, Webapp, WebappFile, WebappVersion
from openhexa.companion.workspace import Workspace

__all__ = [
    "APIError",
    "AuthenticationError",
    "ByteStream",
    "Catalog",
    "CatalogItem",
    "Client",
    "Connection",
    "ConnectionField",
    "DatabaseCredentials",
    "DatabaseTable",
    "Dataset",
    "DatasetFile",
    "DatasetVersion",
    "InvalidInputError",
    "ItemType",
    "NotFoundError",
    "OpenHexaError",
    "PermissionDeniedError",
    "Pipeline",
    "PipelineParameter",
    "PipelineVersion",
    "Publication",
    "QueryResult",
    "Run",
    "RunDatasetVersion",
    "RunDetails",
    "RunMessage",
    "RunOutput",
    "SavedQuery",
    "SavedQuerySummary",
    "SearchHit",
    "SearchResult",
    "SourceFile",
    "TableColumn",
    "User",
    "Webapp",
    "WebappFile",
    "WebappVersion",
    "Workspace",
    "WorkspaceFile",
    "search",
]
