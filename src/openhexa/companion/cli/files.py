"""`hexa files` commands."""

from pathlib import Path, PurePosixPath

import click

from openhexa.companion import Client, NotFoundError, WorkspaceFile
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._local import (
    check_targets,
    local_path,
    save_stream,
    upload_pairs,
)
from openhexa.companion.cli._output import (
    cut,
    format_size,
    format_time,
    json_option,
    limit_option,
    nest,
    print_bytes,
    print_json,
    print_table,
)
from openhexa.companion.cli._prompt import confirm, yes_option


@click.group()
def files() -> None:
    """Read and write the workspace files, shared by pipelines and notebooks.

    PATH is relative to the root of the workspace files, such as data/cases.csv.
    """


@files.command("list")
@click.argument("directory", default="")
@click.option("-r", "--recursive", is_flag=True, help="List the files of subdirectories too.")
@limit_option("files")
@json_option
@pass_client
def list_files(
    client: LazyClient, directory: str, recursive: bool, limit: int | None, as_json: bool
) -> None:
    """List the files in DIRECTORY, or the root by default."""
    items = cut(
        client.get().files.list(directory, recursive=recursive),
        limit,
        empty=f"No files in {directory or 'the root'}",
        complete=True,
    )
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
    default=Path(),
    help="Directory to save the files in. Defaults to the current directory.",
)
@click.option("--force", is_flag=True, help="Replace local files that already exist.")
@json_option
@pass_client
def download(
    client: LazyClient, paths: tuple[str, ...], output: Path, force: bool, as_json: bool
) -> None:
    """Download the files or directories at PATHS.

    A file is saved under its name, and a directory under its name with the files in
    it, keeping their paths below it. To copy files for `hexa local run`, use `hexa
    local pull files/PATH`. Existing local files are left untouched unless --force is
    given; the command stops before downloading anything if one exists. With --json,
    prints the files written, with their `local_path`.
    """
    root = output
    library = client.get()
    targets: list[tuple[WorkspaceFile, Path]] = []
    for path in paths:
        item = library.files.get(path)
        if item.is_directory:
            parent = PurePosixPath(item.path).parent.as_posix()
            base = "" if parent == "." else f"{parent}/"
            targets += [
                (file, local_path(root, file.path.removeprefix(base)))
                for file in library.files.list(item.path, recursive=True)
            ]
        else:
            targets.append((item, local_path(root, PurePosixPath(item.path).name)))
    check_targets((target for _, target in targets), force=force)
    for file, target in targets:
        with library.files.read(file.path) as stream:
            save_stream(stream, target)
        click.echo(f"{target} ({format_size(file.size)})", err=True)
    click.echo(f"Downloaded {len(targets)} file(s) to {root}", err=True)
    if as_json:
        print_json([nest(file, local_path=str(target)) for file, target in targets])


@files.command()
@click.argument("path")
@json_option
@pass_client
def read(client: LazyClient, path: str, as_json: bool) -> None:
    """Print the file at PATH to stdout, as is, without saving it.

    Pipe it into `head` to look at the start of a large file. With --json, only errors
    are printed as JSON: the content is printed as is, since it may not be text. Use
    `hexa files download` to save files.
    """
    del as_json  # The content is never JSON; the root command prints errors as JSON.
    library = client.get()
    item = library.files.get(path)
    if item.is_directory:
        raise click.UsageError(
            f"'{item.path}' is a directory: list it with `hexa files list {item.path}`"
        )
    with library.files.read(item.path) as stream:
        print_bytes(stream)


@files.command()
@click.argument("sources", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--to",
    "destination",
    default="",
    help="Directory of the workspace files to upload to. Defaults to the root.",
)
@click.option("--force", is_flag=True, help="Replace workspace files that already exist.")
@json_option
@pass_client
def upload(
    client: LazyClient, sources: tuple[Path, ...], destination: str, force: bool, as_json: bool
) -> None:
    """Upload SOURCES to the workspace files.

    The files in a directory keep their path relative to it; hidden files and
    directories, such as .env, are skipped. Existing workspace files are left untouched
    unless --force is given; the command stops before uploading anything if one exists.
    With --json, prints the files uploaded.
    """
    prefix = f"{destination.strip('/')}/" if destination.strip("/") else ""
    library = client.get()
    pairs = [(local, prefix + path) for local, path in upload_pairs(sources)]
    if not force:
        _check_remote_targets(library, [path for _, path in pairs])
    uploaded = []
    for local, path in pairs:
        uploaded.append(library.files.upload(local, path))
        click.echo(f"{path} ({format_size(local.stat().st_size)})", err=True)
    click.echo(f"Uploaded {len(pairs)} file(s)", err=True)
    if as_json:
        print_json(uploaded)


def _check_remote_targets(library: Client, paths: list[str]) -> None:
    """Refuse to replace workspace files, listing each destination directory once."""
    remote: set[str] = set()
    for directory in sorted({PurePosixPath(path).parent.as_posix() for path in paths}):
        remote |= _paths_in(library, "" if directory == "." else directory)
    existing = [path for path in paths if path in remote]
    if existing:
        raise click.ClickException(
            f"Not uploading: {', '.join(existing)} already exist in the workspace files. "
            "Use --force to replace."
        )


def _paths_in(library: Client, directory: str) -> set[str]:
    """The paths directly in `directory` of the workspace files, none if it does not exist."""
    try:
        return {item.path for item in library.files.list(directory)}
    except NotFoundError:
        return set()


@files.command()
@click.argument("path")
@yes_option
@json_option
@pass_client
def delete(client: LazyClient, path: str, yes: bool, as_json: bool) -> None:
    """Delete the file at PATH, or the directory at PATH with everything in it.

    With --json, prints the file or directory deleted.
    """
    library = client.get()
    item = library.files.get(path)
    if item.is_directory:
        count = len(library.files.list(item.path, recursive=True))
        confirm(f"Delete directory '{item.path}' and the {count} file(s) in it", yes=yes)
    else:
        confirm(f"Delete '{item.path}'", yes=yes)
    library.files.delete(item.path)
    click.echo(f"Deleted {item.path}", err=True)
    if as_json:
        print_json(item)
