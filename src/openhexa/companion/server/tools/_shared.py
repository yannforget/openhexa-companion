"""Argument types and annotations shared by the tools."""

from typing import Annotated

from mcp_types import ToolAnnotations
from pydantic import Field

# Annotations of tools that only read from OpenHEXA.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

WorkspaceArg = Annotated[
    str,
    Field(description="Slug of the OpenHEXA workspace, as listed by openhexa_list_workspaces."),
]

DatasetArg = Annotated[
    str,
    Field(
        description="The dataset, as given by openhexa_list_datasets: its slug, or "
        "'workspace/slug' for a dataset shared from another workspace."
    ),
]

VersionArg = Annotated[
    str | None,
    Field(description="Name or ID of the dataset version. Defaults to the latest version."),
]
