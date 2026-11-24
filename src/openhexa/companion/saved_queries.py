"""SQL queries saved in the Data Studio of a workspace."""

import builtins
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openhexa.companion._transport import Transport
from openhexa.companion._util import format_options
from openhexa.companion.errors import InvalidInputError, NotFoundError, PermissionDeniedError
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
    id
    permissions {{ update delete updateVisibility }}
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

UPDATE_MUTATION = f"""
mutation UpdateSavedQuery($input: UpdateSavedQueryInput!) {{
  updateSavedQuery(input: $input) {{
    success
    errors
    savedQuery {{
      {SUMMARY_FIELDS}
      content
    }}
  }}
}}
"""

DELETE_MUTATION = """
mutation DeleteSavedQuery($input: DeleteSavedQueryInput!) {
  deleteSavedQuery(input: $input) { success errors }
}
"""

# Explanations of the error codes of saved query mutations.
QUERY_ERRORS = {
    "SAVED_QUERY_NOT_FOUND": "the query does not exist or the token's user cannot see it",
    "VERSIONING_UNAVAILABLE": "OpenHEXA could not record the query's history; retrying is safe",
}
# Who may share or unshare a saved query, which OpenHEXA reports as PERMISSION_DENIED.
SHARE_RULE = "only its author may share or unshare a query"


def _who_may(verb: str) -> str:
    """Who may change or delete a saved query, which OpenHEXA reports as PERMISSION_DENIED."""
    return (
        f"only its author may {verb} a private query; a shared one, its author, workspace "
        "editors and admins, and organization admins and owners"
    )


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
    """Reads, creates, changes and deletes the saved queries of a workspace.

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
        return _saved_query(self._find(slug))

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

    def update(
        self,
        slug: str,
        *,
        sql: str | None = None,
        name: str | None = None,
        description: str | None = None,
        shared: bool | None = None,
    ) -> SavedQuery:
        """Change a saved query; arguments left to `None` are kept. The slug does not change.

        Args:
            slug: Slug of the query.
            sql: The new SQL. OpenHEXA records the previous SQL in the query's history.
            name: The new display name.
            description: What the query is for.
            shared: Let every workspace member see the query, or only its author.

        Raises:
            InvalidInputError: No change is given.
            NotFoundError: The workspace has no saved query `slug` that the token's user
                can see.
            PermissionDeniedError: The token's user may not make this change: only the
                author may change a private query or share and unshare a query; workspace
                editors and admins, and organization admins and owners, may also change a
                shared one.
            APIError: OpenHEXA could not record the query's history (code
                `VERSIONING_UNAVAILABLE`); retrying is safe.
        """
        changes = {"content": sql, "name": name, "description": description}
        changes = {key: value for key, value in changes.items() if value is not None}
        if shared is not None:
            changes["visibility"] = "WORKSPACE" if shared else "PRIVATE"
        if not changes:
            raise InvalidInputError(f"No change given for saved query '{slug}'")
        data = self._find(slug)
        action = f"update saved query '{slug}' in workspace '{self._workspace}'"
        permissions = data["permissions"]
        if not permissions["update"]:
            raise PermissionDeniedError(
                f"Could not {action}: {_who_may('change')}", ["PERMISSION_DENIED"]
            )
        # OpenHEXA only checks who may share when the visibility changes.
        sharing = changes.get("visibility", data["visibility"]) != data["visibility"]
        if sharing and not permissions["updateVisibility"]:
            raise PermissionDeniedError(f"Could not {action}: {SHARE_RULE}", ["PERMISSION_DENIED"])
        result = self._transport.mutate(
            "updateSavedQuery",
            UPDATE_MUTATION,
            {"input": {"id": data["id"], **changes}},
            action=action,
            reasons={**QUERY_ERRORS, "PERMISSION_DENIED": f"{_who_may('change')}; {SHARE_RULE}"},
        )
        return _saved_query(result["savedQuery"])

    def delete(self, slug: str) -> SavedQuery:
        """Delete a saved query and return it.

        Raises:
            NotFoundError: The workspace has no saved query `slug` that the token's user
                can see.
            PermissionDeniedError: The token's user may not delete it: only the author may
                delete a private query; workspace editors and admins, and organization
                admins and owners, may also delete a shared one.
        """
        data = self._find(slug)
        action = f"delete saved query '{slug}' in workspace '{self._workspace}'"
        rule = _who_may("delete")
        if not data["permissions"]["delete"]:
            raise PermissionDeniedError(f"Could not {action}: {rule}", ["PERMISSION_DENIED"])
        self._transport.mutate(
            "deleteSavedQuery",
            DELETE_MUTATION,
            {"input": {"id": data["id"]}},
            action=action,
            reasons={**QUERY_ERRORS, "PERMISSION_DENIED": rule},
        )
        return _saved_query(data)

    def _find(self, slug: str) -> dict[str, Any]:
        """Get the saved query `slug` of the workspace, with its ID and permissions."""
        data = self._transport.query(GET_QUERY, {"slug": slug})["savedQueryBySlug"]
        # Slugs are unique across workspaces, so the lookup is not scoped to this one.
        if data is None or data["workspace"]["slug"] != self._workspace:
            slugs = (item.slug for item in self.list())
            raise NotFoundError(
                f"Saved query '{slug}' not found in workspace '{self._workspace}'. "
                f"Saved queries: {format_options(slugs)}"
            )
        return data


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
