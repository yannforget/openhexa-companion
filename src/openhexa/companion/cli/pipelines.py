"""`hexa pipelines` commands."""

import io
import zipfile
from pathlib import Path
from typing import get_args

import click

from openhexa.companion.cli._context import LazyClient, pass_client
from openhexa.companion.cli._output import format_time, json_option, print_json, print_table
from openhexa.companion.cli._prompt import confirm, yes_option
from openhexa.companion.runs import RunStatus

version_option = click.option(
    "--version",
    "version_name",
    help="Version name or number, such as v3. Defaults to the latest version.",
)


@click.group()
def pipelines() -> None:
    """Read and deploy pipelines.

    PIPELINE is the code of a pipeline, as shown by `hexa pipelines list`.
    """


@pipelines.command("list")
@click.option(
    "--last-run-status",
    type=click.Choice(get_args(RunStatus)),
    help="Only list pipelines whose latest run has this status, such as failed.",
)
@json_option
@pass_client
def list_pipelines(client: LazyClient, last_run_status: str | None, as_json: bool) -> None:
    """List the pipelines of the workspace."""
    items = client.get().pipelines.list(last_run_status=last_run_status)
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
    """Show a pipeline version and its parameters."""
    library = client.get()
    item = library.pipelines.get(pipeline)
    version = library.pipelines.version(pipeline, version_name)
    if as_json:
        print_json({"pipeline": item, "version": version})
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
@json_option
@pass_client
def versions(client: LazyClient, pipeline: str, as_json: bool) -> None:
    """List the versions of a pipeline, newest first."""
    items = client.get().pipelines.versions(pipeline)
    if as_json:
        print_json(items)
        return
    print_table(
        [(v.label, format_time(v.created_at), v.description or "") for v in items],
        ("version", "deployed", "description"),
    )


@pipelines.command()
@click.argument("pipeline")
@version_option
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to extract the source in. Defaults to PIPELINE-VERSION.",
)
@click.option("--force", is_flag=True, help="Extract into a directory that already exists.")
@pass_client
def source(
    client: LazyClient,
    *,
    pipeline: str,
    version_name: str | None,
    output: Path | None,
    force: bool,
) -> None:
    """Download the source code of a pipeline version."""
    library = client.get()
    version = library.pipelines.version(pipeline, version_name)
    target = output or Path(f"{pipeline}-v{version.number}")
    if target.exists() and not force:
        raise click.ClickException(f"{target} already exists. Use --force to extract into it.")
    # `extractall` drops absolute paths and `..` parts, so nothing lands outside `target`.
    with zipfile.ZipFile(io.BytesIO(library.pipelines.source(version))) as archive:
        archive.extractall(target)
    click.echo(f"Extracted version {version.label} of {pipeline} to {target}", err=True)


@pipelines.command()
@click.argument("pipeline", required=False)
@click.option(
    "--source",
    "source_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
    show_default=True,
    help="Directory with pipeline.py at its root.",
)
@click.option("--version", "version_name", help="Name of the new version.")
@click.option("--description", help="What changed in this version.")
@click.option(
    "--create",
    "create_name",
    metavar="NAME",
    help="Create a new pipeline named NAME instead of deploying to PIPELINE.",
)
@json_option
@pass_client
def deploy(
    client: LazyClient,
    *,
    pipeline: str | None,
    source_dir: Path,
    version_name: str | None,
    description: str | None,
    create_name: str | None,
    as_json: bool,
) -> None:
    """Deploy the pipeline in --source as a new version of PIPELINE.

    Hidden files and directories (such as .env, .git and .venv), __pycache__ and the
    workspace directory are not sent. Use --create NAME to create a new pipeline instead.
    """
    if (pipeline is None) == (create_name is None):
        raise click.UsageError("Give either PIPELINE or --create NAME")
    library = client.get()
    if pipeline is None:
        version = library.pipelines.create(
            create_name or "", source_dir, description=description, version_name=version_name
        )
    else:
        version = library.pipelines.deploy(
            pipeline, source_dir, version_name=version_name, description=description
        )
    if as_json:
        print_json(version)
        return
    click.echo(f"Deployed version {version.label} of {version.pipeline}", err=True)
    click.echo(version.pipeline)


@pipelines.command()
@click.argument("pipeline")
@yes_option
@pass_client
def delete(client: LazyClient, pipeline: str, yes: bool) -> None:
    """Delete a pipeline with its versions and runs."""
    library = client.get()
    item = library.pipelines.get(pipeline)
    count = len(library.pipelines.versions(pipeline))
    confirm(f"Delete pipeline '{item.code}' and its {count} version(s) and runs", yes=yes)
    library.pipelines.delete(pipeline)
    click.echo(f"Deleted pipeline {item.code}", err=True)
