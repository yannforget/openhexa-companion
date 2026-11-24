"""Pipeline parameter values given on the command line, for `pipelines run` and `run`."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click


def parameter_options[F: Callable[..., Any]](command: F) -> F:
    """Add `--param` and `--params-file` to a command."""
    command = click.option(
        "--params-file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="JSON file with parameter values by code; --param values take precedence.",
    )(command)
    return click.option(
        "-p",
        "--param",
        "params",
        multiple=True,
        metavar="CODE=VALUE",
        help="Value of a parameter. VALUE is read as JSON if it can be (42, true, "
        '["a","b"]), as a string otherwise. Repeat for each parameter.',
    )(command)


def read_parameters(params: tuple[str, ...], params_file: Path | None) -> dict[str, Any]:
    """Merge the values of `--params-file` and `--param`, the latter taking precedence."""
    parameters: dict[str, Any] = {}
    if params_file is not None:
        try:
            loaded = json.loads(params_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise click.BadParameter(f"{params_file} is not valid JSON: {err}") from err
        if not isinstance(loaded, dict):
            raise click.BadParameter(f"{params_file} must hold a JSON object")
        parameters.update(loaded)
    for param in params:
        code, separator, raw = param.partition("=")
        if not separator or not code:
            raise click.BadParameter(f"Expected CODE=VALUE, got '{param}'")
        try:
            parameters[code] = json.loads(raw)
        except json.JSONDecodeError:
            parameters[code] = raw
    return parameters
