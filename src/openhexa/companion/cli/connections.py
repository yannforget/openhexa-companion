"""`hexa connections` commands."""

from typing import Any

import click

from openhexa.companion import Connection
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import (
    cut,
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
