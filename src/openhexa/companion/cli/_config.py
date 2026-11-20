"""Workspace credentials for the CLI, from the environment or a `.env` file."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import click

# Variables holding the credentials, in the environment or a `.env` file.
KEYS = ("OPENHEXA_URL", "OPENHEXA_WORKSPACE", "OPENHEXA_TOKEN")


@dataclass(frozen=True)
class Config:
    url: str
    workspace: str
    token: str = field(repr=False)


def load_config(env_file: Path) -> Config:
    """Read the credentials from environment variables, then from `env_file`.

    Environment variables take precedence, so a variable can override one key of the
    file. Only literal `KEY=value` lines are read; quotes around values are removed.

    Raises:
        click.ClickException: A credential is missing.
    """
    values = _read_env_file(env_file) if env_file.is_file() else {}
    values |= {key: os.environ[key] for key in KEYS if os.environ.get(key)}
    missing = [key for key in KEYS if not values.get(key)]
    if missing:
        raise click.ClickException(
            f"Missing {', '.join(missing)}. Set them as environment variables or in "
            f"{env_file}, for example:\n\n"
            "  OPENHEXA_URL=https://app.openhexa.org\n"
            "  OPENHEXA_WORKSPACE=my-workspace\n"
            "  OPENHEXA_TOKEN=<token from the workspace settings>"
        )
    return Config(values["OPENHEXA_URL"], values["OPENHEXA_WORKSPACE"], values["OPENHEXA_TOKEN"])


def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().removeprefix("export ").partition("=")
        key = key.strip()
        if separator and key in KEYS:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    return values
