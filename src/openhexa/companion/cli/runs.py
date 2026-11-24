"""`hexa runs` commands."""

import json
import time

import click

from openhexa.companion import Client, Run
from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._errors import RUN_FAILED_EXIT_CODE
from openhexa.companion.cli._output import (
    cut,
    fetch_limit,
    format_size,
    format_time,
    json_option,
    limit_option,
    nest,
    print_json,
    print_table,
)

# Seconds between two checks of a run that is followed.
POLL_INTERVAL = 5

run_id_argument = click.argument("run_id")


@click.group()
def runs() -> None:
    """Inspect and stop pipeline runs on OpenHEXA.

    Start a run with `hexa pipelines run`. RUN_ID is the ID of a run, as shown by
    `hexa runs list PIPELINE`.
    """


@runs.command("list")
@click.argument("pipeline")
@limit_option("runs", default=20)
@json_option
@pass_client
def list_runs(client: LazyClient, pipeline: str, limit: int, as_json: bool) -> None:
    """List the runs of a pipeline, newest first."""
    items = cut(
        client.get().runs.list(pipeline, limit=fetch_limit(limit)),
        limit,
        empty=f"Pipeline {pipeline} has no runs",
    )
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

    Use `hexa runs logs` for the full output of the pipeline. With --json, prints the
    run with its `messages`, `outputs` and `dataset_versions`.
    """
    library = client.get()
    run = library.runs.get(run_id)
    details = library.runs.details(run_id)
    if as_json:
        print_json(
            nest(
                run,
                messages=details.messages,
                outputs=details.outputs,
                dataset_versions=details.dataset_versions,
            )
        )
        return
    click.echo(f"Run {run.id} of {run.pipeline}, version {run.version or 'unknown'}")
    started = format_time(run.started_at) if run.started_at else "not started"
    click.echo(f"Status: {run.status}, started {started}, {run.duration} s")
    if run.user:
        click.echo(f"Started by: {run.user} ({run.trigger})")
    if run.stopped_by:
        click.echo(f"Stopped by: {run.stopped_by}")
    if run.timeout and run.status in ("failed", "stopped"):
        click.echo(f"Timeout: {run.timeout} s")
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
@json_option
@pass_client
def logs(client: LazyClient, run_id: str, follow: bool, as_json: bool) -> None:
    """Print the logs of a run.

    With --follow, the command exits with code 5 if the run does not succeed. With
    --json, prints the run with its `logs` once it is followed to the end.
    """
    library = client.get()
    if not follow and not as_json:
        click.echo(library.runs.details(run_id).logs, nl=False)
        return
    run = (
        follow_run(library, run_id, print_logs=not as_json) if follow else library.runs.get(run_id)
    )
    if as_json:
        print_json(nest(run, logs=library.runs.details(run_id).logs))
    if follow:
        exit_on_failure(run)


@runs.command()
@run_id_argument
@json_option
@pass_client
def stop(client: LazyClient, run_id: str, as_json: bool) -> None:
    """Stop a run. With --json, prints the run, which is terminating."""
    run = client.get().runs.stop(run_id)
    click.echo(f"Stopping run {run_id}", err=True)
    if as_json:
        print_json(run)


def follow_run(library: Client, run_id: str, *, print_logs: bool) -> Run:
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


def exit_on_failure(run: Run) -> None:
    """Exit with code 5 if the run did not succeed."""
    if run.status != "success":
        raise click.exceptions.Exit(RUN_FAILED_EXIT_CODE)
