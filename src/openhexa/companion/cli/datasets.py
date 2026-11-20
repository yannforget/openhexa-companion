"""`hexa datasets` commands."""

from pathlib import Path

import click

from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._local import check_targets, local_path, save_stream, upload_pairs
from openhexa.companion.cli._output import (
    format_size,
    format_time,
    json_option,
    print_json,
    print_table,
)
from openhexa.companion.cli._prompt import confirm, yes_option

dataset_argument = click.argument("dataset")
version_option = click.option(
    "--version", "version_name", help="Version name. Defaults to the latest version."
)


@click.group()
def datasets() -> None:
    """Read and publish datasets.

    DATASET is a dataset slug, or WORKSPACE/SLUG for a dataset shared from another
    workspace.
    """


@datasets.command("list")
@json_option
@pass_client
def list_datasets(client: LazyClient, as_json: bool) -> None:
    """List the datasets of the workspace, including those shared with it."""
    workspace = client.get().workspace
    items = client.get().datasets.list()
    if as_json:
        print_json(items)
        return
    print_table(
        [
            (d.slug if d.workspace == workspace else d.reference, d.latest_version, d.name)
            for d in items
        ],
        ("dataset", "latest version", "name"),
    )


@datasets.command()
@dataset_argument
@json_option
@pass_client
def show(client: LazyClient, dataset: str, as_json: bool) -> None:
    """Show a dataset and its description."""
    item = client.get().datasets.get(dataset)
    if as_json:
        print_json(item)
        return
    click.echo(f"{item.name} ({item.reference})")
    click.echo(f"Latest version: {item.latest_version or 'none'}")
    if item.description:
        click.echo()
        click.echo(item.description)


@datasets.command()
@dataset_argument
@json_option
@pass_client
def versions(client: LazyClient, dataset: str, as_json: bool) -> None:
    """List the versions of a dataset, newest first."""
    items = client.get().datasets.versions(dataset)
    if as_json:
        print_json(items)
        return
    print_table(
        [(v.name, format_time(v.created_at), v.changelog or "") for v in items],
        ("version", "created", "changelog"),
    )


@datasets.command()
@dataset_argument
@version_option
@json_option
@pass_client
def files(client: LazyClient, dataset: str, version_name: str | None, as_json: bool) -> None:
    """List the files of a dataset version."""
    library = client.get()
    version = library.datasets.version(dataset, version_name)
    items = library.datasets.files(version)
    if as_json:
        print_json({"version": version, "files": items})
        return
    click.echo(f"Version {version.name}", err=True)
    print_table(
        [(f.path, format_size(f.size), f.content_type) for f in items],
        ("path", "size", "content type"),
    )


@datasets.command()
@dataset_argument
@click.argument("paths", nargs=-1)
@version_option
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    default=".",
    show_default=True,
    help="Directory to save the files in, keeping their paths within the version.",
)
@click.option("--force", is_flag=True, help="Replace local files that already exist.")
@pass_client
def download(
    client: LazyClient,
    *,
    dataset: str,
    paths: tuple[str, ...],
    version_name: str | None,
    output: Path,
    force: bool,
) -> None:
    """Download files of a dataset version: those at PATHS, or all of them.

    Existing local files are left untouched unless --force is given; the command stops
    before downloading anything if one exists.
    """
    library = client.get()
    version = library.datasets.version(dataset, version_name)
    if paths:
        selected = [library.datasets.file(version, path) for path in paths]
    else:
        selected = library.datasets.files(version)
    targets = [(file, local_path(output, file.path)) for file in selected]
    check_targets((target for _, target in targets), force=force)
    for file, target in targets:
        with library.datasets.read(file) as stream:
            save_stream(stream, target)
        click.echo(f"{target} ({format_size(file.size)})", err=True)
    click.echo(f"Downloaded {len(targets)} file(s) of version {version.name}", err=True)


@datasets.command()
@click.argument("name")
@click.option("--description", default="", help="Description of the dataset, in Markdown.")
@json_option
@pass_client
def create(client: LazyClient, name: str, description: str, as_json: bool) -> None:
    """Create an empty dataset named NAME in the workspace and print its slug."""
    item = client.get().datasets.create(name, description)
    if as_json:
        print_json(item)
        return
    click.echo(item.slug)


@datasets.command()
@dataset_argument
@click.argument("sources", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--version", "version_name", required=True, help="Name of the new version.")
@click.option("--changelog", help="What changed in this version.")
@json_option
@pass_client
def upload(
    client: LazyClient,
    *,
    dataset: str,
    sources: tuple[Path, ...],
    version_name: str,
    changelog: str | None,
    as_json: bool,
) -> None:
    """Publish SOURCES as a new version of a dataset.

    Files are added at the root of the version. The files in a directory keep their
    path relative to it; hidden files and directories, such as `.env`, are skipped.
    """
    uploads = upload_pairs(sources)
    library = client.get()
    version = library.datasets.create_version(dataset, version_name, changelog)
    uploaded = []
    for local, path in uploads:
        uploaded.append(library.datasets.upload(version, local, path))
        click.echo(f"{path} ({format_size(local.stat().st_size)})", err=True)
    if as_json:
        print_json({"version": version, "files": uploaded})
        return
    click.echo(f"Published version {version.name} with {len(uploaded)} file(s)", err=True)


@datasets.command()
@dataset_argument
@yes_option
@pass_client
def delete(client: LazyClient, dataset: str, yes: bool) -> None:
    """Delete a dataset with all its versions and files."""
    library = client.get()
    item = library.datasets.get(dataset)
    count = len(library.datasets.versions(dataset))
    confirm(f"Delete dataset '{item.reference}' and its {count} version(s)", yes=yes)
    library.datasets.delete(dataset)
    click.echo(f"Deleted dataset {item.reference}", err=True)
