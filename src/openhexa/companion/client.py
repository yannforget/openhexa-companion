"""Client for one OpenHEXA workspace."""

from collections.abc import Iterable
from types import TracebackType
from typing import Self

import httpx2

from openhexa.companion._transport import Transport
from openhexa.companion.connections import Connections
from openhexa.companion.database import Database
from openhexa.companion.datasets import Datasets
from openhexa.companion.files import Files
from openhexa.companion.pipelines import Pipelines
from openhexa.companion.runs import Runs
from openhexa.companion.saved_queries import SavedQueries
from openhexa.companion.search import Catalog, fetch_catalog
from openhexa.companion.user import User, get_user
from openhexa.companion.webapps import Webapps
from openhexa.companion.workspace import Workspace, get_workspace, update_description


class Client:
    """Works with one OpenHEXA workspace, using a token that gives access to it.

    Close the client when done, or use it as a context manager.

    Example:
        ```python
        with Client("https://app.openhexa.org", "my-workspace", token) as client:
            for dataset in client.datasets.list():
                print(dataset.name)
        ```

    Attributes:
        url: Base URL of the OpenHEXA instance.
        workspace: Slug of the workspace.
        datasets: Datasets of the workspace.
        files: Files of the workspace, shared by its pipelines and notebooks.
        connections: Connections of the workspace, including secret values.
        database: The workspace database.
        saved_queries: SQL queries saved in the Data Studio of the workspace.
        pipelines: Pipelines of the workspace.
        runs: Runs of the pipelines of the workspace.
        webapps: Webapps of the workspace.
    """

    def __init__(
        self,
        url: str,
        workspace: str,
        token: str,
        *,
        timeout: float = 60,
        http_transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Create a client. No request is sent until an operation needs one.

        Args:
            url: Base URL of the OpenHEXA instance, such as `https://app.openhexa.org`.
            workspace: Slug of the workspace.
            token: An OpenHEXA token with access to the workspace.
            timeout: Seconds to wait for each network operation.
            http_transport: Replaces the HTTP transport, for tests.
        """
        self._transport = Transport(url, token, timeout=timeout, http_transport=http_transport)
        self.url = self._transport.url
        self.workspace = workspace
        self.datasets = Datasets(self._transport, workspace)
        self.files = Files(self._transport, workspace)
        self.connections = Connections(self._transport, workspace)
        self.database = Database(self._transport, workspace)
        self.saved_queries = SavedQueries(self._transport, workspace)
        self.pipelines = Pipelines(self._transport, workspace)
        self.runs = Runs(self._transport, workspace, self.pipelines)
        self.webapps = Webapps(self._transport, workspace)

    def get_workspace(self) -> Workspace:
        """Read the workspace.

        Raises:
            NotFoundError: The workspace does not exist or the token cannot access it.
        """
        return get_workspace(self._transport, self.workspace)

    def get_user(self) -> User:
        """Read the OpenHEXA user the token belongs to.

        A workspace token is issued to a user, so this identifies who holds it.
        """
        return get_user(self._transport)

    def update_homepage(self, content: str) -> Workspace:
        """Replace the workspace homepage, its description in Markdown.

        Raises:
            InvalidInputError: `content` is empty, which OpenHEXA would ignore.
            PermissionDeniedError: The token may not update the workspace: only
                workspace editors and admins can.
        """
        return update_description(self._transport, self.workspace, content)

    def catalog(self, types: Iterable[str] | None = None) -> Catalog:
        """List the datasets, pipelines, webapps and database tables of the workspace.

        Pass the items to `search` to find those matching keywords. Datasets include
        those shared with the workspace. Tables are only named; if the workspace database
        cannot be read, the other items are returned and `errors` says why.

        Args:
            types: Only list items of these types: `dataset`, `pipeline`, `webapp` or
                `table`.

        Raises:
            InvalidInputError: A type is unknown.
            NotFoundError: The workspace does not exist or the token cannot access it.
        """
        return fetch_catalog(self._transport, self.workspace, types)

    def close(self) -> None:
        """Close the HTTP connections."""
        self._transport.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
