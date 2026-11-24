"""`hexa datasets` commands."""

import json
from pathlib import Path
from typing import Any

import click

from openhexa.companion import Client
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._local import (
    check_targets,
    local_path,
    save_stream,
    upload_pairs,
)
from openhexa.companion.cli._output import (
    cut,
    fetch_limit,
    format_size,
    format_time,
    json_option,
    limit_option,
    nest,
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
@limit_option("datasets")
@json_option
@pass_client
def list_datasets(client: LazyClient, limit: int | None, as_json: bool) -> None:
    """List the datasets of the workspace, including those shared with it."""
    workspace = client.get().workspace
    items = cut(
        client.get().datasets.list(limit=fetch_limit(limit)),
        limit,
        empty=f"No datasets in workspace {workspace}",
    )
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
    if item.attributes:
        click.echo("Attributes:")
        for key, value in item.attributes.items():
            click.echo(f"  {key}={json.dumps(value, ensure_ascii=False)}")
    if item.description:
        click.echo()
        click.echo(item.description)


@datasets.command()
@dataset_argument
@version_option
@limit_option("files")
@json_option
@pass_client
def files(
    client: LazyClient, dataset: str, version_name: str | None, limit: int | None, as_json: bool
) -> None:
    """List the files of a dataset version.

    Each file names its version, also printed on stderr.
    """
    library = client.get()
    version = library.datasets.version(dataset, version_name)
    click.echo(f"Version {version.name}", err=True)
    items = cut(
        library.datasets.files(version, limit=fetch_limit(limit)),
        limit,
        empty=f"Version {version.name} of dataset {dataset} has no files",
    )
    if as_json:
        print_json(items)
        return
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
    default=Path(),
    help="Directory to save the files in, keeping their paths within the version. "
    "Defaults to the current directory.",
)
@click.option("--force", is_flag=True, help="Replace local files that already exist.")
@json_option
@pass_client
def download(
    client: LazyClient,
    *,
    dataset: str,
    paths: tuple[str, ...],
    version_name: str | None,
    output: Path,
    force: bool,
    as_json: bool,
) -> None:
    """Download files of a dataset version: those at PATHS, or all of them.

    Files keep their paths within the version. To copy a version for `hexa local run`,
    use `hexa local pull datasets/DATASET[@VERSION]`. Existing local files are left
    untouched unless --force is given; the command stops before downloading anything if
    one exists. With --json, prints the files written, with their `local_path`.
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
    click.echo(f"Downloaded {len(targets)} file(s) of version {version.name} to {output}", err=True)
    if as_json:
        print_json([nest(file, local_path=str(target)) for file, target in targets])


description_option = click.option(
    "-d", "--description", help="Description of the dataset, in Markdown."
)
message_option = click.option("-m", "--message", help="What changed in this version.")


@datasets.command()
@click.argument("name")
@click.argument("sources", nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--name",
    "version_name",
    help="Name of the first version, when SOURCES are given. Defaults to v1.",
)
@description_option
@message_option
@json_option
@pass_client
def create(
    client: LazyClient,
    *,
    name: str,
    sources: tuple[Path, ...],
    version_name: str | None,
    description: str | None,
    message: str | None,
    as_json: bool,
) -> None:
    """Create a dataset named NAME and print its slug.

    With SOURCES, publishes them as its first version, as `hexa datasets publish` does;
    without them, the dataset is empty. With --json, prints the dataset, with the first
    version and its `files` as `version` when SOURCES are given.
    """
    if not sources and (version_name is not None or message is not None):
        raise click.UsageError("--name and --message apply to the first version: give SOURCES")
    uploads = upload_pairs(sources) if sources else []
    library = client.get()
    item = library.datasets.create(name, description or "")
    version = None
    if uploads:
        version = _publish(library, item.slug, uploads, version_name or "v1", message)
        item = library.datasets.get(item.slug)
    if as_json:
        print_json(item if version is None else nest(item, version=version))
        return
    click.echo(item.slug)


@datasets.command()
@dataset_argument
@click.argument("sources", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--name", "version_name", required=True, help="Name of the new version.")
@message_option
@json_option
@pass_client
def publish(
    client: LazyClient,
    *,
    dataset: str,
    sources: tuple[Path, ...],
    version_name: str,
    message: str | None,
    as_json: bool,
) -> None:
    """Publish SOURCES as a new version of a dataset, which becomes its latest version.

    Files are added at the root of the version. The files in a directory keep their
    path relative to it; hidden files and directories, such as `.env`, are skipped.
    With --json, prints the version with its `files`.
    """
    uploads = upload_pairs(sources)
    version = _publish(client.get(), dataset, uploads, version_name, message)
    if as_json:
        print_json(version)
        return
    click.echo(version["name"])


def _publish(
    library: Client,
    dataset: str,
    uploads: list[tuple[Path, str]],
    version_name: str,
    message: str | None,
) -> dict[str, Any]:
    """Create a version of `dataset` with the files of `uploads`, reporting progress.

    Returns:
        The version as JSON, with its `files`.
    """
    version = library.datasets.create_version(dataset, version_name, message)
    uploaded = []
    for local, path in uploads:
        uploaded.append(library.datasets.upload(version, local, path))
        click.echo(f"{path} ({format_size(local.stat().st_size)})", err=True)
    click.echo(
        f"Published version {version.name} of {dataset} with {len(uploaded)} file(s)", err=True
    )
    return nest(version, files=uploaded)


@datasets.command()
@dataset_argument
@click.option("--name", help="New name of the dataset. Its slug does not change.")
@description_option
@json_option
@pass_client
def update(
    client: LazyClient,
    *,
    dataset: str,
    name: str | None,
    description: str | None,
    as_json: bool,
) -> None:
    """Change the name or description of a dataset.

    Use `hexa datasets versions update` to change a version. With --json, prints the
    dataset.
    """
    if name is None and description is None:
        raise click.UsageError("Give --name or --description")
    item = client.get().datasets.update(dataset, name=name, description=description)
    click.echo(f"Updated dataset {item.reference}", err=True)
    if as_json:
        print_json(item)


@datasets.command()
@dataset_argument
@yes_option
@json_option
@pass_client
def delete(client: LazyClient, dataset: str, yes: bool, as_json: bool) -> None:
    """Delete a dataset with all its versions and files.

    Use `hexa datasets versions delete` to delete a single version. With --json, prints
    the dataset.
    """
    library = client.get()
    item = library.datasets.get(dataset)
    count = len(library.datasets.versions(dataset))
    confirm(f"Delete dataset '{item.reference}' and its {count} version(s)", yes=yes)
    library.datasets.delete(dataset)
    click.echo(f"Deleted dataset {item.reference}", err=True)
    if as_json:
        print_json(item)


@datasets.group()
def versions() -> None:
    """List, rename and delete the versions of a dataset.

    VERSION is the name of a version, as shown by `hexa datasets versions list`.
    """


@versions.command("list")
@dataset_argument
@limit_option("versions", default=20)
@json_option
@pass_client
def list_versions(client: LazyClient, dataset: str, limit: int, as_json: bool) -> None:
    """List the versions of a dataset, newest first."""
    items = cut(
        client.get().datasets.versions(dataset, limit=fetch_limit(limit)),
        limit,
        empty=f"Dataset {dataset} has no version yet",
    )
    if as_json:
        print_json(items)
        return
    print_table(
        [(v.name, format_time(v.created_at), v.changelog or "") for v in items],
        ("version", "created", "message"),
    )


@versions.command("update")
@dataset_argument
@click.argument("version_name", metavar="VERSION")
@click.option("--name", help="New name of the version.")
@click.option("-m", "--message", help="New description of what changed in the version.")
@json_option
@pass_client
def update_version(
    client: LazyClient,
    *,
    dataset: str,
    version_name: str,
    name: str | None,
    message: str | None,
    as_json: bool,
) -> None:
    """Rename a version of a dataset or change its message. With --json, prints the version."""
    if name is None and message is None:
        raise click.UsageError("Give --name or --message")
    library = client.get()
    version = library.datasets.version(dataset, version_name)
    result = library.datasets.update_version(version, name=name, changelog=message)
    click.echo(f"Updated version {result.name} of dataset {dataset}", err=True)
    if as_json:
        print_json(result)


@versions.command("delete")
@dataset_argument
@click.argument("version_name", metavar="VERSION")
@yes_option
@json_option
@pass_client
def delete_version(
    client: LazyClient, dataset: str, version_name: str, yes: bool, as_json: bool
) -> None:
    """Delete a version of a dataset and its files.

    Deleting the latest version makes the previous one the latest. With --json, prints
    the version.
    """
    library = client.get()
    version = library.datasets.version(dataset, version_name)
    count = len(library.datasets.files(version))
    confirm(
        f"Delete version '{version.name}' of dataset '{dataset}' and its {count} file(s)",
        yes=yes,
    )
    library.datasets.delete_version(version)
    click.echo(f"Deleted version {version.name} of dataset {dataset}", err=True)
    if as_json:
        print_json(version)


@datasets.group()
def attributes() -> None:
    """Set or delete the custom attributes of a dataset.

    Attributes are key/value metadata, such as source=DHIS2 or year=2025. `hexa
    datasets show` lists them.
    """


@attributes.command("set")
@dataset_argument
@click.argument("key")
@click.argument("value")
@json_option
@pass_client
def set_attribute(client: LazyClient, dataset: str, key: str, value: str, as_json: bool) -> None:
    """Set the attribute KEY of a dataset to VALUE, replacing any previous value.

    VALUE is read as JSON if it can be (2025, true, ["COD","SEN"]), as text otherwise.
    With --json, prints the dataset.
    """
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    item = client.get().datasets.set_attribute(dataset, key, parsed)
    click.echo(f"Set {key}={json.dumps(parsed, ensure_ascii=False)} on {item.reference}", err=True)
    if as_json:
        print_json(item)


@attributes.command("delete")
@dataset_argument
@click.argument("key")
@json_option
@pass_client
def delete_attribute(client: LazyClient, dataset: str, key: str, as_json: bool) -> None:
    """Remove the attribute KEY of a dataset. With --json, prints the dataset."""
    item = client.get().datasets.delete_attribute(dataset, key)
    click.echo(f"Removed {key} from {item.reference}", err=True)
    if as_json:
        print_json(item)
