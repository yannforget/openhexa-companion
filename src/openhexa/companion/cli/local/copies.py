"""`hexa local pull | status | clean`: manage the local copy of the workspace."""

import json
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from openhexa.companion import Client, DatasetFile, DatasetVersion, NotFoundError
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._local import (
    LOCAL_WORKSPACE,
    check_targets,
    local_path,
    local_versions,
    local_workspace,
    save_stream,
)
from openhexa.companion.cli._output import format_size, json_option, note, print_json
from openhexa.companion.cli._prompt import confirm, yes_option
from openhexa.companion.cli.connections import connection_json

# Kinds of items in the local copy, each a folder of `.hexa/workspace/`.
KINDS = ("files", "datasets", "connections")


@dataclass(frozen=True)
class Download:
    """A file to write in the local copy, with a function returning its content."""

    target: Path
    size: int
    content: Callable[[], Iterable[bytes]]


@dataclass
class Copy:
    """The local copy of an item that `pull` writes, and its JSON."""

    item: str
    local_path: Path
    downloads: list[Download]
    details: dict[str, Any] = field(default_factory=dict)
    folder: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "local_path": str(self.local_path),
            **self.details,
            "files": [{"local_path": str(d.target), "size": d.size} for d in self.downloads],
        }


def _split(item: str) -> tuple[str, str]:
    """Split ITEM into its kind and the name within it, such as `datasets`, `population`."""
    kind, _, name = item.strip("/").partition("/")
    if kind not in KINDS:
        raise click.UsageError(
            f"Invalid item '{item}': expected a path starting with {', '.join(KINDS)}, "
            "such as datasets/population"
        )
    return kind, name


@click.command()
@click.argument("items", nargs=-1, required=True)
@click.option("--force", is_flag=True, help="Replace local files that already exist.")
@json_option
@pass_client
def pull(client: LazyClient, items: tuple[str, ...], force: bool, as_json: bool) -> None:
    """Copy workspace files, dataset versions and connections into .hexa/workspace/.

    Each ITEM names what to copy by its place in the local copy: files/PATH for a file
    or directory, which keeps its full path, or files for all the workspace files;
    datasets/DATASET for the latest version of a dataset, or datasets/DATASET@VERSION;
    connections/SLUG for a connection, with its secret values.

    DATASET is a slug, or WORKSPACE/SLUG for a shared dataset, which is saved under its
    slug as pipelines name it. Datasets take @VERSION rather than --version, since
    one command can pull several datasets. `hexa local run` reads the copies instead of
    the workspace. Every item is checked before anything is written: existing local
    files are left untouched unless --force is given. With --json, prints the items
    copied, with their `files`.
    """
    for item in items:
        kind, name = _split(item)
        if kind != "files" and not name:
            raise click.UsageError(f"Name the {kind[:-1]} to pull, such as {kind}/SLUG")
    library = client.get()
    copies = [_resolve(library, item) for item in items]
    _drop_repeated_downloads(copies)
    check_targets((d.target for copy in copies for d in copy.downloads), force=force)
    for copy in copies:
        local_workspace(_split(copy.item)[0])
        if copy.folder:
            copy.local_path.mkdir(parents=True, exist_ok=True)
        for download in copy.downloads:
            save_stream(download.content(), download.target)
            note(f"{download.target} ({format_size(download.size)})")
    count = sum(len(copy.downloads) for copy in copies)
    note(f"Pulled {len(copies)} item(s), {count} file(s), to {LOCAL_WORKSPACE}")
    if as_json:
        print_json([copy.to_json() for copy in copies])


def _drop_repeated_downloads(copies: list[Copy]) -> None:
    """Download a file once when several items include it, such as `files` and `files/a`."""
    seen: set[Path] = set()
    for copy in copies:
        downloads = [d for d in copy.downloads if d.target not in seen]
        seen.update(d.target for d in downloads)
        copy.downloads = downloads


def _resolve(library: Client, item: str) -> Copy:
    kind, name = _split(item)
    if kind == "files":
        return _resolve_files(library, item, name)
    if kind == "datasets":
        return _resolve_dataset(library, item, name)
    return _resolve_connection(library, item, name)


def _resolve_files(library: Client, item: str, path: str) -> Copy:
    root = LOCAL_WORKSPACE / "files"
    found = library.files.get(path) if path else None
    if found is None or found.is_directory:
        listed = library.files.list(found.path if found else "", recursive=True)
    else:
        listed = [found]

    downloads = [
        Download(local_path(root, f.path), f.size, _file_reader(library, f.path)) for f in listed
    ]
    target = local_path(root, found.path) if found else root
    return Copy(item, target, downloads, folder=found is None or found.is_directory)


def _file_reader(library: Client, path: str) -> Callable[[], Iterable[bytes]]:
    def read() -> Iterable[bytes]:
        with library.files.read(path) as stream:
            yield from stream

    return read


def _resolve_dataset(library: Client, item: str, name: str) -> Copy:
    dataset, at, version_name = name.partition("@")
    if at and not version_name:
        raise click.UsageError(f"Invalid item '{item}': give a version after @")
    version = library.datasets.version(dataset, version_name or None)
    _check_slug(library, version)
    if "/" in version.name or version.name in (".", ".."):
        raise click.ClickException(
            f"Version '{version.name}' cannot be a folder name, so it cannot be copied"
        )
    folder = local_path(LOCAL_WORKSPACE / "datasets", f"{version.dataset}/{version.name}")
    downloads = [
        Download(local_path(folder, f.path), f.size, _dataset_file_reader(library, f))
        for f in library.datasets.files(version)
    ]
    details = {"dataset": version.dataset, "version": version.name}
    return Copy(item, folder, downloads, details, folder=True)


