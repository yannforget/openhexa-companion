"""Local files read or written by commands that transfer files."""

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

import click


def local_path(output: Path, path: str) -> Path:
    """Place the remote `path` under `output`, refusing paths that would escape it."""
    remote = PurePosixPath(path)
    if not remote.parts or remote.is_absolute() or ".." in remote.parts:
        raise click.ClickException(f"Refusing to save '{path}' outside {output}")
    return output.joinpath(*remote.parts)


def check_targets(targets: Iterable[Path], *, force: bool) -> None:
    """Refuse to overwrite existing files unless `force` is set."""
    existing = [str(target) for target in targets if target.exists()]
    if existing and not force:
        raise click.ClickException(
            f"Not downloading: {', '.join(existing)} already exist. Use --force to replace."
        )


def save_stream(chunks: Iterable[bytes], target: Path) -> None:
    """Write `chunks` to `target` through a temporary file, so a failure leaves no partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.part")
    try:
        with partial.open("wb") as out:
            for chunk in chunks:
                out.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def upload_pairs(sources: Iterable[Path]) -> list[tuple[Path, str]]:
    """List `(local file, remote path)` pairs, refusing two files at the same path.

    A file is uploaded under its name. The files in a directory keep their path relative
    to it; hidden files and directories, such as `.env`, are skipped.
    """
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
