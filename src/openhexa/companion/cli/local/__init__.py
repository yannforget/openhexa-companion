"""`hexa local` commands: the local copy of the workspace, and local runs."""

import click

from openhexa.companion.cli.local.copies import clean, pull, status
from openhexa.companion.cli.local.run import run


@click.group()
def local() -> None:
    """Run pipelines on this machine, with a local copy of the workspace.

    The local copy is in .hexa/workspace/ of the current directory, so run these
    commands from the project root. It holds workspace files, dataset versions and
    connections, and mirrors their names: an ITEM is a path in it, such as
    files/data/cases.csv, datasets/population or connections/dhis2-prod.
    """


local.add_command(run)
local.add_command(pull)
local.add_command(status)
local.add_command(clean)
