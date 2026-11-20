import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from openhexa.companion.cli import main
from openhexa.companion.cli.runs import _read_parameters

ENV_FILE = Path(__file__).parent.parent / ".env"


def test_cli_runs():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0, result.output


def test_missing_credentials_are_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for key in ("OPENHEXA_URL", "OPENHEXA_WORKSPACE", "OPENHEXA_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENHEXA_URL=https://openhexa.test\n")

    result = CliRunner().invoke(main, ["--env-file", str(env_file), "datasets", "list"])

    assert result.exit_code == 1
    assert "Missing OPENHEXA_WORKSPACE, OPENHEXA_TOKEN" in result.output


def test_parameters_are_read_as_json_or_strings(tmp_path: Path):
    params_file = tmp_path / "params.json"
    params_file.write_text('{"count": 1, "mode": "bad"}')

    parameters = _read_parameters(("count=3", 'words=["a"]', "name=Kinshasa"), params_file)

    assert parameters == {"count": 3, "mode": "bad", "words": ["a"], "name": "Kinshasa"}


@pytest.mark.workspace
def test_delete_needs_confirmation_without_terminal():
    if not ENV_FILE.exists():
        pytest.skip(".env with demo workspace credentials is missing")
    result = CliRunner().invoke(
        main, ["--env-file", str(ENV_FILE), "files", "delete", "regional_summary.parquet"]
    )

    assert result.exit_code == 1
    assert "Add --yes to confirm" in result.output


@pytest.mark.workspace
def test_list_datasets_as_json():
    if not ENV_FILE.exists():
        pytest.skip(".env with demo workspace credentials is missing")
    result = CliRunner().invoke(main, ["--env-file", str(ENV_FILE), "datasets", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert all("slug" in item and "workspace" in item for item in json.loads(result.output))
