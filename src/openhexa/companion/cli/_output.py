"""Printing command results as tables or JSON."""

import dataclasses
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import click

json_option = click.option(
    "--json", "as_json", is_flag=True, help="Print the result as JSON, for scripts and agents."
)


def print_json(value: Any) -> None:
    """Print dataclasses, lists of them and plain values as indented JSON."""
    click.echo(json.dumps(value, default=_to_json, indent=2, ensure_ascii=False))


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
