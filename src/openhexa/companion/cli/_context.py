"""The workspace client shared by the commands of one invocation."""

from pathlib import Path

import click

from openhexa.companion import Client
from openhexa.companion.cli._config import load_config


class LazyClient:
    """Creates the client on first use, so `--help` works without credentials."""

    def __init__(self, env_file: Path) -> None:
        self._env_file = env_file
        self._client: Client | None = None

    def get(self) -> Client:
        if self._client is None:
            config = load_config(self._env_file)
            self._client = Client(config.url, config.workspace, config.token)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


pass_client = click.make_pass_decorator(LazyClient)
