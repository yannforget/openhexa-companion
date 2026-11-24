"""`hexa workspace` commands."""

from typing import TextIO

import click

from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import json_option, print_json


@click.group(invoke_without_command=True)
@json_option
@click.pass_context
@pass_client
def workspace(client: LazyClient, ctx: click.Context, as_json: bool) -> None:
    """Show the workspace: name, Docker image, configuration and homepage."""
    if ctx.invoked_subcommand is not None:
        return
    info = client.get().get_workspace()
    if as_json:
        print_json(info)
        return
    click.echo(f"{info.name} ({info.slug})")
    click.echo(f"Docker image: {info.docker_image or 'default'}")
    if info.configuration:
        click.echo("Configuration:")
        for key, value in info.configuration.items():
            click.echo(f"  {key}={value}")
    if info.description:
        click.echo()
        click.echo(info.description)


@workspace.command()
@click.option(
    "--edit",
    "source",
    type=click.File("r", encoding="utf-8"),
    help="Replace the homepage with the Markdown in this file, or - for stdin.",
)
@json_option
@pass_client
def homepage(client: LazyClient, source: TextIO | None, as_json: bool) -> None:
    """Print the workspace homepage in Markdown, or replace it with --edit.

    The homepage documents the workspace for its members and for assistants. Only
    workspace editors and admins may replace it. With --json, prints the workspace,
    whose `description` is the homepage.
    """
    library = client.get()
    if source is None:
        info = library.get_workspace()
        if as_json:
            print_json(info)
            return
        # Printed as stored, so that saving it to a file and editing it back is exact.
        click.echo(info.description, nl=False)
        return
    info = library.update_homepage(source.read())
    click.echo(f"Updated the homepage of workspace {library.workspace}", err=True)
    if as_json:
        print_json(info)
