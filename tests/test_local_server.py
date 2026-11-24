"""The local OpenHEXA API of `hexa local run`, through HTTP, with a fake OpenHEXA upstream."""

import json
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src/openhexa/companion/cli/_runtime"))

from server import LocalServer  # pyrefly: ignore[missing-import]

REAL_TOKEN = "real-token"


# Authorization header and body of each request forwarded to the fake OpenHEXA.
RECEIVED: list[tuple[str, dict[str, Any]]] = []


class FakeOpenHexa(BaseHTTPRequestHandler):
    """Records forwarded requests and answers every one with a fixed result."""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        RECEIVED.append((self.headers["Authorization"], body))
        if "workspace(" in body["query"]:
            dataset = {"slug": "remote", "workspace": {"slug": "ws"}}
            data = {"workspace": {"datasets": {"items": [{"id": "1", "dataset": dataset}]}}}
        else:
            data = {"forwarded": True}
        content = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def _serve(server: ThreadingHTTPServer) -> Iterator[ThreadingHTTPServer]:
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def upstream() -> Iterator[ThreadingHTTPServer]:
    RECEIVED.clear()
    yield from _serve(ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenHexa))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    for path, content in {
        "datasets/population/v2/pop.csv": "old\n",
        "datasets/population/v10/pop.csv": "district,pop\nA,10\n",
        "connections/dhis.json": json.dumps(
            {"slug": "dhis", "type": "DHIS2", "fields": {"url": "https://dhis2.test"}}
        ),
        "files/reports/input.txt": "hello\n",
    }.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content)
    return tmp_path


def _server(workspace: Path, upstream: ThreadingHTTPServer, **settings: Any) -> LocalServer:
    host, port = upstream.server_address[:2]
    settings = {
        "openhexa_url": f"http://{host!s}:{port}",
        "token": REAL_TOKEN,
        "workspace": "ws",
        **settings,
    }
    return LocalServer(settings, workspace, workspace / "files")


@pytest.fixture
def server(workspace: Path, upstream: ThreadingHTTPServer) -> Iterator[LocalServer]:
    yield from _serve(_server(workspace, upstream))


