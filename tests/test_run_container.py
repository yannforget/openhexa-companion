"""`hexa run` end to end: a real container engine and the demo workspace.

Run on demand with `uv run pytest -m container`. The engine is HEXA_CONTAINER_ENGINE or
the first of docker and podman; the image is HEXA_TEST_IMAGE or the workspace's.
"""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from openhexa.companion.cli import main

pytestmark = [pytest.mark.container, pytest.mark.workspace]

# A dataset of the demo workspace, read remotely by the test pipeline.
REMOTE_DATASET = "demo-monthly-sales-parquet"

PIPELINE = f"""
from openhexa.sdk import current_run, pipeline, workspace


@pipeline("local run test")
def local_run_test():
    with open(f"{{workspace.files_path}}/input.txt") as source:
        current_run.log_info(source.read().strip())
    local = workspace.get_dataset("population")
    current_run.log_info(local.latest_version.get_file("pop.csv").read().decode().strip())
    remote = workspace.get_dataset("{REMOTE_DATASET}").latest_version
    current_run.log_info(f"remote files: {{len(list(remote.files))}}")
    version = local.create_version("v3")
    version.add_file(b"district,pop\\nA,12\\n", filename="pop.csv")
    try:
        workspace.create_dataset("must not exist", "")
    except Exception:
        current_run.log_warning("remote write refused")
"""


def test_run_reads_locally_and_remotely(
    tmp_path: Path, demo_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
):
    for key, value in demo_env.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "pipe").mkdir()
    (tmp_path / "pipe/pipeline.py").write_text(PIPELINE)
    (tmp_path / ".hexa/workspace/files").mkdir(parents=True)
    (tmp_path / ".hexa/workspace/files/input.txt").write_text("local file\n")
    (tmp_path / ".hexa/workspace/datasets/population/v2").mkdir(parents=True)
    (tmp_path / ".hexa/workspace/datasets/population/v2/pop.csv").write_text("district,pop\n")
    monkeypatch.chdir(tmp_path)
    image = ["--image", os.environ["HEXA_TEST_IMAGE"]] if "HEXA_TEST_IMAGE" in os.environ else []

    result = CliRunner().invoke(main, ["run", "pipe", "--json", *image])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    messages = [m["message"] for m in summary["messages"]]
    assert messages[:2] == ["local file", "district,pop"]
    assert messages[2].startswith("remote files: ")
    assert messages[3] == "remote write refused"
    assert summary["refused"] == ["createDataset"]
    new_file = tmp_path / ".hexa/workspace/datasets/population/v3/pop.csv"
    assert new_file.read_text() == "district,pop\nA,12\n"
