import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from openhexa.companion.cli import main

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


@pytest.mark.workspace
def test_list_datasets_as_json():
    if not ENV_FILE.exists():
        pytest.skip(".env with demo workspace credentials is missing")
    result = CliRunner().invoke(main, ["--env-file", str(ENV_FILE), "datasets", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert all("slug" in item and "workspace" in item for item in json.loads(result.output))