def _request(
    url: str, *, method: str = "GET", data: bytes | None = None, token: str | None = None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def _graphql(server: LocalServer, query: str, **variables: Any) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode()
    status, content = _request(
        f"{server.url}/graphql/", method="POST", data=body, token=server.run_token
    )
    assert status == 200
    return json.loads(content)


def test_reads_local_datasets_and_their_files(server: LocalServer):
    link = _graphql(
        server,
        "query($s: String!, $w: String!) { datasetLinkBySlug(datasetSlug: $s, "
        "workspaceSlug: $w) { dataset { id latestVersion { id name } } } }",
        s="population",
        w="ws",
    )["data"]["datasetLinkBySlug"]["dataset"]
    assert link["latestVersion"]["name"] == "v10"

    version = _graphql(
        server,
        "query($id: ID!) { datasetVersion(id: $id) { fileByName(name: $n) { id } } }",
        id=link["latestVersion"]["id"],
        n="pop.csv",
    )["data"]["datasetVersion"]
    download = _graphql(
        server,
        "mutation($input: PrepareVersionFileDownloadInput!) { "
        "prepareVersionFileDownload(input: $input) { downloadUrl } }",
        input={"fileId": version["fileByName"]["id"]},
    )["data"]["prepareVersionFileDownload"]

    assert _request(download["downloadUrl"]) == (200, b"district,pop\nA,10\n")


def test_writes_a_new_local_version_without_overwriting(server: LocalServer, workspace: Path):
    version = _graphql(
        server,
        "mutation($input: CreateDatasetVersionInput!) { createDatasetVersion(input: $input) "
        "{ success version { id } } }",
        input={"datasetId": "local:dataset:population", "name": "v11"},
    )["data"]["createDatasetVersion"]["version"]
    upload_query = (
        "mutation($input: GenerateDatasetUploadUrlInput!) { "
        "generateDatasetUploadUrl(input: $input) { success errors uploadUrl } }"
    )
    upload = _graphql(server, upload_query, input={"versionId": version["id"], "uri": "a/b.csv"})
    url = upload["data"]["generateDatasetUploadUrl"]["uploadUrl"]

    assert _request(url, method="PUT", data=b"new")[0] == 200
    assert (workspace / "datasets/population/v11/a/b.csv").read_bytes() == b"new"
    assert _request(url, method="PUT", data=b"again")[0] == 409
    again = _graphql(server, upload_query, input={"versionId": version["id"], "uri": "a/b.csv"})
    assert again["data"]["generateDatasetUploadUrl"]["errors"] == ["ALREADY_EXISTS"]
    assert server.router.summary["new_versions"] == [{"dataset": "population", "version": "v11"}]


def test_forwards_other_queries_with_the_real_token(server: LocalServer):
    answer = _graphql(server, "{ me { email } }")

    assert answer == {"data": {"forwarded": True}}
    assert RECEIVED[0][0] == f"Bearer {REAL_TOKEN}"


def test_adds_local_datasets_to_the_workspace_list(server: LocalServer):
    answer = _graphql(server, '{ workspace(slug: "ws") { datasets { items { id } } } }')

    slugs = [i["dataset"]["slug"] for i in answer["data"]["workspace"]["datasets"]["items"]]
    assert slugs == ["remote", "population"]


def test_answers_local_connections_and_run_reporting(server: LocalServer):
    connection = _graphql(
        server,
        'query { connectionBySlug(workspaceSlug: "ws", connectionSlug: "dhis") '
        "{ __typename type fields { code value } } }",
    )["data"]["connectionBySlug"]
    _graphql(
        server,
        "mutation($input: LogPipelineMessageInput!) { logPipelineMessage(input: $input) "
        "{ success } }",
        input={"priority": "WARNING", "message": "careful"},
    )

    assert connection["__typename"] == "DHIS2Connection"
    assert connection["fields"] == [{"code": "url", "value": "https://dhis2.test"}]
    assert server.router.summary["messages"] == [{"priority": "WARNING", "message": "careful"}]
    assert RECEIVED == []


def test_refuses_remote_writes(server: LocalServer):
    answer = _graphql(server, 'mutation { createDataset(input: {name: "x"}) { success } }')

    assert "--allow-remote-writes" in answer["errors"][0]["message"]
    assert RECEIVED == []


def test_forwards_remote_writes_when_allowed(workspace: Path, upstream: ThreadingHTTPServer):
    for server in _serve(_server(workspace, upstream, allow_remote_writes=True)):
        _graphql(server, 'mutation { createDataset(input: {name: "x"}) { success } }')

    assert len(RECEIVED) == 1


@pytest.mark.parametrize(
    "query",
    [
        "query a { me { email } } query b { me { email } }",
        '{ datasetLinkBySlug(datasetSlug: "population", workspaceSlug: "ws") { id } me { id } }',
        "not graphql",
    ],
)
def test_refuses_requests_it_cannot_route(server: LocalServer, query: str):
    answer = _graphql(server, query)

    assert answer["errors"][0]["message"].startswith("Local run refused a request")
    assert RECEIVED == []


def test_rejects_other_tokens(server: LocalServer):
    body = json.dumps({"query": "{ me { email } }"}).encode()

    status, _ = _request(f"{server.url}/graphql/", method="POST", data=body, token=REAL_TOKEN)

    assert status == 401
    assert RECEIVED == []


def test_serves_no_file_outside_the_datasets(server: LocalServer):
    assert _request(f"{server.url}/local/datasets/../connections/dhis.json")[0] == 404
    assert _request(f"{server.url}/local/datasets/%2E%2E/connections/dhis.json")[0] == 404
