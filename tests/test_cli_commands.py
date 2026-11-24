"""The shape of the `hexa` commands: arguments, options and what they print."""

import base64
import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from openhexa.companion.cli import main

# Answers a GraphQL request body with the `data` of the response, or an HTTP response.
Handler = Callable[[dict[str, Any]], dict[str, Any] | httpx2.Response]
FakeOpenHexa = Callable[[Handler], list[str]]

PIPELINE_SOURCE = {"pipeline.py": "print('hello')\n", "lib/util.py": "X = 1\n"}


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(main, list(args))


def _pipeline_version(**fields: Any) -> dict[str, Any]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in PIPELINE_SOURCE.items():
            archive.writestr(path, content)
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": None,
        "versionNumber": 1,
        "description": None,
        "createdAt": "2026-11-01T10:00:00+00:00",
        "timeout": None,
        "config": {},
        "parameters": [],
        "pipeline": {"code": "etl", "workspace": {"slug": "ws"}},
        "zipfile": base64.b64encode(buffer.getvalue()).decode("ascii"),
        **fields,
    }


def _pipelines(body: dict[str, Any]) -> dict[str, Any]:
    """Answer lookups of pipeline `etl` and its creation or deployment."""
    query = body["query"]
    for mutation in ("createPipeline", "uploadPipeline"):
        if f"{mutation}(" in query:
            version = body["variables"]["input"].get("version", body["variables"]["input"])
            created = _pipeline_version(
                name=version["name"], description=version["description"], versionNumber=2
            )
            return {mutation: {"success": True, "errors": [], "pipelineVersion": created}}
    if "pipelineVersion(" in query:
        return {"pipelineVersion": _pipeline_version()}
    return {"pipelineByCode": {"currentVersion": _pipeline_version()}}


def test_pipelines_run_points_folders_to_local_run(fake_openhexa: FakeOpenHexa, tmp_path: Path):
    def no_pipeline(body: dict[str, Any]) -> dict[str, Any]:
        if "pipelineByCode(" in body["query"]:
            return {"pipelineByCode": None}
        return {"pipelines": {"totalPages": 1, "items": []}}

    fake_openhexa(no_pipeline)

    result = _invoke("pipelines", "run", str(tmp_path))

    assert result.exit_code == 2
    assert "hexa local run" in result.stderr


def test_pipelines_source_saves_to_the_pipeline_folder(
    fake_openhexa: FakeOpenHexa, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    fake_openhexa(_pipelines)
    (tmp_path / "etl").mkdir()
    (tmp_path / "etl" / "notes.md").write_text("mine")

    first = _invoke("pipelines", "source", "etl")
    (tmp_path / "etl" / "pipeline.py").write_text("changed")
    refused = _invoke("pipelines", "source", "etl")
    forced = _invoke("pipelines", "source", "etl", "--force", "--json")

    assert first.exit_code == 0, first.output
    assert refused.exit_code == 1
    assert "--force" in refused.stderr
    assert forced.exit_code == 0, forced.output
    assert sorted(json.loads(forced.stdout)) == [
        str(Path("etl/lib/util.py")),
        str(Path("etl/pipeline.py")),
    ]
    assert (tmp_path / "etl" / "pipeline.py").read_text() == PIPELINE_SOURCE["pipeline.py"]
    assert (tmp_path / "etl" / "notes.md").read_text() == "mine"


@pytest.mark.parametrize(
    ("args", "mutation"),
    [
        (["create", "ETL", "-d", "Loads cases"], "createPipeline"),
        (["publish", "etl"], "uploadPipeline"),
    ],
)
def test_pipelines_create_and_publish_take_source_name_and_message(
    fake_openhexa: FakeOpenHexa, tmp_path: Path, args: list[str], mutation: str
):
    (tmp_path / "pipeline.py").write_text("")
    bodies: list[dict[str, Any]] = []

    def record(body: dict[str, Any]) -> dict[str, Any]:
        bodies.append(body)
        return _pipelines(body)

    fake_openhexa(record)

    result = _invoke("pipelines", *args, str(tmp_path), "--name", "v2", "-m", "Faster")

    assert result.exit_code == 0, result.output
    assert result.stdout == "etl\n"
    sent = bodies[-1]["variables"]["input"]
    version = sent.get("version", sent)
    assert mutation in bodies[-1]["query"]
    assert (version["name"], version["description"]) == ("v2", "Faster")


def test_queries_show_prints_details_and_sql_prints_only_sql(fake_openhexa: FakeOpenHexa):
    saved = {
        "slug": "cases-by-district",
        "name": "Cases by district",
        "description": "Monthly totals",
        "visibility": "WORKSPACE",
        "createdBy": {"displayName": "Ada"},
        "updatedAt": "2026-11-01T10:00:00+00:00",
        "content": "select 1\n",
        "workspace": {"slug": "ws"},
    }
    fake_openhexa(lambda _body: {"savedQueryBySlug": saved})

    shown = _invoke("queries", "show", "cases-by-district")
    sql = _invoke("queries", "sql", "cases-by-district")

    assert "Cases by district" in shown.stdout
    assert "Monthly totals" in shown.stdout
    assert shown.stdout.endswith("select 1\n")
    assert shown.stderr == ""
    assert sql.stdout == "select 1\n"


def test_datasets_create_needs_sources_for_version_options(fake_openhexa: FakeOpenHexa):
    queries = fake_openhexa(lambda _body: {})

    result = _invoke("datasets", "create", "Cases", "--name", "v1")

    assert result.exit_code == 2
    assert "give SOURCES" in result.stderr
    assert queries == []


def _workspace_and_user(body: dict[str, Any]) -> dict[str, Any]:
    if "me {" in body["query"]:
        return {"me": {"user": {"id": "u1", "email": "ana@example.org"}}}
    workspace = {
        "slug": "ws",
        "name": "Workspace",
        "description": "# Home\n",
        "configuration": {},
        "dockerImage": None,
    }
    return {"workspace": workspace}


def test_workspace_show_checks_the_configuration(fake_openhexa: FakeOpenHexa):
    fake_openhexa(_workspace_and_user)

    text = _invoke("workspace", "show")
    as_json = _invoke("workspace", "show", "--json")

    assert text.exit_code == 0, text.output
    assert "OpenHEXA: https://openhexa.test" in text.stdout
    assert "User: ana@example.org" in text.stdout
    shown = json.loads(as_json.stdout)
    assert shown["slug"] == "ws"
    assert shown["url"] == "https://openhexa.test"
    assert shown["user"] == {"id": "u1", "email": "ana@example.org"}


def test_a_rejected_token_says_where_to_get_a_new_one(fake_openhexa: FakeOpenHexa):
    fake_openhexa(lambda _body: httpx2.Response(401))

    text = _invoke("workspace", "show")
    as_json = _invoke("workspace", "show", "--json")

    assert text.exit_code == 4
    assert "https://openhexa.test/user/account" in text.stderr
    assert "OPENHEXA_TOKEN in .env" in text.stderr
    error = json.loads(as_json.stderr)["error"]
    assert error["type"] == "AuthenticationError"
    assert "/user/account" in error["message"]
