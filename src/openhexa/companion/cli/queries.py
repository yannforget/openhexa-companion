"""`hexa queries` commands."""

from typing import TextIO

import click

from openhexa.companion import SavedQuery
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import format_time, json_option, print_json, print_table


@click.group()
def queries() -> None:
    """Read and save the SQL queries of the workspace's Data Studio.

    Only the queries shared with the workspace and your own private ones are visible.
    """


@queries.command("list")
@click.option("--search", help="Only list queries whose name or description contains this.")
@json_option
@pass_client
def list_queries(client: LazyClient, search: str | None, as_json: bool) -> None:
    """List the saved queries, most recently updated first."""
    items = client.get().saved_queries.list(search)
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
    """Print the SQL of a saved query, with its details on stderr."""
    item = client.get().saved_queries.get(slug)
    if as_json:
        print_json(item)
        return
    _describe(item)
    click.echo(item.sql.rstrip("\n"))


@queries.command()
@click.argument("name")
@click.argument("source", metavar="FILE", type=click.File("r", encoding="utf-8"))
@click.option("--description", help="What the query is for.")
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
    click.echo(f"Saved query {item.slug}", err=True)


def _describe(item: SavedQuery) -> None:
    visibility = "shared with the workspace" if item.shared else "private"
    click.echo(f"{item.name} ({item.slug}, {visibility})", err=True)
    click.echo(
        f"By {item.author or 'a deleted user'}, updated {format_time(item.updated_at)}",
        err=True,
    )
    if item.description:
        click.echo(item.description, err=True)
    click.echo(err=True)
