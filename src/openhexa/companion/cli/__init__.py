"""The `hexa` command-line interface."""

from pathlib import Path

import click

from openhexa.companion import OpenHexaError
from openhexa.companion.cli._context import LazyClient
from openhexa.companion.cli.connections import connections, database
from openhexa.companion.cli.datasets import datasets
from openhexa.companion.cli.files import files
from openhexa.companion.cli.pipelines import pipelines
from openhexa.companion.cli.runs import runs
from openhexa.companion.cli.webapps import webapps
from openhexa.companion.cli.workspace import workspace


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


main.add_command(connections)
main.add_command(database)
main.add_command(datasets)
main.add_command(files)
main.add_command(pipelines)
main.add_command(runs)
main.add_command(webapps)
main.add_command(workspace)
