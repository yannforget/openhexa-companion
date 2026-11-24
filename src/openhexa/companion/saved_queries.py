"""SQL queries saved in the Data Studio of a workspace."""

import builtins
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openhexa.companion._transport import Transport
from openhexa.companion._util import format_options
from openhexa.companion.errors import NotFoundError
from openhexa.companion.workspace import workspace_not_found

SUMMARY_FIELDS = """
  slug
  name
  description
  visibility
  createdBy { displayName }
  updatedAt
"""

LIST_QUERY = f"""
query SavedQueries($workspace: String!, $search: String, $page: Int!, $perPage: Int!) {{
  workspace(slug: $workspace) {{
    savedQueries(query: $search, page: $page, perPage: $perPage) {{
      totalPages
      items {{ {SUMMARY_FIELDS} }}
    }}
  }}
}}
"""

GET_QUERY = f"""
query SavedQuery($slug: String!) {{
  savedQueryBySlug(slug: $slug) {{
    {SUMMARY_FIELDS}
    content
    workspace {{ slug }}
  }}
}}
"""

CREATE_MUTATION = f"""
mutation CreateSavedQuery($input: CreateSavedQueryInput!) {{
  createSavedQuery(input: $input) {{
    success
    errors
    savedQuery {{
      {SUMMARY_FIELDS}
      content
    }}
  }}
}}
"""


@dataclass(frozen=True)
class SavedQuerySummary:
    """A saved query, without its SQL.

    Attributes:
        slug: Identifies the query, unique across workspaces.
        name: Display name.
        description: What the query is for, if documented.
        shared: Whether every workspace member can see it, rather than only its author.
        author: Display name of the user who created it, if the account still exists.
        updated_at: When it was last changed.
    """

    slug: str
    name: str
    description: str
    shared: bool
    author: str | None
    updated_at: datetime


@dataclass(frozen=True)
class SavedQuery(SavedQuerySummary):
    """A saved query.

    Attributes:
        sql: The SQL of the query.
    """

    sql: str


class SavedQueries:
    """Reads and creates the saved queries of a workspace.

    Only the queries the token's user can see are visible: shared ones and their own.
    """

    def __init__(self, transport: Transport, workspace: str) -> None:
        self._transport = transport
        self._workspace = workspace

    def list(
        self, search: str | None = None, *, limit: int | None = None
    ) -> builtins.list[SavedQuerySummary]:
        """List the saved queries, most recently updated first.

        Args:
            search: Only list queries whose name or description contains it.
            limit: Maximum number of queries, or `None` for all of them.
        """

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            variables = {
                "workspace": self._workspace,
                "search": search,
                "page": page,
                "perPage": per_page,
            }
            data = self._transport.query(LIST_QUERY, variables)["workspace"]
            if data is None:
                raise workspace_not_found(self._transport, self._workspace)
            return data["savedQueries"]

        return [_summary(item) for item in self._transport.paginate(fetch_page, limit)]

    def get(self, slug: str) -> SavedQuery:
        """Get the saved query `slug`, with its SQL.

        Raises:
            NotFoundError: The workspace has no saved query `slug` that the token's user
                can see.
        """
        data = self._transport.query(GET_QUERY, {"slug": slug})["savedQueryBySlug"]
        # Slugs are unique across workspaces, so the lookup is not scoped to this one.
        if data is None or data["workspace"]["slug"] != self._workspace:
            slugs = (item.slug for item in self.list())
            raise NotFoundError(
                f"Saved query '{slug}' not found in workspace '{self._workspace}'. "
                f"Saved queries: {format_options(slugs)}"
            )
        return _saved_query(data)

    def create(
        self, name: str, sql: str, *, description: str | None = None, shared: bool = False
    ) -> SavedQuery:
        """Save a SQL query in the workspace. OpenHEXA does not check the SQL.

        OpenHEXA derives the slug from `name`, adding a suffix if it is already used.

        Args:
            name: Display name.
            sql: The SQL of the query.
            description: What the query is for.
            shared: Let every workspace member see the query, not only its author.

        Raises:
            PermissionDeniedError: The token may not create saved queries.
            APIError: OpenHEXA could not record the query's history (code
                `VERSIONING_UNAVAILABLE`); retrying is safe.
        """
        variables = {
            "input": {
                "workspaceSlug": self._workspace,
                "name": name,
                "content": sql,
                "description": description,
                "visibility": "WORKSPACE" if shared else "PRIVATE",
            }
        }
        result = self._transport.mutate(
            "createSavedQuery",
            CREATE_MUTATION,
            variables,
            action=f"save query '{name}' in workspace '{self._workspace}'",
            reasons={
                "PERMISSION_DENIED": "the token may not create saved queries",
                "WORKSPACE_NOT_FOUND": "the workspace does not exist or the token cannot access it",
                "VERSIONING_UNAVAILABLE": "OpenHEXA could not record the query's history; "
                "retrying is safe",
            },
        )
        return _saved_query(result["savedQuery"])


def _summary(data: dict[str, Any]) -> SavedQuerySummary:
    return SavedQuerySummary(**_summary_fields(data))


def _saved_query(data: dict[str, Any]) -> SavedQuery:
    return SavedQuery(**_summary_fields(data), sql=data["content"])


def _summary_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": data["slug"],
        "name": data["name"],
        "description": data["description"] or "",
        "shared": data["visibility"] == "WORKSPACE",
        "author": data["createdBy"]["displayName"] if data["createdBy"] else None,
        "updated_at": datetime.fromisoformat(data["updatedAt"]),
    }
