"""`hexa runs` commands."""

import json
import time
from pathlib import Path

import click

from openhexa.companion import Client, Run
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import (
    format_size,
    format_time,
    json_option,
    print_json,
    print_table,
)
from openhexa.companion.cli._parameters import parameter_options, read_parameters

# Seconds between two checks of a run that is followed.
POLL_INTERVAL = 5
# Exit code of `start --wait` and `logs --follow` when the run did not succeed.
RUN_FAILED_EXIT_CODE = 2

run_id_argument = click.argument("run_id")


@click.group()
def runs() -> None:
    """Start, stop and inspect pipeline runs.

    RUN_ID is the ID of a run, as shown by `hexa runs list PIPELINE`.
    """


@runs.command("list")
@click.argument("pipeline")
@click.option("-n", "--limit", default=20, show_default=True, help="Number of runs to show.")
@json_option
@pass_client
def list_runs(client: LazyClient, pipeline: str, limit: int, as_json: bool) -> None:
    """List the runs of a pipeline, newest first."""
    items = client.get().runs.list(pipeline, limit=limit)
    if as_json:
        print_json(items)
        return
    print_table(
        [
            (
                r.id,
                r.status,
                format_time(r.started_at) if r.started_at else "",
                f"{r.duration} s",
                r.version or "",
                r.trigger or "",
            )
            for r in items
        ],
        ("run", "status", "started", "duration", "version", "trigger"),
    )


@runs.command()
@run_id_argument
@json_option
@pass_client
def show(client: LazyClient, run_id: str, as_json: bool) -> None:
    """Show a run: status, parameters, messages and outputs.

    Use `hexa runs logs` for the full output of the pipeline.
    """
    library = client.get()
    run = library.runs.get(run_id)
    details = library.runs.details(run_id)
    if as_json:
        print_json(
            {
                "run": run,
                "messages": details.messages,
                "outputs": details.outputs,
                "dataset_versions": details.dataset_versions,
            }
        )
        return
    click.echo(f"Run {run.id} of {run.pipeline}, version {run.version or 'unknown'}")
    started = format_time(run.started_at) if run.started_at else "not started"
    click.echo(f"Status: {run.status}, started {started}, {run.duration} s")
    if run.user:
        click.echo(f"Started by: {run.user} ({run.trigger})")
    if run.parameters:
        click.echo("Parameters:")
        for code, value in run.parameters.items():
            click.echo(f"  {code}={json.dumps(value, ensure_ascii=False)}")
    if details.messages:
        click.echo("Messages:")
        for message in details.messages:
            click.echo(f"  {message.priority:<8} {message.message}")
    if details.outputs:
        click.echo("Outputs:")
        for output in details.outputs:
            size = f" ({format_size(output.size)})" if output.size is not None else ""
            click.echo(f"  {output.type:<6} {output.name}{size}")
    for item in details.dataset_versions:
        click.echo(f"Published dataset {item.workspace}/{item.dataset} version {item.version}")


@runs.command()
@run_id_argument
@click.option("-f", "--follow", is_flag=True, help="Keep printing new logs until the run ends.")
@pass_client
def logs(client: LazyClient, run_id: str, follow: bool) -> None:
    """Print the logs of a run.

    With --follow, the command exits with code 2 if the run does not succeed.
    """
    library = client.get()
    if not follow:
        click.echo(library.runs.details(run_id).logs, nl=False)
        return
    run = _follow(library, run_id, print_logs=True)
    _exit_on_failure(run)


@runs.command()
@click.argument("pipeline")
@click.option(
    "--version",
    "version_name",
    help="Version name or number, such as v3. Defaults to the latest version.",
)
@parameter_options
@click.option("--wait", is_flag=True, help="Wait for the run to end, printing its logs.")
@json_option
@pass_client
def start(
    client: LazyClient,
    *,
    pipeline: str,
    version_name: str | None,
    params: tuple[str, ...],
    params_file: Path | None,
    wait: bool,
    as_json: bool,
) -> None:
    """Start a run of PIPELINE and print its ID.

    Parameters without a value use the value configured in OpenHEXA, then the default
    declared in the code. With --wait, the command exits with code 2 if the run does
    not succeed.
    """
    parameters = read_parameters(params, params_file)
    library = client.get()
    version = library.pipelines.version(pipeline, version_name)
    run = library.runs.start(version, parameters)
    click.echo(f"Started run {run.id} of {pipeline} version {version.label}", err=True)
    if wait:
        run = _follow(library, run.id, print_logs=not as_json)
    if as_json:
        print_json(run)
    else:
        click.echo(run.id)
    if wait:
        _exit_on_failure(run)


@runs.command()
@run_id_argument
@pass_client
def stop(client: LazyClient, run_id: str) -> None:
    """Stop a run."""
    client.get().runs.stop(run_id)
    click.echo(f"Stopping run {run_id}", err=True)


def _follow(library: Client, run_id: str, *, print_logs: bool) -> Run:
    """Poll a run until it ends, printing new logs to stderr, and return it."""
    printed = 0
    status = None
    while True:
        run = library.runs.get(run_id)
        if print_logs:
            logs = library.runs.details(run_id).logs
            # Logs only grow; print what was added since the last check.
            click.echo(logs[printed:], nl=False, err=True)
            printed = len(logs)
        if run.status != status:
            click.echo(f"[{run.status}]", err=True)
            status = run.status
        if run.finished:
            return run
        time.sleep(POLL_INTERVAL)


def _exit_on_failure(run: Run) -> None:
    if run.status != "success":
        raise click.exceptions.Exit(RUN_FAILED_EXIT_CODE)
