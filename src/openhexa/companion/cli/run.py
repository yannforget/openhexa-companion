"""`hexa run`: run a pipeline locally, in the image of hosted runs."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import click

from openhexa.companion import Workspace
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._local import LOCAL_WORKSPACE, local_workspace
from openhexa.companion.cli._output import json_option
from openhexa.companion.cli._parameters import parameter_options, read_parameters
from openhexa.companion.pipelines import source_files

# Image of hosted runs when the workspace sets none, unless the instance changes it.
DEFAULT_IMAGE = "blsq/openhexa-blsq-environment:latest"
# Scripts run inside the container: the startup script and the local OpenHEXA API.
RUNTIME = Path(__file__).parent / "_runtime"
# Engines tried in order when none is chosen.
ENGINES = ("docker", "podman")
# Oldest Podman with `--userns=keep-id:uid=...,gid=...`.
MIN_PODMAN = (4, 3)
# Seconds to wait for an engine command that does not run a container.
ENGINE_TIMEOUT = 60
# Seconds a pipeline has to end after Ctrl-C before its container is stopped.
INTERRUPT_GRACE = 10
INTERRUPTED_EXIT_CODE = 130


@dataclass(frozen=True)
class Engine:
    """A container engine command, such as `docker` or `distrobox-host-exec podman`."""

    command: list[str]
    podman: bool
    selinux: bool


@dataclass(frozen=True)
class Mount:
    host: Path
    container: str
    writable: bool


@click.command("run")
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@parameter_options
@click.option("--image", help="Image to run the pipeline in, instead of the workspace's.")
@click.option(
    "--engine",
    "engine_choice",
    metavar="COMMAND",
    help='Container engine command, such as docker, podman or "distrobox-host-exec '
    'podman". Defaults to HEXA_CONTAINER_ENGINE, then the first of docker and podman '
    "that answers.",
)
@click.option(
    "--ignore-local-datasets",
    is_flag=True,
    help="Read every dataset from the workspace, even those with a local copy.",
)
@click.option(
    "--ignore-local-connections",
    is_flag=True,
    help="Read every connection from the workspace, even those with a local file.",
)
@click.option(
    "--allow-remote-writes",
    is_flag=True,
    help="Let the pipeline change the workspace, such as creating dataset versions.",
)
@click.option(
    "--dry-run", is_flag=True, help="Run the checks and print the engine command, then stop."
)
@json_option
@pass_client
def run(
    client: LazyClient,
    *,
    source: Path,
    params: tuple[str, ...],
    params_file: Path | None,
    image: str | None,
    engine_choice: str | None,
    ignore_local_datasets: bool,
    ignore_local_connections: bool,
    allow_remote_writes: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Run the pipeline in SOURCE locally, without deploying it.

    SOURCE is a directory with pipeline.py, the current directory by default. The
    pipeline runs in a container, with the image of hosted runs. It reads the copies of
    workspace files, datasets and connections in .hexa/workspace/, where `hexa files
    download` and `hexa datasets download` save them, and the rest from the workspace.
    It cannot change the workspace unless --allow-remote-writes is given.

    The command exits with the pipeline's exit code, or 130 when interrupted.
    """
    parameters = read_parameters(params, params_file)
    files = source_files(source)
    config = client.config()
    workspace = client.get().get_workspace()
    engine = find_engine(engine_choice or os.environ.get("HEXA_CONTAINER_ENGINE"))
    chosen, reason = choose_image(image, workspace)
    say(f"Image: {chosen} ({reason})")
    present = has_image(engine, chosen)
    datasets = None if ignore_local_datasets else _existing(LOCAL_WORKSPACE / "datasets")
    connections = None if ignore_local_connections else _existing(LOCAL_WORKSPACE / "connections")
    if connections:
        check_connections(connections)
    say(f"Local: {describe_local(datasets, connections)}")
    say(
        "Remote writes: allowed"
        if allow_remote_writes
        else "Remote writes: refused (--allow-remote-writes to allow)"
    )

    run_id = uuid.uuid4().hex[:12]
    run_dir = cache_dir() / "runs" / run_id
    mounts = [
        Mount(run_dir / "pipeline", "/home/jovyan/pipeline", writable=False),
        Mount(LOCAL_WORKSPACE.absolute() / "files", "/home/jovyan/workspace", writable=True),
        Mount(run_dir / "runtime", "/hexa/runtime", writable=False),
        Mount(run_dir / "run", "/hexa/run", writable=True),
    ]
    if datasets:
        mounts.append(Mount(datasets.absolute(), "/hexa/workspace/datasets", writable=True))
    if connections:
        mounts.append(Mount(connections.absolute(), "/hexa/workspace/connections", writable=False))
    name = f"hexa-run-{run_id}"
    command = container_command(engine, chosen, name, mounts)
    if dry_run:
        if not present:
            say(f"The image is not in the {engine_name(engine)} cache: a run would pull it.")
        click.echo(shlex.join(command))
        return

    if not present:
        pull(engine, chosen)
    local_workspace("files")
    stage(source, files, run_dir)
    settings = {
        "openhexa_url": config.url,
        "token": config.token,
        "workspace": config.workspace,
        "run_id": f"local-{run_id}",
        "pipeline_code": source.absolute().name,
        "parameters": parameters,
        "allow_remote_writes": allow_remote_writes,
    }
    started = time.monotonic()
    try:
        code, interrupted = run_container(engine, command, name, settings, quiet_stdout=as_json)
        summary = read_summary(run_dir / "run" / "summary.json")
    finally:
        shutil.rmtree(run_dir)
    exit_code = INTERRUPTED_EXIT_CODE if interrupted else code
    summary = {
        "exit_code": exit_code,
        "status": "interrupted" if interrupted else "success" if code == 0 else "failed",
        "duration_seconds": round(time.monotonic() - started),
        "image": chosen,
        **summary,
    }
    if as_json:
        click.echo(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_summary(summary, datasets)
    if exit_code:
        raise click.exceptions.Exit(exit_code)


def say(message: str) -> None:
    click.echo(message, err=True)


def find_engine(choice: str | None) -> Engine:
    """Find the container engine and what it needs: rootless Podman, SELinux labels.

    Raises:
        click.ClickException: No engine answers, or Podman is too old or not rootless.
    """
    candidates = [shlex.split(choice)] if choice else [[name] for name in ENGINES]
    for command in candidates:
        version = _answer([*command, "version"])
        if version is not None:
            break
    else:
        tried = choice or " and ".join(ENGINES)
        hint = (
            ' Inside a Distrobox or toolbox container, use --engine "distrobox-host-exec '
            'podman" or set HEXA_CONTAINER_ENGINE.'
            if Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()
            else ""
        )
        raise click.ClickException(
            f"No container engine answers: tried {tried}. Install Docker or Podman, and "
            f"check that `{tried.split(' and ')[0]} version` works.{hint}"
        )
    if "podman" not in version.lower():
        options = _answer([*command, "info", "--format", "{{json .SecurityOptions}}"]) or ""
        return Engine(command, podman=False, selinux="selinux" in options)
    found = re.search(r"Version:\s*(\d+)\.(\d+)", version)
    if found and (int(found[1]), int(found[2])) < MIN_PODMAN:
        raise click.ClickException(
            f"Podman {found[1]}.{found[2]} is too old: local runs need Podman "
            f"{'.'.join(map(str, MIN_PODMAN))} or later."
        )
    info = _answer(
        [
            *command,
            "info",
            "--format",
            "{{.Host.Security.Rootless}} {{.Host.Security.SELinuxEnabled}}",
        ]
    )
    rootless, _, selinux = (info or "").strip().partition(" ")
    if rootless != "true":
        raise click.ClickException(
            "Podman runs as root: local runs need rootless Podman, so that the files "
            "the pipeline writes belong to you. Run hexa as your own user."
        )
    return Engine(command, podman=True, selinux=selinux == "true")


def _answer(command: list[str]) -> str | None:
    """Return the output of an engine command, or None if it fails."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=ENGINE_TIMEOUT
        )
    except OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired:
        return None
    return result.stdout


def engine_name(engine: Engine) -> str:
    return "Podman" if engine.podman else "Docker"


def choose_image(option: str | None, workspace: Workspace) -> tuple[str, str]:
    """Return the image to run, fully qualified, and why it was chosen."""
    if option:
        return qualified(option), "--image"
    if workspace.docker_image:
        return qualified(workspace.docker_image), "the workspace's image"
    reason = (
        "default: the workspace sets none, and hosted runs use the instance's default, "
        "which may differ"
    )
    return qualified(DEFAULT_IMAGE), reason


def qualified(image: str) -> str:
    """Prefix Docker Hub to an image without registry, since Podman may not guess it.

    Image IDs, such as `31444761be30`, are left as they are.
    """
    if re.fullmatch(r"(sha256:)?[0-9a-f]{12,64}", image):
        return image
    first, _, rest = image.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        return image
    return f"docker.io/{image}" if rest else f"docker.io/library/{image}"


def has_image(engine: Engine, image: str) -> bool:
    return _answer([*engine.command, "image", "inspect", image]) is not None


def pull(engine: Engine, image: str) -> None:
    say(f"Pulling {image}: several GB, once. Later runs reuse it.")
    try:
        subprocess.run([*engine.command, "pull", "--platform", "linux/amd64", image], check=True)
    except subprocess.CalledProcessError as err:
        raise click.ClickException(
            f"Cannot pull {image} (exit code {err.returncode}). Check the image name and "
            "your connection, or pass a local image with --image."
        ) from err


def cache_dir() -> Path:
    """The user cache directory, where runs are staged."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "openhexa-companion"


def _existing(folder: Path) -> Path | None:
    return folder if folder.is_dir() else None


def describe_local(datasets: Path | None, connections: Path | None) -> str:
    parts = ["files"]
    for folder in sorted(p for p in datasets.iterdir() if p.is_dir()) if datasets else ():
        versions = sorted((p.name for p in folder.iterdir() if p.is_dir()), key=_natural_key)
        parts.append(f"dataset {folder.name} ({versions[-1] if versions else 'no version'})")
    if connections:
        parts += [f"connection {p.stem}" for p in sorted(connections.glob("*.json"))]
    return ", ".join(parts)


def check_connections(folder: Path) -> None:
    """Refuse connection files the SDK could not use, such as ones without secrets.

    Raises:
        click.ClickException: A file is not the JSON of `hexa connections show`, or has
            fields without a value.
    """
    for path in sorted(folder.glob("*.json")):
        fields = _connection_fields(path)
        if fields is None:
            raise click.ClickException(
                f"{path} is not a connection: expected the JSON printed by `hexa "
                f"connections show {path.stem} --show-secrets --json`, with `type` and "
                "`fields`."
            )
        missing = sorted(code for code, value in fields.items() if value is None)
        if missing:
            raise click.ClickException(
                f"{path} has no value for {', '.join(missing)}: secret values are only "
                f"printed with --show-secrets. Save it again with `hexa connections show "
                f"{path.stem} --show-secrets --json > {path}`."
            )


def _connection_fields(path: Path) -> dict[str, Any] | None:
    """The fields of a connection file, or None if it is not one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        return None
    fields = data.get("fields")
    return fields if isinstance(fields, dict) else None


def _natural_key(name: str) -> list[Any]:
    return [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in re.split(r"(\d+)", name)]


def container_command(engine: Engine, image: str, name: str, mounts: list[Mount]) -> list[str]:
    """Build the engine command starting the run; the token is never part of it."""
    command = [
        *engine.command,
        "run",
        "--rm",
        "--interactive",
        "--name",
        name,
        "--platform",
        "linux/amd64",
        "--pull=never",
        "--sig-proxy=false",
        "--entrypoint",
        "python",
    ]
    if engine.podman:
        # Maps the user to the image's `jovyan`, so written files belong to the user.
        command.append("--userns=keep-id:uid=1000,gid=100")
    elif sys.platform == "linux":
        # The `users` group can write to the home folder and /opt/conda of the image.
        command += ["--user", f"{os.getuid()}:{os.getgid()}", "--group-add", "users"]
    for mount in mounts:
        options = ["rw" if mount.writable else "ro"] + (["z"] if engine.selinux else [])
        command += ["--volume", f"{mount.host}:{mount.container}:{','.join(options)}"]
    return [*command, image, "/hexa/runtime/start.py"]


def stage(source: Path, files: list[Path], run_dir: Path) -> None:
    """Copy the pipeline files `deploy` would send and the runtime scripts into `run_dir`."""
    for path in files:
        target = run_dir / "pipeline" / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    (run_dir / "runtime").mkdir()
    for script in ("start.py", "server.py"):
        shutil.copy2(RUNTIME / script, run_dir / "runtime" / script)
    (run_dir / "run").mkdir()


def run_container(
    engine: Engine,
    command: list[str],
    name: str,
    settings: dict[str, Any],
    *,
    quiet_stdout: bool,
) -> tuple[int, bool]:
    """Run the container, passing `settings` on stdin, and return its exit code.

    The output of the run is streamed to stdout, or to stderr with `quiet_stdout`.
    The engine runs in a session of its own, so Ctrl-C reaches only this process. It
    then sends SIGINT to the pipeline, so that its `finally` blocks run, and stops the
    container if the pipeline has not ended after `INTERRUPT_GRACE` seconds.

    Returns:
        The exit code, and whether the run was interrupted.
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    stdin = process.stdin
    if stdin is None:
        raise RuntimeError("The engine was started without stdin")
    try:
        stdin.write(json.dumps(settings) + "\n")
        stdin.close()
    except BrokenPipeError:
        # The container did not start; the engine prints why.
        pass
    reader = threading.Thread(target=_copy, args=(process.stdout, quiet_stdout), daemon=True)
    reader.start()
    try:
        return _wait(process, reader), False
    except KeyboardInterrupt:
        say("Interrupting the run...")
        subprocess.run(
            [*engine.command, "kill", "--signal=INT", name],
            capture_output=True,
            check=False,
            timeout=ENGINE_TIMEOUT,
        )
        try:
            return _wait(process, reader, timeout=INTERRUPT_GRACE), True
        except subprocess.TimeoutExpired:
            say("The pipeline did not stop: stopping its container.")
            subprocess.run(
                [*engine.command, "stop", "-t", "5", name],
                capture_output=True,
                check=False,
                timeout=ENGINE_TIMEOUT,
            )
            return _wait(process, reader), True


def _copy(source: IO[str] | None, to_stderr: bool) -> None:
    for line in source or ():
        click.echo(line, nl=False, err=to_stderr)


def _wait(
    process: subprocess.Popen[str], reader: threading.Thread, timeout: float | None = None
) -> int:
    """Wait for the engine to exit and for its output to be copied."""
    code = process.wait(timeout=timeout)
    reader.join()
    return code


def read_summary(path: Path) -> dict[str, Any]:
    """Read what the run reported, or note that it ended before writing it."""
    if not path.is_file():
        return {"complete": False}
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary.pop("exit_code", None)
    summary.pop("duration_seconds", None)
    return {"complete": True, **summary}


def print_summary(summary: dict[str, Any], datasets: Path | None) -> None:
    status, duration = summary["status"], summary["duration_seconds"]
    if status == "success":
        say(f"Run succeeded in {duration} s.")
    elif status == "interrupted":
        say(f"Run interrupted after {duration} s.")
    else:
        say(f"Run failed (exit code {summary['exit_code']}) in {duration} s.")
    if not summary["complete"]:
        say("  The container ended before the pipeline: see the output above.")
        return
    counts = Counter(m["priority"].lower() for m in summary["messages"])
    if counts:
        say("  Messages: " + ", ".join(f"{n} {level}" for level, n in counts.items()))
    if summary["outputs"]:
        say("  Outputs: " + ", ".join(o["name"] for o in summary["outputs"]))
    if summary["new_versions"]:
        items = []
        for item in summary["new_versions"]:
            folder = (datasets or Path()) / item["dataset"] / item["version"]
            count = sum(1 for p in folder.rglob("*") if p.is_file())
            items.append(f"{item['dataset']} {item['version']} ({count} files)")
        say("  New local versions: " + ", ".join(items))
    if summary["refused"]:
        say("  Refused remote writes: " + ", ".join(summary["refused"]))
