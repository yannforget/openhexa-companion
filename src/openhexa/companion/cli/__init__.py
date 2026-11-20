"""The `hexa` command-line interface."""

from pathlib import Path

import click

from openhexa.companion import OpenHexaError
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import json_option, print_json
from openhexa.companion.cli.datasets import datasets


class Group(click.Group):
    """Turns library errors into a one-line message and exit code 1."""

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except OpenHexaError as err:
            raise click.ClickException(str(err)) from err


@click.group(cls=Group)
@click.version_option(package_name="openhexa-companion")
@click.option(
    "--env-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=".env",
    show_default=True,
    help="File with OPENHEXA_URL, OPENHEXA_WORKSPACE and OPENHEXA_TOKEN. Environment "
    "variables take precedence.",
)
@click.pass_context
def main(ctx: click.Context, env_file: Path) -> None:
    """Work with an OpenHEXA workspace."""
    ctx.obj = LazyClient(env_file)
    ctx.call_on_close(ctx.obj.close)


@main.command()
@json_option
@pass_client
def workspace(client: LazyClient, as_json: bool) -> None:
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


main.add_command(datasets)
