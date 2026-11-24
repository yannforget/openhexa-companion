"""`hexa queries` commands."""

from typing import TextIO

import click

from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import (
    cut,
    fetch_limit,
    format_time,
    json_option,
    limit_option,
    print_json,
    print_table,
)


@click.group()
def queries() -> None:
    """Read and save the SQL queries of the workspace's Data Studio.

    Only the queries shared with the workspace and your own private ones are visible.
    """


@queries.command("list")
@limit_option("queries")
@json_option
@pass_client
def list_queries(client: LazyClient, limit: int | None, as_json: bool) -> None:
    """List the saved queries, most recently updated first.

    Find queries by keywords with `hexa search -t query`.
    """
    library = client.get()
    items = cut(
        library.saved_queries.list(limit=fetch_limit(limit)),
        limit,
        empty=f"No saved queries in workspace {library.workspace}",
    )
    if as_json:
        print_json(items)
        return
    print_table(
        [
            (q.slug, "shared" if q.shared else "private", format_time(q.updated_at), q.name)
            for q in items
        ],
        ("query", "visibility", "updated", "name"),
    )


@queries.command()
@click.argument("slug")
@json_option
@pass_client
def show(client: LazyClient, slug: str, as_json: bool) -> None:
    """Show a saved query: its details, then its SQL.

    Use `hexa queries sql` to print only the SQL.
    """
    item = client.get().saved_queries.get(slug)
    if as_json:
        print_json(item)
        return
    visibility = "shared with the workspace" if item.shared else "private"
    click.echo(f"{item.name} ({item.slug}, {visibility})")
    click.echo(f"By {item.author or 'a deleted user'}, updated {format_time(item.updated_at)}")
    if item.description:
        click.echo(item.description)
    click.echo()
    click.echo(item.sql.rstrip("\n"))


@queries.command()
@click.argument("slug")
@json_option
@pass_client
def sql(client: LazyClient, slug: str, as_json: bool) -> None:
    """Print only the SQL of a saved query, to pipe or save it.

    With --json, prints the query, whose `sql` is the SQL.
    """
    item = client.get().saved_queries.get(slug)
    if as_json:
        print_json(item)
        return
    # Printed as stored, so that saving it to a file is exact.
    click.echo(item.sql, nl=False)


@queries.command()
@click.argument("name")
@click.argument("source", metavar="FILE", type=click.File("r", encoding="utf-8"))
@click.option("-d", "--description", help="What the query is for.")
@click.option("--shared", is_flag=True, help="Let every workspace member see the query.")
@json_option
@pass_client
def create(
    client: LazyClient,
    *,
    name: str,
    source: TextIO,
    description: str | None,
    shared: bool,
    as_json: bool,
) -> None:
    """Save the SQL in FILE, or - for stdin, as a query named NAME.

    OpenHEXA does not check the SQL: run it first. The query is private unless --shared.
    """
    item = client.get().saved_queries.create(
        name, source.read(), description=description, shared=shared
    )
    if as_json:
        print_json(item)
        return
    click.echo(f"Saved query {item.name}", err=True)
    click.echo(item.slug)
