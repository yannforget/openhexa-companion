"""`hexa search` command."""

import click

from openhexa.companion import search as search_items
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import format_time, json_option, print_json, print_table
from openhexa.companion.search import ITEM_TYPES


@click.command()
@click.argument("query", nargs=-1)
@click.option(
    "--type",
    "types",
    type=click.Choice(ITEM_TYPES),
    multiple=True,
    help="Only search this type. Repeat for several.",
)
@click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
@json_option
@pass_client
def search(
    client: LazyClient, query: tuple[str, ...], types: tuple[str, ...], limit: int, as_json: bool
) -> None:
    """Find datasets, pipelines, webapps and database tables matching any word of QUERY.

    Searches names, slugs, descriptions and pipeline tags, ignoring case and accents.
    Datasets shared with the workspace are included; tables are searched by name. Give
    alternatives at once, such as `malaria paludisme`. Without QUERY, lists the most
    recently updated items.
    """
    catalog = client.get().catalog(types or None)
    result = search_items(catalog.items, " ".join(query), types or None, limit)
    for kind, error in catalog.errors.items():
        click.echo(f"Could not search {kind}s: {error}", err=True)
    if as_json:
        print_json(result)
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
    total = sum(result.matches.values())
    if total > len(result.hits):
        counts = ", ".join(f"{count} {kind}s" for kind, count in result.matches.items() if count)
        click.echo(f"Showing {len(result.hits)} of {total} matches ({counts})", err=True)
    elif not total:
        click.echo(f"No match among {result.searched} items", err=True)
