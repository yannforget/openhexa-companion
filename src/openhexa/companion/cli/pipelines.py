"""`hexa pipelines` commands."""

import io
import zipfile
from pathlib import Path
from typing import get_args

import click

from openhexa.companion import NotFoundError, PipelineVersion
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._local import check_targets, local_path, save_stream
from openhexa.companion.cli._output import (
    cut,
    fetch_limit,
    format_time,
    json_option,
    limit_option,
    nest,
    print_json,
    print_table,
)
from openhexa.companion.cli._parameters import parameter_options, read_parameters
from openhexa.companion.cli._prompt import confirm, yes_option
from openhexa.companion.cli.runs import exit_on_failure, follow_run
from openhexa.companion.runs import RunStatus

version_option = click.option(
    "--version",
    "version_name",
    help="Version name or number, such as v3. Defaults to the latest version.",
)


@click.group()
def pipelines() -> None:
    """Read, publish and run pipelines on OpenHEXA.

    PIPELINE is the code of a pipeline, as shown by `hexa pipelines list`.
    """


@pipelines.command("list")
@click.option(
    "--last-run-status",
    type=click.Choice(get_args(RunStatus)),
    help="Only list pipelines whose latest run has this status, such as failed.",
)
@limit_option("pipelines")
@json_option
@pass_client
def list_pipelines(
    client: LazyClient, last_run_status: str | None, limit: int | None, as_json: bool
) -> None:
    """List the pipelines of the workspace."""
    library = client.get()
    items = cut(
        library.pipelines.list(last_run_status=last_run_status, limit=fetch_limit(limit)),
        limit,
        empty=f"No pipelines in workspace {library.workspace}"
        + (f" whose last run is {last_run_status}" if last_run_status else ""),
    )
    if as_json:
        print_json(items)
        return
    print_table(
        [(p.code, p.current_version or "", p.schedule or "", p.name) for p in items],
        ("pipeline", "version", "schedule", "name"),
    )


@pipelines.command()
@click.argument("pipeline")
@version_option
@json_option
@pass_client
def show(client: LazyClient, pipeline: str, version_name: str | None, as_json: bool) -> None:
    """Show a pipeline and a version with its parameters.

    With --json, prints the pipeline with the version as `version`.
    """
    library = client.get()
    item = library.pipelines.get(pipeline)
    version = library.pipelines.version(pipeline, version_name)
    if as_json:
        print_json(nest(item, version=version))
        return
    click.echo(f"{item.name} ({item.code})")
    if item.schedule:
        click.echo(f"Schedule: {item.schedule}")
    click.echo(f"Version: {version.label}, deployed {format_time(version.created_at)}")
    if version.timeout:
        click.echo(f"Timeout: {version.timeout} s")
    if item.description:
        click.echo()
        click.echo(item.description)
    if version.parameters:
        click.echo()
        print_table(
            [
                (
                    p.code,
                    p.type + ("[]" if p.multiple else ""),
                    "yes" if p.required else "no",
                    version.config.get(p.code, p.default),
                    p.help or "",
                )
                for p in version.parameters
            ],
            ("parameter", "type", "required", "default", "help"),
        )


@pipelines.command()
@click.argument("pipeline")
@version_option
@parameter_options
@click.option("--wait", is_flag=True, help="Wait for the run to end, printing its logs.")
@json_option
@pass_client
def run(
    client: LazyClient,
    *,
    pipeline: str,
    version_name: str | None,
    params: tuple[str, ...],
    params_file: Path | None,
    wait: bool,
    as_json: bool,
) -> None:
    """Start a run of PIPELINE on OpenHEXA and print its ID.

    To run code from a local folder on this machine, use `hexa local run`. Parameters
    without a value use the value configured in OpenHEXA, then the default declared in
    the code. Follow the run with `hexa runs logs RUN_ID -f`. With --wait, the command
    exits with code 5 if the run does not succeed. With --json, prints the run.
    """
    parameters = read_parameters(params, params_file)
    library = client.get()
    try:
        version = library.pipelines.version(pipeline, version_name)
    except NotFoundError as err:
        # A pipeline folder named like a published pipeline, such as one saved by
        # `pipelines source`, must not hide it, so the folder is only checked now.
        if Path(pipeline).is_dir():
            raise click.UsageError(
                f"{err}. {pipeline} is a local folder: `hexa pipelines run` runs a pipeline "
                "published on OpenHEXA; use `hexa local run` to run local code."
            ) from err
        raise
    started = library.runs.start(version, parameters)
    click.echo(f"Started run {started.id} of {pipeline} version {version.label}", err=True)
    result = follow_run(library, started.id, print_logs=not as_json) if wait else started
    if as_json:
        print_json(result)
    else:
        click.echo(result.id)
    if wait:
        exit_on_failure(result)


