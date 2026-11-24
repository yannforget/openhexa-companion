"""`hexa workspace` commands."""

from typing import TextIO

import click

from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import json_option, print_json


@click.group()
def workspace() -> None:
    """Read and edit the workspace configured in .env."""


@workspace.command()
@json_option
@pass_client
def show(client: LazyClient, as_json: bool) -> None:
    """Show the workspace: name, Docker image, configuration and homepage."""
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
@json_option
@pass_client
def homepage(client: LazyClient, as_json: bool) -> None:
    """Print the workspace homepage, in Markdown.

    The homepage documents the workspace for its members and for assistants. Change it
    with `hexa workspace update --homepage`. With --json, prints the workspace, whose
    `description` is the homepage.
    """
    info = client.get().get_workspace()
    if as_json:
        print_json(info)
        return
    # Printed as stored, so that saving it to a file and editing it back is exact.
    click.echo(info.description, nl=False)


@workspace.command()
@click.option(
    "--homepage",
    "source",
    required=True,
    type=click.File("r", encoding="utf-8"),
    help="Replace the homepage with the Markdown in this file, or - for stdin.",
)
@json_option
@pass_client
def update(client: LazyClient, source: TextIO, as_json: bool) -> None:
    """Change the workspace homepage.

    Only workspace editors and admins may change it. With --json, prints the workspace.
    """
    library = client.get()
    info = library.update_homepage(source.read())
    click.echo(f"Updated the homepage of workspace {library.workspace}", err=True)
    if as_json:
        print_json(info)
