"""Tool finding datasets, pipelines, webapps, saved queries and database tables by keywords."""

from datetime import datetime
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from openhexa.companion import Catalog, Client, ItemType, SearchHit, SearchResult, search
from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import READ_ONLY, WorkspaceArg, join_notes

# Hits returned when no limit is given.
DEFAULT_LIMIT = 20
# Largest number of hits a search may ask for.
MAX_LIMIT = 100


class Hit(BaseModel):
    type: ItemType = Field(description="'dataset', 'pipeline', 'webapp', 'query' or 'table'.")
    reference: str = Field(description="Identifies the item in the tool for its type.")
    name: str = Field(description="Display name.")
    owner_workspace: str | None = Field(description="Owner of a shared dataset.")
    extract: str | None = Field(description="Matching part of the description.")
    updated_at: datetime | None = Field(description="When the item last changed.")


class SearchResults(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    hits: list[Hit] = Field(description="Matching items, best first.")
    matches: dict[ItemType, int] = Field(description="Matching items by type, all hits included.")
    searched: int = Field(description="Number of items searched.")
    note: str | None = Field(description="Types not searched, or hits cut off.")


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_search",
        title="Search an OpenHEXA workspace",
        annotations=READ_ONLY,
    )
    async def search_workspace(
        workspace: WorkspaceArg,
        query: Annotated[
            str,
            Field(
                description="Words to look for. An item matches if any word does, so one "
                "query can hold alternatives such as synonyms, abbreviations and "
                "translations: 'malaria paludisme palu'. Empty lists the most recently "
                "updated items."
            ),
        ] = "",
        types: Annotated[
            list[ItemType] | None,
            Field(description="Restricts the search to these types. Defaults to every type."),
        ] = None,
        limit: Annotated[
            int, Field(ge=1, le=MAX_LIMIT, description="Maximum number of hits.")
        ] = DEFAULT_LIMIT,
    ) -> SearchResults:
        """Find datasets, pipelines, webapps, saved queries and tables in an OpenHEXA workspace.

        Searches the names, slugs, descriptions and pipeline tags of the workspace's
        items, including datasets shared with it; tables are searched by name only.
        Words match the start of words, ignoring case and accents, but not misspellings.
        Hits are best first. A hit's reference is the dataset, pipeline, webapp, query
        or table argument of openhexa_get_dataset, openhexa_get_pipeline,
        openhexa_get_webapp, openhexa_get_saved_query or openhexa_get_database_table,
        depending on its type. owner_workspace is set for datasets shared from another
        workspace. extract is the part of the description around the matched words,
        which are between **. matches counts matching items by type, including those
        not returned.
        """

        def run(client: Client) -> tuple[Catalog, SearchResult]:
            catalog = client.catalog(types)
            return catalog, search(catalog.items, query, types, limit)

        catalog, result = await access.run(workspace, run)
        return SearchResults(
            workspace=workspace,
            hits=[_hit(hit) for hit in result.hits],
            matches=result.matches,
            searched=result.searched,
            note=join_notes(
                *(f"Could not search {kind}s: {error}." for kind, error in catalog.errors.items()),
                _cut_note(result),
            ),
        )


def _hit(hit: SearchHit) -> Hit:
    item = hit.item
    return Hit(
        type=item.type,
        reference=item.reference,
        name=item.name,
        owner_workspace=item.workspace if item.shared else None,
        extract=hit.extract,
        updated_at=item.updated_at,
    )


def _cut_note(result: SearchResult) -> str | None:
    total = sum(result.matches.values())
    if total <= len(result.hits):
        return None
    counts = ", ".join(f"{count} {kind}s" for kind, count in result.matches.items() if count)
    return (
        f"Showing {len(result.hits)} of {total} matches ({counts}). A narrower query, "
        "types or a higher limit returns more."
    )
