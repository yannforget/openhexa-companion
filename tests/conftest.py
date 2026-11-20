from collections.abc import Iterator
from pathlib import Path

import pytest

from openhexa.companion import Client

ENV_FILE = Path(__file__).parent.parent / ".env"


def _read_env() -> dict[str, str]:
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            env[key.strip()] = value.strip().strip("\"'")
    return env


@pytest.fixture(scope="session")
def client() -> Iterator[Client]:
    """A client for the demo workspace configured in `.env`."""
    if not ENV_FILE.exists():
        pytest.skip(".env with demo workspace credentials is missing")
    env = _read_env()
    with Client(env["OPENHEXA_URL"], env["OPENHEXA_WORKSPACE"], env["OPENHEXA_TOKEN"]) as client:
        yield client