@pipelines.command()
@click.argument("pipeline")
@version_option
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the source in. Defaults to PIPELINE.",
)
@click.option("--force", is_flag=True, help="Replace local files that already exist.")
@json_option
@pass_client
def source(
    client: LazyClient,
    *,
    pipeline: str,
    version_name: str | None,
    output: Path | None,
    force: bool,
    as_json: bool,
) -> None:
    """Download the source code of a pipeline version.

    Files go to a folder named after the pipeline, which `hexa local run` takes as the
    pipeline code. Existing local files are left untouched unless --force is given; the
    command stops before writing anything if one exists. With --json, prints the paths
    of the files written.
    """
    library = client.get()
    version = library.pipelines.version(pipeline, version_name)
    target_dir = output or Path(pipeline)
    with zipfile.ZipFile(io.BytesIO(library.pipelines.source(version))) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        targets = [(member, local_path(target_dir, member.filename)) for member in members]
        check_targets((target for _, target in targets), force=force)
        for member, target in targets:
            save_stream([archive.read(member)], target)
    click.echo(f"Saved version {version.label} of {pipeline} to {target_dir}", err=True)
    if as_json:
        print_json([str(target) for _, target in targets])


source_argument = click.argument(
    "source_dir",
    metavar="[SOURCE]",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
SOURCE_HELP = (
    "SOURCE is a directory with pipeline.py at its root, the current directory by "
    "default. Hidden files and directories (such as .env, .git and .venv), __pycache__ "
    "and the workspace directory are not sent."
)


@pipelines.command()
@click.argument("name")
@source_argument
@click.option("-d", "--description", help="What the pipeline does.")
@click.option("--name", "version_name", help="Name of the first version. Defaults to v1.")
@click.option("-m", "--message", help="Description of the first version.")
@json_option
@pass_client
def create(
    client: LazyClient,
    *,
    name: str,
    source_dir: Path,
    description: str | None,
    version_name: str | None,
    message: str | None,
    as_json: bool,
) -> None:
    """Create a pipeline named NAME from the code in SOURCE and print its code.

    OpenHEXA derives the pipeline code from NAME. The code in SOURCE becomes the first
    version. With --json, prints the first version, whose `pipeline` is the code.
    """
    version = client.get().pipelines.create(
        name,
        source_dir,
        description=description,
        version_name=version_name,
        version_description=message,
    )
    _report(version, "Created pipeline", as_json=as_json)


@pipelines.command()
@click.argument("pipeline")
@source_argument
@click.option("--name", "version_name", help="Name of the new version. Defaults to its number.")
@click.option("-m", "--message", help="What changed in this version.")
@json_option
@pass_client
def publish(
    client: LazyClient,
    *,
    pipeline: str,
    source_dir: Path,
    version_name: str | None,
    message: str | None,
    as_json: bool,
) -> None:
    """Deploy the code in SOURCE as a new version of PIPELINE.

    This is the deployment: the new version becomes the latest, which runs and
    schedules use unless they name another. With --json, prints the version.
    """
    version = client.get().pipelines.deploy(
        pipeline, source_dir, version_name=version_name, description=message
    )
    _report(version, "Published", as_json=as_json)


def _report(version: PipelineVersion, action: str, *, as_json: bool) -> None:
    if as_json:
        print_json(version)
        return
    click.echo(f"{action} version {version.label} of {version.pipeline}", err=True)
    click.echo(version.pipeline)


@pipelines.command()
@click.argument("pipeline")
@yes_option
@json_option
@pass_client
def delete(client: LazyClient, pipeline: str, yes: bool, as_json: bool) -> None:
    """Delete a pipeline with its versions and runs. With --json, prints the pipeline."""
    library = client.get()
    item = library.pipelines.get(pipeline)
    count = len(library.pipelines.versions(pipeline))
    confirm(f"Delete pipeline '{item.code}' and its {count} version(s) and runs", yes=yes)
    library.pipelines.delete(pipeline)
    click.echo(f"Deleted pipeline {item.code}", err=True)
    if as_json:
        print_json(item)


@pipelines.group()
def versions() -> None:
    """List and delete the versions of a pipeline.

    VERSION is the name or number of a version, such as v3, as shown by `hexa pipelines
    versions list`.
    """


@versions.command("list")
@click.argument("pipeline")
@limit_option("versions", default=20)
@json_option
@pass_client
def list_versions(client: LazyClient, pipeline: str, limit: int, as_json: bool) -> None:
    """List the versions of a pipeline, newest first."""
    items = cut(
        client.get().pipelines.versions(pipeline, limit=fetch_limit(limit)),
        limit,
        empty=f"Pipeline {pipeline} has no version yet",
    )
    if as_json:
        print_json(items)
        return
    print_table(
        [(v.label, format_time(v.created_at), v.description or "") for v in items],
        ("version", "deployed", "message"),
    )


@versions.command("delete")
@click.argument("pipeline")
@click.argument("version_name", metavar="VERSION")
@yes_option
@json_option
@pass_client
def delete_version(
    client: LazyClient, pipeline: str, version_name: str, yes: bool, as_json: bool
) -> None:
    """Delete a version of a pipeline with its runs.

    Deleting the latest version makes the previous one the latest. The only version of
    a pipeline cannot be deleted: delete the pipeline instead. With --json, prints the
    version.
    """
    library = client.get()
    version = library.pipelines.version(pipeline, version_name)
    confirm(f"Delete version '{version.label}' of pipeline '{pipeline}' and its runs", yes=yes)
    library.pipelines.delete_version(version)
    click.echo(f"Deleted version {version.label} of pipeline {pipeline}", err=True)
    if as_json:
        print_json(version)