def _dataset_file_reader(library: Client, file: DatasetFile) -> Callable[[], Iterable[bytes]]:
    def read() -> Iterable[bytes]:
        with library.datasets.read(file) as stream:
            yield from stream

    return read


def _check_slug(library: Client, version: DatasetVersion) -> None:
    """Refuse a local copy that would replace another dataset in `hexa local run`.

    Pipelines name datasets by slug, and `hexa local run` finds local copies by slug. A
    dataset shared from another workspace can only be saved under its slug if the slug
    names that dataset in the workspace, as the SDK resolves it.
    """
    if version.workspace == library.workspace:
        return
    reference = f"{version.workspace}/{version.dataset}"
    try:
        resolved = library.datasets.get(version.dataset)
    except NotFoundError:
        resolved = None
    if resolved is None or resolved.workspace != version.workspace:
        raise click.ClickException(
            f"In workspace {library.workspace}, the slug {version.dataset} does not name "
            f"the shared dataset {reference}, so `hexa local run` could not use a local "
            f"copy of it. Use `hexa datasets download {reference}` to save it elsewhere."
        )


def _resolve_connection(library: Client, item: str, slug: str) -> Copy:
    connection = library.connections.get(slug)
    missing = [
        code for code, value in connection.values(include_secrets=True).items() if value is None
    ]
    if missing:
        raise click.ClickException(
            f"The token cannot read {', '.join(missing)} of connection {slug}: only "
            "workspace editors and admins can read secret values."
        )
    content = json.dumps(connection_json(connection, show_secrets=True), indent=2) + "\n"
    target = local_path(LOCAL_WORKSPACE / "connections", f"{connection.slug}.json")
    download = Download(target, len(content.encode()), lambda: [content.encode()])
    return Copy(item, target, [download], {"connection": connection.slug, "type": connection.type})


@click.command()
@json_option
def status(as_json: bool) -> None:
    """Show what the local copy in .hexa/workspace/ holds.

    Lists the number of workspace files, the versions of each dataset with the one
    `hexa local run` reads (the greatest in natural order, so v10 comes after v2), and
    the connections. With --json, prints {"files", "datasets", "connections"}.
    """
    files = sorted(_files_in(LOCAL_WORKSPACE / "files"))
    datasets: list[dict[str, Any]] = []
    folder = LOCAL_WORKSPACE / "datasets"
    for dataset in sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else ():
        versions = local_versions(dataset)
        datasets.append(
            {
                "dataset": dataset.name,
                "versions": versions,
                "used": versions[-1] if versions else None,
            }
        )
    folder = LOCAL_WORKSPACE / "connections"
    connections = sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []
    if not (files or datasets or connections):
        note(f"Nothing in {LOCAL_WORKSPACE}: use `hexa local pull` to copy workspace items")
    if as_json:
        print_json(
            {
                "files": [
                    {"path": p.relative_to(LOCAL_WORKSPACE / "files").as_posix(), "size": size}
                    for p, size in files
                ],
                "datasets": datasets,
                "connections": connections,
            }
        )
        return
    if files:
        size = format_size(sum(size for _, size in files))
        click.echo(f"files: {len(files)} file(s), {size}")
    for item in datasets:
        used = f", local run reads {item['used']}" if item["used"] else ", no version"
        click.echo(f"datasets/{item['dataset']}: {', '.join(item['versions']) or '-'}{used}")
    for slug in connections:
        click.echo(f"connections/{slug}")


def _files_in(folder: Path) -> list[tuple[Path, int]]:
    if not folder.is_dir():
        return []
    return [(p, p.stat().st_size) for p in folder.rglob("*") if p.is_file()]


@click.command()
@click.argument("item", required=False)
@yes_option
@json_option
def clean(item: str | None, yes: bool, as_json: bool) -> None:
    """Delete the local copy of ITEM, or the whole local copy by default.

    ITEM is a folder of .hexa/workspace/, such as files, files/reports,
    datasets/population, datasets/population/v3 (or datasets/population@v3) or
    connections/dhis2-prod. `hexa local run` then reads it from the workspace again.
    With --json, prints the item deleted, with its number of `files`.
    """
    target = _local_item(item) if item else LOCAL_WORKSPACE
    if not target.exists():
        raise NotFoundError(f"Nothing to delete: {target} does not exist")
    count = len(_files_in(target)) if target.is_dir() else 1
    confirm(f"Delete the local copy {target} ({count} file(s))", yes=yes)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    parent = target.parent
    if parent.parent == LOCAL_WORKSPACE / "datasets" and not any(parent.iterdir()):
        # A dataset folder without versions would hide the workspace's dataset.
        parent.rmdir()
    note(f"Deleted {target} ({count} file(s))")
    if as_json:
        print_json({"item": item or "", "local_path": str(target), "files": count})


def _local_item(item: str) -> Path:
    kind, name = _split(item)
    if not name:
        return LOCAL_WORKSPACE / kind
    if kind == "datasets":
        return local_path(LOCAL_WORKSPACE / kind, name.replace("@", "/", 1))
    if kind == "connections":
        return local_path(LOCAL_WORKSPACE / kind, f"{name.removesuffix('.json')}.json")
    return local_path(LOCAL_WORKSPACE / kind, name)
