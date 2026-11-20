"""Information about the workspace a client is scoped to."""

from dataclasses import dataclass
from typing import Any

from openhexa.companion._transport import Transport
from openhexa.companion.errors import InvalidInputError, NotFoundError

WORKSPACE_FIELDS = """
  slug
  name
  description
  configuration
  dockerImage
"""

WORKSPACE_QUERY = f"""
query Workspace($workspace: String!) {{
  workspace(slug: $workspace) {{ {WORKSPACE_FIELDS} }}
}}
"""

UPDATE_MUTATION = f"""
mutation UpdateWorkspace($input: UpdateWorkspaceInput!) {{
  updateWorkspace(input: $input) {{
    success
    errors
    workspace {{ {WORKSPACE_FIELDS} }}
  }}
}}
"""


@dataclass(frozen=True)
class Workspace:
    """An OpenHEXA workspace.

    Attributes:
        slug: Identifies the workspace in URLs and API calls.
        name: Display name.
        description: The workspace homepage, in Markdown.
        configuration: Environment variables given to pipelines and notebooks.
        docker_image: Image running pipelines and notebooks, or `None` for the default.
    """

    slug: str
    name: str
    description: str
    configuration: dict[str, Any]
    docker_image: str | None


def get_workspace(transport: Transport, slug: str) -> Workspace:
    """Read the workspace `slug`.

    Raises:
        NotFoundError: The workspace does not exist or the token cannot access it.
    """
    data = transport.query(WORKSPACE_QUERY, {"workspace": slug})["workspace"]
    if data is None:
        raise workspace_not_found(transport, slug)
    return _workspace(data)


def update_description(transport: Transport, slug: str, description: str) -> Workspace:
    """Replace the homepage of the workspace `slug` with the Markdown `description`.

    Raises:
        InvalidInputError: `description` is empty, which OpenHEXA would ignore.
        PermissionDeniedError: The token may not update the workspace.
    """
    if not description.strip():
        raise InvalidInputError("The workspace homepage cannot be empty")
    result = transport.mutate(
        "updateWorkspace",
        UPDATE_MUTATION,
        {"input": {"slug": slug, "description": description}},
        action=f"update the homepage of workspace '{slug}'",
        reasons={
            "NOT_FOUND": "the workspace does not exist or the token cannot access it",
            "PERMISSION_DENIED": "only workspace editors and admins may update the homepage",
        },
    )
    return _workspace(result["workspace"])


def _workspace(data: dict[str, Any]) -> Workspace:
    return Workspace(
        slug=data["slug"],
        name=data["name"],
        description=data["description"] or "",
        configuration=data["configuration"] or {},
        docker_image=data["dockerImage"] or None,
    )


def workspace_not_found(transport: Transport, slug: str) -> NotFoundError:
    return NotFoundError(
        f"Workspace '{slug}' not found on {transport.host}: it does not exist or the "
        "token does not give access to it"
    )
