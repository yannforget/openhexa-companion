"""`hexa search` command."""

import click

from openhexa.companion import search as search_items
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import (
    format_time,
    json_option,
    limit_option,
    nest,
    note,
    print_json,
    print_table,
)
from openhexa.companion.search import ITEM_TYPES


@click.command()
@click.argument("query", nargs=-1)
@click.option(
    "-t",
    "--type",
    "types",
    type=click.Choice(ITEM_TYPES),
    multiple=True,
    help="Only search this type. Repeat for several.",
)
@limit_option("matches", default=20)
@json_option
@pass_client
def search(
    client: LazyClient, query: tuple[str, ...], types: tuple[str, ...], limit: int, as_json: bool
) -> None:
    """Find datasets, pipelines, webapps, saved queries and tables matching any word of QUERY.

    Searches names, slugs, descriptions and pipeline tags, ignoring case and accents.
    Datasets shared with the workspace are included, and saved queries you can see;
    database tables are searched by name. Give
    alternatives at once, such as `malaria paludisme`. Without QUERY, lists the most
    recently updated items. With --json, prints the matching items, each with the
    `extract` that matched.
    """
    catalog = client.get().catalog(types or None)
    result = search_items(catalog.items, " ".join(query), types or None, limit)
    for kind, error in catalog.errors.items():
        click.echo(f"Could not search {kind}s: {error}", err=True)
    total = sum(result.matches.values())
    if total > len(result.hits):
        counts = ", ".join(f"{count} {kind}s" for kind, count in result.matches.items() if count)
        note(f"Showing {len(result.hits)} of {total} matches ({counts}); use -n to show more")
    elif not total:
        note(f"No match among {result.searched} items")
    if as_json:
        print_json([nest(hit.item, extract=hit.extract) for hit in result.hits])
        return
    print_table(
        [
            (
                hit.item.type,
                hit.item.reference,
                format_time(hit.item.updated_at) if hit.item.updated_at else "",
                hit.item.name,
            )
            for hit in result.hits
        ],
        ("type", "reference", "updated", "name"),
    )
