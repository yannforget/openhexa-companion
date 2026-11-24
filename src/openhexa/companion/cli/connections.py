"""`hexa connections` and `hexa database` commands."""

from typing import Any

import click

from openhexa.companion import Connection
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import (
    cut,
    fetch_limit,
    json_option,
    limit_option,
    print_json,
    print_table,
)

# Shown instead of secret values that are not requested.
HIDDEN = "********"

show_secrets_option = click.option(
    "--show-secrets",
    is_flag=True,
    help="Print secret values. Only use on your own machine, never in shared logs.",
)


@click.group()
def connections() -> None:
    """Read the connections of the workspace."""


@connections.command("list")
@limit_option("connections")
@json_option
@pass_client
def list_connections(client: LazyClient, limit: int | None, as_json: bool) -> None:
    """List the connections of the workspace, with secret values hidden."""
    library = client.get()
    items = cut(
        library.connections.list(),
        limit,
        empty=f"No connections in workspace {library.workspace}",
        complete=True,
    )
    if as_json:
        print_json([connection_json(c, show_secrets=False) for c in items])
        return
    print_table([(c.slug, c.type, c.name) for c in items], ("connection", "type", "name"))


@connections.command()
@click.argument("slug")
@show_secrets_option
@json_option
@pass_client
def show(client: LazyClient, slug: str, show_secrets: bool, as_json: bool) -> None:
    """Show the fields of a connection, with secret values hidden by default."""
    connection = client.get().connections.get(slug)
    values = connection.values(include_secrets=show_secrets)
    if as_json:
        print_json(connection_json(connection, show_secrets=show_secrets))
        return
    click.echo(f"{connection.name} ({connection.slug}, {connection.type})")
    if connection.description:
        click.echo(connection.description)
    for item in connection.fields:
        value = values[item.code]
        if value is None:
            value = "(not readable with this token)" if show_secrets else HIDDEN
        click.echo(f"  {item.code}={value}")


def connection_json(connection: Connection, *, show_secrets: bool) -> dict[str, Any]:
    """The JSON of a connection, with `fields` mapping codes to values.

    Secret values are `null` unless `show_secrets` is set. `hexa run` reads this JSON
    as a local copy of the connection.
    """
    return {
        "slug": connection.slug,
        "name": connection.name,
        "type": connection.type,
        "description": connection.description,
        "fields": connection.values(include_secrets=show_secrets),
    }


@click.group()
def database() -> None:
    """Read the tables and credentials of the workspace database."""


@database.command()
@limit_option("tables")
@json_option
@pass_client
def tables(client: LazyClient, limit: int | None, as_json: bool) -> None:
    """List the tables of the workspace database."""
    items = cut(
        client.get().database.tables(limit=fetch_limit(limit)),
        limit,
        empty="The workspace database has no tables",
    )
    if as_json:
        print_json(items)
        return
    print_table([(t.name, t.rows) for t in items], ("table", "rows"))


@database.command()
@click.option("--read-only", is_flag=True, help="Credentials of a user that may only read.")
@show_secrets_option
@json_option
@pass_client
def credentials(client: LazyClient, read_only: bool, show_secrets: bool, as_json: bool) -> None:
    """Show how to connect to the workspace database, with the password hidden by default."""
    item = client.get().database.credentials(read_only=read_only)
    values = {
        "host": item.host,
        "port": item.port,
        "database": item.database,
        "username": item.username,
        "password": item.password if show_secrets else None,
        "url": item.url if show_secrets else None,
    }
    if as_json:
        print_json(values)
        return
    for key, value in values.items():
        click.echo(f"{key}={HIDDEN if value is None else value}")
