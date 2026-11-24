"""The `hexa` command-line interface."""

from pathlib import Path

import click

from openhexa.companion.cli._context import LazyClient
from openhexa.companion.cli._errors import Group
from openhexa.companion.cli.connections import connections
from openhexa.companion.cli.database import database
from openhexa.companion.cli.datasets import datasets
from openhexa.companion.cli.files import files
from openhexa.companion.cli.local import local
from openhexa.companion.cli.pipelines import pipelines
from openhexa.companion.cli.queries import queries
from openhexa.companion.cli.runs import runs
from openhexa.companion.cli.search import search
from openhexa.companion.cli.webapps import webapps
from openhexa.companion.cli.workspace import workspace


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
    """Work with an OpenHEXA workspace.

    Every command accepts --json, which HEXA_JSON=1 turns on; errors are then printed
    as {"error": {"type", "message"}} on stderr. Exit codes: 1 for an error, 2 for
    misuse, 3 for not found, 4 for permission denied or a rejected token, 5 for a run
    that did not succeed.
    """
    ctx.obj = LazyClient(env_file)
    ctx.call_on_close(ctx.obj.close)


main.add_command(connections)
main.add_command(database)
main.add_command(datasets)
main.add_command(files)
main.add_command(local)
main.add_command(pipelines)
main.add_command(queries)
main.add_command(runs)
main.add_command(search)
main.add_command(webapps)
main.add_command(workspace)
