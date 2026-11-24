"""`hexa database` commands."""

import os
import signal
import subprocess

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
    """Read the tables and credentials of the workspace database.

    Query it with ordinary clients through `hexa database exec`. Only workspace editors
    and admins, and organization admins and owners, get the credentials.
    """


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


# Options after the first word of COMMAND, such as `psql --help`, belong to COMMAND.
@database.command(
    "exec", context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False}
)
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
@click.option("--write", is_flag=True, help="Connect as the user that may also write.")
@json_option
@pass_client
def exec_command(client: LazyClient, command: tuple[str, ...], write: bool, as_json: bool) -> None:
    """Run COMMAND connected to the workspace database, and exit with its exit code.

    COMMAND gets PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD and DATABASE_URL, which
    psql, most PostgreSQL drivers and many tools read, so the password never has to be
    shown. Put `--` before COMMAND, for example `hexa database exec -- psql -c "select
    count(*) from cases"` or `hexa database exec -- python analyse.py`.

    COMMAND connects as the read-only user unless --write is given. Only workspace
    editors and admins, and organization admins and owners, get the credentials. With
    --json, only errors are printed as JSON: COMMAND prints what it prints.
    """
    del as_json  # COMMAND prints its own output; the root command prints errors as JSON.
    item = client.get().database.credentials(read_only=not write)
    env = os.environ | {
        "PGHOST": item.host,
        "PGPORT": str(item.port),
        "PGDATABASE": item.database,
        "PGUSER": item.username,
        "PGPASSWORD": item.password,
        "DATABASE_URL": item.url,
    }
    # Ctrl+C reaches COMMAND, which decides what to do: psql cancels the query and goes
    # on. Ignoring it here keeps the CLI from killing COMMAND or exiting before it.
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        process = subprocess.Popen(command, env=env)
    except FileNotFoundError:
        raise click.ClickException(f"Command not found: {command[0]}") from None
    except PermissionError:
        raise click.ClickException(f"Command is not executable: {command[0]}") from None
    else:
        code = process.wait()
    finally:
        signal.signal(signal.SIGINT, previous)
    # A command killed by a signal exits like it would in a shell.
    raise click.exceptions.Exit(128 - code if code < 0 else code)
