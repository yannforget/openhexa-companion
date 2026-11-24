"""Printing command results as tables or JSON, and notes about lists."""

import dataclasses
import json
import os
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

import click

# Environment variable that turns on `--json` for every command.
JSON_ENVVAR = "HEXA_JSON"
# Key of `click.Context.meta` recording whether the command prints JSON, for errors.
JSON_META_KEY = "hexa.json"


def _record_json(ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
    ctx.meta[JSON_META_KEY] = value
    return value


json_option = click.option(
    "--json",
    "as_json",
    is_flag=True,
    envvar=JSON_ENVVAR,
    callback=_record_json,
    help=f"Print the result as JSON, for scripts and agents. {JSON_ENVVAR}=1 turns it on.",
)


def limit_option(what: str, default: int | None = None) -> Any:
    """Add `-n/--limit`, the maximum number of `what` to list: all by default, or `default`."""
    return click.option(
        "-n",
        "--limit",
        type=click.IntRange(min=1),
        default=default,
        show_default=default is not None,
        help=f"Maximum number of {what} to show."
        + ("" if default is not None else " Defaults to all."),
    )


def fetch_limit(limit: int | None) -> int | None:
    """The number of items to fetch to show `limit` of them and tell whether there are more."""
    return None if limit is None else limit + 1


def cut[T](items: Sequence[T], limit: int | None, *, empty: str, complete: bool = False) -> list[T]:
    """Return the first `limit` items, saying on stderr if the list is empty or cut.

    Args:
        items: The items, fetched with `fetch_limit(limit)` or all of them.
        limit: Maximum number of items to show, or `None` for all of them.
        empty: Note printed when there are no items, such as "No datasets in workspace X".
        complete: `items` holds every item, so the note can give their number.
    """
    if not items:
        note(empty)
    elif limit is not None and len(items) > limit:
        if complete:
            note(f"Showing {limit} of {len(items)}; use -n to show more")
        else:
            note(f"Showing the first {limit}; use -n to show more")
    return list(items[:limit])


def note(message: str) -> None:
    """Print a note about the result on stderr."""
    click.echo(message, err=True)


def print_json(value: Any) -> None:
    """Print dataclasses, lists of them and plain values as indented JSON."""
    click.echo(json.dumps(value, default=_to_json, indent=2, ensure_ascii=False))


def print_bytes(chunks: Iterable[bytes]) -> None:
    """Write `chunks` to stdout as is, stopping quietly if the reader closes the pipe.

    So `hexa files read PATH | head` reads only the start of a large file.
    """
    try:
        for chunk in chunks:
            # Click writes bytes to the binary stream under stdout.
            click.echo(chunk, nl=False)
    except BrokenPipeError:
        # Python flushes stdout again at exit, which would fail on the closed pipe.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)


def nest(item: Any, **fields: Any) -> dict[str, Any]:
    """Turn the dataclass `item` into a dict for JSON, with related data as extra `fields`."""
    return {**_to_json(item), **fields}


def print_table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> None:
    """Print rows as aligned columns, or nothing if there are no rows."""
    if not rows:
        return
    cells = [[header.upper() for header in headers]] + [
        ["" if cell is None else str(cell) for cell in row] for row in rows
    ]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    for row in cells:
        click.echo(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        )


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "kB", "MB", "GB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"


def format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _to_json(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot convert {type(value).__name__} to JSON")
