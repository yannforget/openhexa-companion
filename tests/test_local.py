"""`hexa local pull | status | clean`: the local copy of the workspace."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from openhexa.companion.cli import main

Handler = Callable[[dict[str, Any]], dict[str, Any] | httpx2.Response]
FakeOpenHexa = Callable[[Handler], list[str]]

LOCAL = Path(".hexa/workspace")


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(main, list(args))


def _connection(password: str | None) -> Handler:
    def handler(_body: dict[str, Any]) -> dict[str, Any]:
        fields = [
            {"code": "url", "secret": False, "value": "https://dhis2.test"},
            {"code": "password", "secret": True, "value": password},
        ]
        connection = {
            "slug": "dhis2",
            "name": "DHIS2",
            "type": "DHIS2",
            "description": None,
            "fields": fields,
        }
        return {"connectionBySlug": connection}

    return handler


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_pull_saves_connections_with_their_secrets(project: Path, fake_openhexa: FakeOpenHexa):
    fake_openhexa(_connection("s3cret"))

    first = _invoke("local", "pull", "connections/dhis2", "--json")
    again = _invoke("local", "pull", "connections/dhis2")
    forced = _invoke("local", "pull", "connections/dhis2", "--force")

    assert first.exit_code == 0, first.output
    saved = json.loads((LOCAL / "connections/dhis2.json").read_text())
    assert saved["type"] == "DHIS2"
    assert saved["fields"] == {"url": "https://dhis2.test", "password": "s3cret"}
    assert json.loads(first.stdout)[0]["local_path"] == str(LOCAL / "connections/dhis2.json")
    assert (LOCAL / ".gitignore").read_text().endswith("*\n")
    assert again.exit_code == 1
    assert "--force" in again.stderr
    assert forced.exit_code == 0, forced.output


def test_pull_checks_every_item_before_writing(project: Path, fake_openhexa: FakeOpenHexa):
    fake_openhexa(_connection(None))

    unreadable = _invoke("local", "pull", "connections/dhis2")
    invalid = _invoke("local", "pull", "connections/dhis2", "pipelines/etl")

    assert unreadable.exit_code == 1
    assert "cannot read password" in unreadable.stderr
    assert invalid.exit_code == 2
    assert "Invalid item 'pipelines/etl'" in invalid.stderr
    assert not LOCAL.exists()


def test_pull_refuses_a_shared_dataset_another_dataset_hides(
    project: Path, fake_openhexa: FakeOpenHexa
):
    def datasets(body: dict[str, Any]) -> dict[str, Any]:
        owner = body["variables"]["owner"]
        dataset = {
            "id": f"id-{owner}",
            "slug": "population",
            "name": "Population",
            "description": "",
            "workspace": {"slug": owner},
            "targetId": f"id-{owner}",
            "latestVersion": {
                "id": "00000000-0000-0000-0000-000000000001",
                "name": "v1",
                "changelog": None,
                "createdAt": "2026-11-01T10:00:00+00:00",
            },
            "attributes": [],
        }
        shared = {"datasets": {"items": [{"dataset": {"id": "id-other"}}]}}
        return {"workspace": shared, "datasetLinkBySlug": {"dataset": dataset}}

    fake_openhexa(datasets)

    result = _invoke("local", "pull", "datasets/other/population")

    assert result.exit_code == 1
    assert "does not name the shared dataset other/population" in result.stderr
    assert not LOCAL.exists()


def test_status_lists_the_local_copy(project: Path):
    for version in ("v2", "v10"):
        (LOCAL / "datasets/population" / version).mkdir(parents=True)
    (LOCAL / "files/data").mkdir(parents=True)
    (LOCAL / "files/data/cases.csv").write_text("a,b\n")
    (LOCAL / "connections").mkdir()
    (LOCAL / "connections/dhis2.json").write_text("{}")

    result = _invoke("local", "status", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "files": [{"path": "data/cases.csv", "size": 4}],
        "datasets": [{"dataset": "population", "versions": ["v2", "v10"], "used": "v10"}],
        "connections": ["dhis2"],
    }


def test_clean_deletes_one_item_or_everything(project: Path):
    for version in ("v1", "v2"):
        (LOCAL / "datasets/population" / version).mkdir(parents=True)
        (LOCAL / "datasets/population" / version / "pop.csv").write_text("a\n")

    unconfirmed = _invoke("local", "clean", "datasets/population@v1")
    version = _invoke("local", "clean", "datasets/population@v1", "--yes")
    missing = _invoke("local", "clean", "datasets/population/v1", "--yes")
    last = _invoke("local", "clean", "datasets/population/v2", "--yes")
    everything = _invoke("local", "clean", "--yes")

    assert unconfirmed.exit_code == 1
    assert "--yes" in unconfirmed.stderr
    assert version.exit_code == 0, version.output
    assert missing.exit_code == 3
    assert last.exit_code == 0, last.output
    assert not (LOCAL / "datasets/population").exists()
    assert everything.exit_code == 0, everything.output
    assert not LOCAL.exists()


@pytest.mark.workspace
def test_pull_files_and_datasets_from_demo_workspace(
    project: Path, demo_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
):
    for key, value in demo_env.items():
        monkeypatch.setenv(key, value)

    result = _invoke(
        "local", "pull", "files/regional_summary.parquet", "datasets/demo-monthly-sales-parquet"
    )

    assert result.exit_code == 0, result.output
    assert (LOCAL / "files/regional_summary.parquet").stat().st_size > 0
    versions = list((LOCAL / "datasets/demo-monthly-sales-parquet").iterdir())
    assert len(versions) == 1
    assert any(versions[0].iterdir())
