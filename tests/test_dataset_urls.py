"""Signed URLs of dataset files, through the library and `hexa datasets urls`."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner

from openhexa.companion import Client, NotFoundError, PermissionDeniedError, _transport
from openhexa.companion.cli import main

VERSION_ID = "6f1d2c1e-8c4b-4f6e-9a57-0d1f3b0b6a11"
PATHS = ["README.md", "data/2023.parquet", "data/2024.parquet"]


def _url(path: str) -> str:
    return f"https://storage.test/{path}?signature=x"


def _answer(body: dict[str, Any], *, signed: bool = True) -> dict[str, Any]:
    """Answer the dataset queries for dataset `pop` with version `v3` holding `PATHS`."""
    variables = body["variables"]

    def file(path: str) -> dict[str, Any]:
        return {
            "id": path,
            "uri": f"dataset-id/{VERSION_ID}/{path}",
            "size": 10,
            "contentType": "application/octet-stream",
            "downloadUrl": _url(path) if signed else None,
        }

    version: dict[str, Any] = {
        "id": VERSION_ID,
        "name": "v3",
        "changelog": None,
        "createdAt": "2026-11-01T10:00:00+00:00",
        "fileByName": file(variables["path"]) if variables.get("path") in PATHS else None,
    }
    if "perPage" in variables and "files(" in body["query"]:
        page, size = variables["page"], variables["perPage"]
        version["files"] = {
            "totalPages": -(-len(PATHS) // size),
            "items": [file(path) for path in PATHS[(page - 1) * size : page * size]],
        }
    return {
        "datasetLinkBySlug": {
            "dataset": {
                "id": "dataset-id",
                "slug": "pop",
                "workspace": {"slug": "ws"},
                "targetId": "dataset-id",
                "latestVersion": version,
                "version": version,
            }
        }
    }


def _client(answer: Callable[[dict[str, Any]], dict[str, Any]]) -> tuple[Client, list[str]]:
    queries: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        queries.append(body["query"])
        return httpx2.Response(200, json={"data": answer(body)})

    transport = httpx2.MockTransport(respond)
    return Client("https://openhexa.test", "ws", "token", http_transport=transport), queries


def test_file_urls_of_all_files_span_pages(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_transport, "PAGE_SIZE", 2)
    client, _queries = _client(_answer)
    version = client.datasets.version("pop")

    assert client.datasets.file_urls(version) == {path: _url(path) for path in PATHS}
    assert list(client.datasets.file_urls(version, limit=1)) == ["README.md"]


def test_file_urls_of_paths_keep_their_order():
    client, _queries = _client(_answer)
    version = client.datasets.version("pop")

    urls = client.datasets.file_urls(version, ["data/2024.parquet", "README.md"])

    assert list(urls.items()) == [
        ("data/2024.parquet", _url("data/2024.parquet")),
        ("README.md", _url("README.md")),
    ]


def test_file_urls_of_missing_path_lists_the_files():
    client, _queries = _client(_answer)
    version = client.datasets.version("pop")

    with pytest.raises(NotFoundError) as error:
        client.datasets.file_urls(version, ["README.md", "data/2025.parquet"])
    assert "no file 'data/2025.parquet'" in str(error.value)
    assert "data/2024.parquet" in str(error.value)


def test_file_urls_need_download_permission():
    client, _queries = _client(lambda body: _answer(body, signed=False))
    version = client.datasets.version("pop")

    with pytest.raises(PermissionDeniedError):
        client.datasets.file_urls(version)
    with pytest.raises(PermissionDeniedError):
        client.datasets.file_urls(version, ["README.md"])


def test_cli_prints_paths_and_urls(fake_openhexa: Callable[..., list[str]]):
    fake_openhexa(_answer)

    plain = CliRunner().invoke(main, ["datasets", "urls", "pop", "-n", "2"])
    as_json = CliRunner().invoke(main, ["datasets", "urls", "pop", "README.md", "--json"])

    assert plain.exit_code == 0, plain.output
    assert plain.stdout.splitlines() == [
        f"README.md\t{_url('README.md')}",
        f"data/2023.parquet\t{_url('data/2023.parquet')}",
    ]
    assert "Version v3" in plain.stderr
    assert "use -n to show more" in plain.stderr
    assert json.loads(as_json.stdout) == {
        "dataset": "pop",
        "version": "v3",
        "version_id": VERSION_ID,
        "urls": {"README.md": _url("README.md")},
    }
