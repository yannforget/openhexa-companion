"""`hexa database` commands."""

import click

from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import (
    cut,
    fetch_limit,
    json_option,
    limit_option,
    print_json,
    print_table,
)
from openhexa.companion.cli.connections import HIDDEN, show_secrets_option


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
