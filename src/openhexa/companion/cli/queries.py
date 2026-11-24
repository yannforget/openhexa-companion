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
from openhexa.companion.cli._prompt import confirm, yes_option


@click.group()
def queries() -> None:
    """Read and save the SQL queries of the workspace's Data Studio.

    Only the queries shared with the workspace and your own private ones are visible.
    Run a query with `hexa database exec -- psql -c "$(hexa queries sql SLUG)"`.
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


@queries.command()
@click.argument("slug")
@click.option(
    "--sql",
    "source",
    type=click.File("r", encoding="utf-8"),
    help="Replace the SQL with the content of this file, or - for stdin.",
)
@click.option("--name", help="Rename the query. Its slug does not change.")
@click.option("-d", "--description", help="What the query is for.")
@click.option(
    "--shared/--private",
    default=None,
    help="Let every workspace member see the query, or only its author.",
)
@json_option
@pass_client
def update(
    client: LazyClient,
    *,
    slug: str,
    source: TextIO | None,
    name: str | None,
    description: str | None,
    shared: bool | None,
    as_json: bool,
) -> None:
    """Change a saved query: its SQL, name, description or visibility.

    OpenHEXA keeps the previous SQL in the query's history and does not check the new
    one: run it first. Only the author may change a private query or share and unshare
    a query; workspace editors and admins, and organization admins and owners, may also
    change a shared one. With --json, prints the query.
    """
    if source is None and name is None and description is None and shared is None:
        raise click.UsageError(
            "Give at least one of --sql, --name, --description, --shared or --private"
        )
    item = client.get().saved_queries.update(
        slug,
        sql=source.read() if source else None,
        name=name,
        description=description,
        shared=shared,
    )
    click.echo(f"Updated saved query {item.slug}", err=True)
    if as_json:
        print_json(item)


@queries.command()
@click.argument("slug")
@yes_option
@json_option
@pass_client
def delete(client: LazyClient, slug: str, yes: bool, as_json: bool) -> None:
    """Delete a saved query.

    Only the author may delete a private query; workspace editors and admins, and
    organization admins and owners, may also delete a shared one. With --json, prints the
    query deleted.
    """
    library = client.get()
    item = library.saved_queries.get(slug)
    confirm(f"Delete saved query '{item.slug}' ({item.name})", yes=yes)
    deleted = library.saved_queries.delete(item.slug)
    click.echo(f"Deleted saved query {deleted.slug}", err=True)
    if as_json:
        print_json(deleted)
