"""Argument types, annotations and helpers shared by the tools."""

import uuid
from typing import Annotated

from mcp_types import ToolAnnotations
from pydantic import Field

# Annotations of tools that only read from OpenHEXA.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
# Annotations of tools that overwrite or remove something in OpenHEXA. Repeating a call
# changes nothing more, so they are idempotent.
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
# Annotations of tools that add something to OpenHEXA, such as a run or a saved query.
# Each call adds another.
CREATE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)

# Items a list tool returns when no limit is given.
DEFAULT_LIST_LIMIT = 100

ListLimitArg = Annotated[int, Field(ge=1, description="Maximum number of items to return.")]

WorkspaceArg = Annotated[
    str,
    Field(description="Slug of the OpenHEXA workspace, as listed by openhexa_list_workspaces."),
]

DatasetArg = Annotated[
    str,
    Field(
        description="The dataset, as given by openhexa_list_datasets or openhexa_search: "
        "its slug, or 'workspace/slug' for a dataset shared from another workspace."
    ),
]

VersionArg = Annotated[
    str | None,
    Field(description="Name or ID of the dataset version. Defaults to the latest version."),
]

PipelineArg = Annotated[
    str,
    Field(
        description="Code of the pipeline, as given by openhexa_list_pipelines or openhexa_search."
    ),
]

RunArg = Annotated[
    str,
    Field(description="ID of the pipeline run, as listed by openhexa_list_pipeline_runs."),
]


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def truncation_note(total: int, limit: int, what: str, argument: str = "limit") -> str | None:
    """Explain that only `limit` of `total` items are shown, and which argument raises it."""
    if total <= limit:
        return None
    return f"Showing {limit} of {total} {what}. Call again with a higher {argument} to see more."


def join_notes(*notes: str | None) -> str | None:
    return " ".join(note for note in notes if note) or None
