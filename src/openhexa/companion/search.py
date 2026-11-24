"""Finding datasets, pipelines, webapps, saved queries and database tables of a workspace."""

import re
import sqlite3
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, get_args

from openhexa.companion._transport import Transport
from openhexa.companion._util import format_options
from openhexa.companion.errors import APIError, InvalidInputError
from openhexa.companion.workspace import workspace_not_found

type ItemType = Literal["dataset", "pipeline", "webapp", "query", "table"]

# Every type of item, in the order results count them.
ITEM_TYPES: tuple[ItemType, ...] = get_args(ItemType.__value__)
# Items requested per list: OpenHEXA's maximum page size, so one page holds everything.
CATALOG_PAGE_SIZE = 10_000
# Mark the matched words in extracts; control characters cannot clash with the text.
MATCH_START, MATCH_END = "\x02", "\x03"
# Words of an identifier, splitting camelCase: `malariaCases2024` gives `malaria Cases2024`.
CAMEL_CASE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# A word, as SQLite's `unicode61` tokenizer sees it: letters and digits.
WORD = re.compile(r"[^\W_]+")

CATALOG_QUERY = """
query Catalog(
  $workspace: String!, $perPage: Int!,
  $datasets: Boolean!, $pipelines: Boolean!, $webapps: Boolean!, $queries: Boolean!
) {
  workspace(slug: $workspace) {
    slug
    datasets(perPage: $perPage) @include(if: $datasets) {
      items { dataset { id slug name description updatedAt workspace { slug } } }
    }
    savedQueries(perPage: $perPage) @include(if: $queries) {
      items { slug name description updatedAt }
    }
  }
  pipelines(workspaceSlug: $workspace, perPage: $perPage) @include(if: $pipelines) {
    items { id code name description tags { name } updatedAt }
  }
  webapps(workspaceSlug: $workspace, perPage: $perPage) @include(if: $webapps) {
    items { id slug name description }
  }
}
"""

# Indexes the searchable text of each item, the row ID being its position in the list.
CREATE_INDEX = """
create virtual table items using fts5(
  name, slug, tags, description, tokenize = 'unicode61 remove_diacritics 2'
)
"""

# Ranks the matching items with BM25, weighting name and slug 3, tags 2 and description 1,
# and extracts 16 tokens of the description around the matched words.
RANK = """
select rowid, bm25(items, 3, 3, 2, 1), snippet(items, 3, ?, ?, '…', 16)
from items where items match ?
"""

TABLES_QUERY = """
query CatalogTables($workspace: String!, $perPage: Int!) {
  workspace(slug: $workspace) {
    database {
      tables(perPage: $perPage, withColumns: false, withCounts: false) { items { name } }
    }
  }
}
"""


def _check_fts5() -> None:
    try:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("create virtual table probe using fts5(text)")
    except sqlite3.OperationalError as err:
        raise ImportError(
            f"Search needs SQLite with the FTS5 extension, which this Python's SQLite "
            f"{sqlite3.sqlite_version} lacks: {err}"
        ) from err


_check_fts5()


@dataclass(frozen=True)
class CatalogItem:
    """Something of the workspace that search can find.

    Attributes:
        type: `dataset`, `pipeline`, `webapp`, `query` or `table`.
        reference: Identifies the item in other operations: the slug of a dataset, or
            `workspace/slug` for one shared from another workspace; the code of a
            pipeline; the slug of a webapp or saved query; the name of a table.
        name: Display name.
        slug: Identifier within its workspace: slug, pipeline code or table name.
        description: Documentation of the item, in Markdown.
        tags: Tags of a pipeline.
        updated_at: When the item last changed, if OpenHEXA records it.
        workspace: Slug of the workspace that owns the item.
        shared: Whether the item is a dataset shared from another workspace.
    """

    type: ItemType
    reference: str
    name: str
    slug: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    updated_at: datetime | None = None
    workspace: str = ""
    shared: bool = False


