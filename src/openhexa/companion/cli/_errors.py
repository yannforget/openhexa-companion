"""Exit codes, and errors printed as text or as JSON with `--json`."""

import json
import os
from collections.abc import Sequence
from typing import NoReturn

import click

from openhexa.companion import (
    AuthenticationError,
    NotFoundError,
    OpenHexaError,
    PermissionDeniedError,
)
from openhexa.companion.cli._context import LazyClient
from openhexa.companion.cli._output import JSON_ENVVAR, JSON_META_KEY

ERROR_EXIT_CODE = 1
USAGE_EXIT_CODE = 2
NOT_FOUND_EXIT_CODE = 3
PERMISSION_DENIED_EXIT_CODE = 4
# Exit code of commands that follow a remote run when it does not succeed.
RUN_FAILED_EXIT_CODE = 5

# Exit code of each library error, checked in order; other errors exit with 1.
LIBRARY_EXIT_CODES: tuple[tuple[type[OpenHexaError], int], ...] = (
    (NotFoundError, NOT_FOUND_EXIT_CODE),
    (PermissionDeniedError, PERMISSION_DENIED_EXIT_CODE),
    (AuthenticationError, PERMISSION_DENIED_EXIT_CODE),
)


class Group(click.Group):
    """The root group: maps errors to exit codes and prints them as JSON when asked."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # Kept to tell whether `--json` was given when parsing a subcommand fails.
        ctx.meta[_ARGS_META_KEY] = list(args)
        return super().parse_args(ctx, args)

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except OpenHexaError as err:
            code = next(
                (code for kind, code in LIBRARY_EXIT_CODES if isinstance(err, kind)),
                ERROR_EXIT_CODE,
            )
            message = str(err)
            if isinstance(err, AuthenticationError):
                message += f". {_token_help(ctx)}"
            _fail(ctx, type(err).__name__, message, code, err)
        except click.UsageError as err:
            if not _json_requested(ctx):
                raise
            _fail(ctx, "UsageError", err.format_message(), USAGE_EXIT_CODE, err)
        except click.ClickException as err:
            _fail(ctx, "Error", err.format_message(), err.exit_code, err)


def _fail(ctx: click.Context, kind: str, message: str, code: int, err: Exception) -> NoReturn:
    """Print an error as text or, if the command asked for JSON, as an `error` object."""
    if _json_requested(ctx):
        error = {"error": {"type": kind, "message": message}}
        click.echo(json.dumps(error, ensure_ascii=False), err=True)
    else:
        click.echo(f"Error: {message}", err=True)
    raise click.exceptions.Exit(code) from err


def _token_help(ctx: click.Context) -> str:
    """Say where to get a new token and where to put it."""
    lazy = ctx.find_object(LazyClient)
    if lazy is None:
        return "Set a valid OPENHEXA_TOKEN"
    # The token was sent, so the client exists.
    return (
        f"Get a new token on your OpenHEXA account page ({lazy.get().url}/user/account, "
        f"under Access tokens) and set OPENHEXA_TOKEN in {lazy.env_file} or in the "
        "environment, which takes precedence"
    )


# Key of `click.Context.meta` holding the arguments given to the root command.
_ARGS_META_KEY = "hexa.args"


def _json_requested(ctx: click.Context) -> bool:
    """Whether the command asked for JSON.

    The `--json` option records its value once parsed. If the command failed before,
    such as on an unknown option, `--json` among the arguments or `HEXA_JSON` decide.
    """
    if JSON_META_KEY in ctx.meta:
        return bool(ctx.meta[JSON_META_KEY])
    args: Sequence[str] = ctx.meta.get(_ARGS_META_KEY, ())
    if "--" in args:
        args = args[: list(args).index("--")]
    if "--json" in args:
        return True
    try:
        return click.BOOL.convert(os.environ.get(JSON_ENVVAR, ""), None, None)
    except click.BadParameter:
        return False
