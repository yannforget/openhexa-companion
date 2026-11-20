"""Confirmation of commands that cannot be undone."""

import sys

import click

yes_option = click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")


def confirm(action: str, *, yes: bool) -> None:
    """Ask to confirm `action`, such as "Delete dataset 'cases'", unless `yes` is set.

    Without a terminal to answer in, as when an agent or a script runs the command,
    `--yes` is required instead.

    Raises:
        click.Abort: The person did not confirm.
        click.ClickException: There is no terminal and `yes` is not set.
    """
    if yes:
        return
    if not sys.stdin.isatty():
        raise click.ClickException(f"{action}? This cannot be undone. Add --yes to confirm.")
    click.confirm(f"{action}? This cannot be undone.", abort=True, err=True)
