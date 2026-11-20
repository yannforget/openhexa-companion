"""`hexa files` commands."""

from pathlib import Path, PurePosixPath

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


@click.group()
def files() -> None:
    """Read and write the workspace files, shared by pipelines and notebooks.

    PATH is relative to the root of the workspace files, such as data/cases.csv.
    """


@files.command("list")
@click.argument("directory", default="")
@click.option("-r", "--recursive", is_flag=True, help="List the files of subdirectories too.")
@json_option
@pass_client
def list_files(client: LazyClient, directory: str, recursive: bool, as_json: bool) -> None:
    """List the files in DIRECTORY, or the root by default."""
    items = client.get().files.list(directory, recursive=recursive)
    if as_json:
        print_json(items)
        return
    print_table(
        [
            (
                item.path,
                "" if item.is_directory else format_size(item.size),
                format_time(item.updated_at) if item.updated_at else "",
            )
            for item in items
        ],
        ("path", "size", "updated"),
    )


@files.command()
@click.argument("paths", nargs=-1, required=True)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    default=".",
    show_default=True,
    help="Directory to save the files in. Files keep their path below a downloaded "
    "directory, or just their name.",
)
@click.option("--force", is_flag=True, help="Replace local files that already exist.")
@pass_client
def download(client: LazyClient, paths: tuple[str, ...], output: Path, force: bool) -> None:
    """Download the files or directories at PATHS.

    Existing local files are left untouched unless --force is given; the command stops
    before downloading anything if one exists.
    """
    library = client.get()
    targets = []
    for path in paths:
        item = library.files.get(path)
        if item.is_directory:
            parent = PurePosixPath(item.path).parent.as_posix()
            base = "" if parent == "." else f"{parent}/"
            targets += [
                (file.path, file.size, local_path(output, file.path.removeprefix(base)))
                for file in library.files.list(item.path, recursive=True)
            ]
        else:
            targets.append((item.path, item.size, local_path(output, PurePosixPath(path).name)))
    check_targets((target for _, _, target in targets), force=force)
    for path, size, target in targets:
        with library.files.read(path) as stream:
            save_stream(stream, target)
        click.echo(f"{target} ({format_size(size)})", err=True)
    click.echo(f"Downloaded {len(targets)} file(s)", err=True)


@files.command()
@click.argument("sources", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--to",
    "destination",
    default="",
    help="Directory of the workspace files to upload to. Defaults to the root.",
)
@pass_client
def upload(client: LazyClient, sources: tuple[Path, ...], destination: str) -> None:
    """Upload SOURCES to the workspace files, replacing files at the same path.

    The files in a directory keep their path relative to it; hidden files and
    directories, such as .env, are skipped.
    """
    prefix = f"{destination.strip('/')}/" if destination.strip("/") else ""
    library = client.get()
    pairs = upload_pairs(sources)
    for local, path in pairs:
        library.files.upload(local, prefix + path)
        click.echo(f"{prefix}{path} ({format_size(local.stat().st_size)})", err=True)
    click.echo(f"Uploaded {len(pairs)} file(s)", err=True)
