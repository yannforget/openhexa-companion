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
def demo_env() -> dict[str, str]:
    """The demo workspace credentials in `.env`: OPENHEXA_URL, _WORKSPACE and _TOKEN."""
    if not ENV_FILE.exists():
        pytest.skip(".env with demo workspace credentials is missing")
    return _read_env()


@pytest.fixture(scope="session")
def client(demo_env: dict[str, str]) -> Iterator[Client]:
    """A client for the demo workspace configured in `.env`."""
    env = demo_env
    with Client(env["OPENHEXA_URL"], env["OPENHEXA_WORKSPACE"], env["OPENHEXA_TOKEN"]) as client:
        yield client


@pytest.fixture
def saved_query_slugs(client: Client) -> Iterator[list[str]]:
    """Slugs of saved queries a test creates in the demo workspace, deleted after it."""
    slugs: list[str] = []
    yield slugs
    for slug in slugs:
        # The library cannot delete saved queries, and deleting needs the ID.
        found = client._transport.query(
            "query($slug: String!) { savedQueryBySlug(slug: $slug) { id } }", {"slug": slug}
        )["savedQueryBySlug"]
        if found:
            client._transport.query(
                "mutation($input: DeleteSavedQueryInput!) "
                "{ deleteSavedQuery(input: $input) { success } }",
                {"input": {"id": found["id"]}},
            )
