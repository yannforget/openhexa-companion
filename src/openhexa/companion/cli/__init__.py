"""The `hexa` command-line interface."""

import click


@click.group()
@click.version_option(package_name="openhexa-companion")
def main() -> None:
    """Work with an OpenHEXA workspace."""
