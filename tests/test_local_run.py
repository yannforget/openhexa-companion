"""`hexa local run --dry-run`, with a fake container engine on PATH and a fake OpenHEXA."""

import shlex
from pathlib import Path

import httpx2
import pytest
from click.testing import CliRunner

from openhexa.companion import Client
from openhexa.companion.cli import main
from openhexa.companion.cli._context import LazyClient

TOKEN = "real-token-0123456789"
VERSIONS = {
    "docker": "Client: Docker Engine - Community\n Version: 29.0.0\nServer: Docker Engine\n",
    "podman": "Client: Podman Engine\nVersion: 5.8.9\n",
}


def _fake_engine(bin_dir: Path, name: str, *, has_image: bool) -> None:
    """Write an engine script answering `version`, `info` and `image inspect`."""
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  version) printf %s {shlex.quote(VERSIONS[name])} ;;\n"
        "  info) echo 'true true' ;;\n"
        f"  image) exit {0 if has_image else 1} ;;\n"
        "  *) exit 99 ;;\n"
        "esac\n"
    )
    script.chmod(0o755)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pipe").mkdir()
    (tmp_path / "pipe/pipeline.py").write_text("")
    (tmp_path / ".hexa/workspace/datasets/population/v4").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.delenv("HEXA_CONTAINER_ENGINE", raising=False)
    for key, value in {
        "OPENHEXA_URL": "https://openhexa.test",
        "OPENHEXA_WORKSPACE": "ws",
        "OPENHEXA_TOKEN": TOKEN,
    }.items():
        monkeypatch.setenv(key, value)
    workspace = {"slug": "ws", "name": "WS", "description": "", "configuration": {}}
    response = {"data": {"workspace": {**workspace, "dockerImage": "blsq/custom:1.0"}}}
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(200, json=response))
    client = Client("https://openhexa.test", "ws", TOKEN, http_transport=transport)
    monkeypatch.setattr(LazyClient, "get", lambda _self: client)
    return tmp_path


@pytest.mark.parametrize("engine", ["docker", "podman"])
def test_dry_run_prints_the_engine_command(project: Path, engine: str):
    _fake_engine(project / "bin", engine, has_image=True)

    result = CliRunner().invoke(main, ["local", "run", "pipe", "--dry-run", "-p", "country=BFA"])

    assert result.exit_code == 0, result.output
    assert TOKEN not in result.output
    assert "Image: docker.io/blsq/custom:1.0 (the workspace's image)" in result.stderr
    assert "dataset population (v4)" in result.stderr
    command = shlex.split(result.stdout.strip())
    assert command[:2] == [engine, "run"]
    assert command[-2:] == ["docker.io/blsq/custom:1.0", "/hexa/runtime/start.py"]
    user_flag = "--userns=keep-id:uid=1000,gid=100" if engine == "podman" else "--group-add"
    assert user_flag in command
    assert any(v.endswith(":/hexa/workspace/datasets:rw,z") for v in command) == (
        engine == "podman"
    )


def test_dry_run_says_when_the_image_would_be_pulled(project: Path):
    _fake_engine(project / "bin", "podman", has_image=False)

    result = CliRunner().invoke(
        main, ["local", "run", "pipe", "--dry-run", "--image", "my/image:2"]
    )

    assert result.exit_code == 0, result.output
    assert "Image: docker.io/my/image:2 (--image)" in result.stderr
    assert "a run would pull it" in result.stderr
    assert not (project / ".hexa/workspace/files").exists()


def test_ignored_local_datasets_are_not_mounted(project: Path):
    _fake_engine(project / "bin", "docker", has_image=True)

    result = CliRunner().invoke(
        main, ["local", "run", "pipe", "--dry-run", "--ignore-local-datasets"]
    )

    assert result.exit_code == 0, result.output
    assert "/hexa/workspace/datasets" not in result.stdout


def test_no_engine_is_a_clean_error(project: Path):
    result = CliRunner().invoke(main, ["local", "run", "pipe", "--dry-run"])

    assert result.exit_code == 1
    assert "No container engine answers: tried docker and podman" in result.output


def test_connection_files_without_secrets_are_refused(project: Path):
    _fake_engine(project / "bin", "docker", has_image=True)
    folder = project / ".hexa/workspace/connections"
    folder.mkdir()
    (folder / "dhis2.json").write_text('{"type": "DHIS2", "fields": {"password": null}}')

    result = CliRunner().invoke(main, ["local", "run", "pipe", "--dry-run"])

    assert result.exit_code == 1
    assert "has no value for password" in result.output
    assert "hexa local pull connections/dhis2 --force" in result.output
