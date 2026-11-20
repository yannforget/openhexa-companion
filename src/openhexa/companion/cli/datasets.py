"""`hexa datasets` commands."""

from pathlib import Path, PurePosixPath

import click

from openhexa.companion import Client, DatasetFile
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import (
    format_size,
    format_time,
    json_option,
    print_json,
    print_table,
)

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
    targets = [(file, _local_path(output, file)) for file in selected]
    existing = [str(target) for _, target in targets if target.exists()]
    if existing and not force:
        raise click.ClickException(
            f"Not downloading: {', '.join(existing)} already exist. Use --force to replace."
        )
    for file, target in targets:
        _download(library, file, target)
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
    uploads = _files_to_upload(sources)
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


def _local_path(output: Path, file: DatasetFile) -> Path:
    """Place `file` under `output`, refusing paths that would escape it."""
    path = PurePosixPath(file.path)
    if path.is_absolute() or ".." in path.parts:
        raise click.ClickException(f"Refusing to save '{file.path}' outside {output}")
    return output.joinpath(*path.parts)


def _download(library: Client, file: DatasetFile, target: Path) -> None:
    """Save `file` at `target`, through a temporary file so a failure leaves no partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.part")
    try:
        with library.datasets.read(file) as stream, partial.open("wb") as out:
            for chunk in stream:
                out.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def _files_to_upload(sources: tuple[Path, ...]) -> list[tuple[Path, str]]:
    """List `(local file, path in the version)` pairs, refusing duplicate paths."""
    uploads: dict[str, Path] = {}
    for source in sources:
        if source.is_dir():
            pairs = [
                (local, local.relative_to(source).as_posix())
                for local in sorted(source.rglob("*"))
                if local.is_file()
                and not any(part.startswith(".") for part in local.relative_to(source).parts)
            ]
        else:
            pairs = [(source, source.name)]
        for local, path in pairs:
            if path in uploads:
                raise click.ClickException(
                    f"Both {uploads[path]} and {local} would be uploaded as '{path}'"
                )
            uploads[path] = local
    if not uploads:
        raise click.ClickException("No files to upload")
    return [(local, path) for path, local in uploads.items()]