@dataclass(frozen=True)
class Catalog:
    """The items of a workspace that search can find.

    Attributes:
        items: The items.
        errors: Why some types could not be listed, by type. Items of other types are
            complete.
    """

    items: list[CatalogItem]
    errors: dict[ItemType, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    """An item matching a search.

    Attributes:
        item: The item.
        extract: Part of the description around the matched words, which are between
            `**`, or `None` when the description does not match.
    """

    item: CatalogItem
    extract: str | None


@dataclass(frozen=True)
class SearchResult:
    """The result of a search.

    Attributes:
        hits: Best matching items first, at most the requested number.
        matches: Number of matching items by type, including those not in `hits`.
        searched: Number of items searched, of the requested types.
    """

    hits: list[SearchHit]
    matches: dict[ItemType, int]
    searched: int


def check_types(types: Iterable[str] | None) -> tuple[ItemType, ...]:
    """Return the requested types, or every type for `None`.

    Raises:
        InvalidInputError: A type is unknown.
    """
    if types is None:
        return ITEM_TYPES
    requested = set(types)
    unknown = sorted(requested.difference(ITEM_TYPES))
    if unknown:
        raise InvalidInputError(
            f"Unknown item types {format_options(unknown)}. Types: {format_options(ITEM_TYPES)}"
        )
    return tuple(t for t in ITEM_TYPES if t in requested)


def fetch_catalog(
    transport: Transport, workspace: str, types: Iterable[str] | None = None
) -> Catalog:
    """List the items of `workspace` of the given types, or of every type.

    Database tables are listed by a separate request, sent in parallel, so that a slow
    or failing workspace database only leaves tables out.

    Raises:
        InvalidInputError: A type is unknown.
        NotFoundError: The workspace does not exist or the token cannot access it.
    """
    wanted = check_types(types)
    variables: dict[str, Any] = {"workspace": workspace, "perPage": CATALOG_PAGE_SIZE}
    with ThreadPoolExecutor(max_workers=2) as executor:
        tables = (
            executor.submit(transport.query, TABLES_QUERY, variables) if "table" in wanted else None
        )
        flags = {f"{t}s": t in wanted for t in ("dataset", "pipeline", "webapp")}
        flags["queries"] = "query" in wanted
        data = transport.query(CATALOG_QUERY, variables | flags)
        if data["workspace"] is None:
            raise workspace_not_found(transport, workspace)
        items = _items(data, workspace)
        errors: dict[ItemType, str] = {}
        if tables is not None:
            try:
                table_data = tables.result()["workspace"]
            except APIError as err:
                errors["table"] = str(err)
            else:
                items += _tables(table_data, workspace)
    return Catalog(items=items, errors=errors)


def search(
    items: Sequence[CatalogItem],
    query: str,
    types: Iterable[str] | None = None,
    limit: int = 20,
) -> SearchResult:
    """Rank the `items` matching any word of `query`.

    Words match the start of words in names, slugs, tags and descriptions, ignoring
    case and accents; `camelCase` identifiers count as separate words. Items matching
    the whole query in their name or slug come first, then those matching every word,
    then the rest by relevance. Ties go to the workspace's own items, then to the most
    recently updated. Search syntax in `query`, such as quotes or `OR`, has no special
    meaning. An empty query returns the most recently updated items.

    Args:
        items: Items to search, such as those of `Client.catalog`.
        query: Words to look for. Any word may match, so several alternatives, such as
            synonyms or translations, can be given at once.
        types: Only search items of these types.
        limit: Maximum number of hits.

    Raises:
        InvalidInputError: A type is unknown or `limit` is below 1.
    """
    if limit < 1:
        raise InvalidInputError(f"Invalid limit {limit}: expected at least 1")
    wanted = check_types(types)
    candidates = [item for item in items if item.type in wanted]
    words = _words(query)
    if words:
        ranked = _rank(candidates, words)
    else:
        ranked = [SearchHit(item, None) for item in sorted(candidates, key=_recency)]
    counts = Counter(hit.item.type for hit in ranked)
    return SearchResult(
        hits=ranked[:limit],
        matches={t: counts[t] for t in wanted},
        searched=len(candidates),
    )


def _rank(items: Sequence[CatalogItem], words: list[str]) -> list[SearchHit]:
    """Find the items matching any of `words` with SQLite FTS5 and sort them."""
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute(CREATE_INDEX)
        connection.executemany(
            "insert into items (rowid, name, slug, tags, description) values (?, ?, ?, ?, ?)",
            [
                (index, item.name, _identifier(item.slug), " ".join(item.tags), item.description)
                for index, item in enumerate(items)
            ],
        )
        # Quoted words are plain text to FTS5, whatever the user typed.
        match = " OR ".join(f'"{word}"*' for word in dict.fromkeys(words))
        rows = connection.execute(RANK, (MATCH_START, MATCH_END, match)).fetchall()
    scored = []
    for index, bm25, snippet in rows:
        item = items[index]
        # Lower BM25 is better; tiers come first, so a full match beats a stronger partial one.
        key = (
            not _has_phrase(item, words),
            not _has_all_words(item, words),
            bm25,
            item.shared,
            *_recency(item),
        )
        extract = _extract(snippet) if MATCH_START in snippet else None
        scored.append((key, SearchHit(item, extract)))
    scored.sort(key=lambda pair: pair[0])
    return [hit for _, hit in scored]


def _recency(item: CatalogItem) -> tuple[bool, float]:
    """Sort key putting the most recently updated items first, and undated ones last."""
    if item.updated_at is None:
        return (True, 0.0)
    return (False, -item.updated_at.timestamp())


def _identifier(slug: str) -> str:
    """Index an identifier both whole and split at camelCase boundaries."""
    split = CAMEL_CASE.sub(" ", slug)
    return slug if split == slug else f"{slug} {split}"


def _words(text: str) -> list[str]:
    """Split `text` into lowercase words without accents, as the FTS5 tokenizer does."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return WORD.findall(plain.casefold())


def _has_all_words(item: CatalogItem, words: list[str]) -> bool:
    text = " ".join([item.name, _identifier(item.slug), *item.tags, item.description])
    tokens = set(_words(text))
    return all(any(token.startswith(word) for token in tokens) for word in words)


def _has_phrase(item: CatalogItem, words: list[str]) -> bool:
    """Whether the name or slug contains `words` in order, as whole words."""
    for text in (item.name, _identifier(item.slug)):
        tokens = _words(text)
        for start in range(len(tokens) - len(words) + 1):
            if tokens[start : start + len(words)] == words:
                return True
    return False


def _extract(snippet: str) -> str:
    text = " ".join(snippet.split())
    return text.replace(MATCH_START, "**").replace(MATCH_END, "**")


def _items(data: dict[str, Any], workspace: str) -> list[CatalogItem]:
    items = []
    links = data["workspace"].get("datasets")
    for link in links["items"] if links else []:
        dataset = link["dataset"]
        owner = dataset["workspace"]["slug"]
        shared = owner != workspace
        items.append(
            CatalogItem(
                type="dataset",
                reference=f"{owner}/{dataset['slug']}" if shared else dataset["slug"],
                name=dataset["name"],
                slug=dataset["slug"],
                description=dataset["description"] or "",
                updated_at=_datetime(dataset["updatedAt"]),
                workspace=owner,
                shared=shared,
            )
        )
    pipelines = data.get("pipelines")
    for pipeline in pipelines["items"] if pipelines else []:
        items.append(
            CatalogItem(
                type="pipeline",
                reference=pipeline["code"],
                name=pipeline["name"] or pipeline["code"],
                slug=pipeline["code"],
                description=pipeline["description"] or "",
                tags=[tag["name"] for tag in pipeline["tags"]],
                updated_at=_datetime(pipeline["updatedAt"]),
                workspace=workspace,
            )
        )
    webapps = data.get("webapps")
    for webapp in webapps["items"] if webapps else []:
        items.append(
            CatalogItem(
                type="webapp",
                reference=webapp["slug"],
                name=webapp["name"],
                slug=webapp["slug"],
                description=webapp["description"] or "",
                workspace=workspace,
            )
        )
    queries = data["workspace"].get("savedQueries")
    for query in queries["items"] if queries else []:
        items.append(
            CatalogItem(
                type="query",
                reference=query["slug"],
                name=query["name"],
                slug=query["slug"],
                description=query["description"] or "",
                updated_at=_datetime(query["updatedAt"]),
                workspace=workspace,
            )
        )
    return items


def _tables(data: dict[str, Any] | None, workspace: str) -> list[CatalogItem]:
    if data is None:
        return []
    return [
        CatalogItem(
            type="table",
            reference=table["name"],
            name=table["name"],
            slug=table["name"],
            workspace=workspace,
        )
        for table in data["database"]["tables"]["items"]
    ]


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
