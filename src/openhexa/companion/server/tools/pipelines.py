"""Tools reading pipelines, their runs and run logs."""

from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, JsonValue

from openhexa.companion import Client, Pipeline, PipelineVersion, Run, RunDetails, SourceFile
from openhexa.companion._util import format_options
from openhexa.companion.runs import RunStatus
from openhexa.companion.server.tools._access import Access
from openhexa.companion.server.tools._shared import (
    CREATE,
    DESTRUCTIVE,
    READ_ONLY,
    PipelineArg,
    RunArg,
    WorkspaceArg,
    is_uuid,
    truncation_note,
)

# Versions listed with a pipeline when no limit is given.
DEFAULT_VERSION_LIMIT = 10
# Runs listed when no limit is given.
DEFAULT_RUN_LIMIT = 20
# Run messages returned when no limit is given, the latest ones.
DEFAULT_MESSAGE_LIMIT = 100
# Characters of logs returned when no length is given.
DEFAULT_LOG_LENGTH = 20_000
# Characters of source code returned by one call.
MAX_SOURCE_LENGTH = 50_000
# Replaces the values of secret parameters, which OpenHEXA returns in clear.
SECRET_MASK = "<secret>"
# Parameter type whose values are hidden.
SECRET_TYPE = "secret"
# Note returned with a run just started or stopped.
RUN_STATUS_NOTE = (
    "The run continues in OpenHEXA; openhexa_get_pipeline_run returns its current status, "
    "messages and outputs."
)

PipelineVersionArg = Annotated[
    str | None,
    Field(description="Version label, name or ID, such as 'v3'. Defaults to the latest version."),
]


class PipelineSummary(BaseModel):
    code: str = Field(description="Identifies the pipeline in other tools.")
    name: str = Field(description="Display name.")
    description: str = Field(description="What the pipeline does. Empty if undocumented.")
    type: str = Field(
        description="'zipFile' for pipelines deployed from code, 'notebook' for those "
        "running a notebook of the workspace."
    )
    schedule: str | None = Field(description="Cron expression of the schedule, if scheduled.")
    current_version: str | None = Field(
        description="Label of the latest version, such as 'v3' or 'fix [v3]', or null if "
        "none was deployed."
    )


class PipelineList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    pipelines: list[PipelineSummary] = Field(description="Pipelines of the workspace, by name.")


class ParameterInfo(BaseModel):
    code: str = Field(description="Name used to pass a value when starting a run.")
    name: str = Field(description="Display name.")
    type: str = Field(description="Type, such as 'str', 'int', 'bool', 'dataset' or 'secret'.")
    required: bool = Field(description="Whether a run needs a value.")
    multiple: bool = Field(description="Whether the parameter takes a list of values.")
    default: Any = Field(description="Value used when a run gives none. Secrets are masked.")
    choices: list[Any] | None = Field(description="Allowed values, if restricted.")
    help: str | None = Field(description="Explanation of the parameter, if documented.")


class VersionSummary(BaseModel):
    label: str = Field(description="Name of the version as OpenHEXA shows it, such as 'v3'.")
    version_id: str = Field(description="OpenHEXA ID of the version.")
    description: str | None = Field(description="What changed in this version, if documented.")
    created_at: datetime = Field(description="When the version was deployed.")


class VersionDetails(VersionSummary):
    timeout: int | None = Field(
        description="Seconds after which runs are stopped, or null for the default."
    )
    parameters: list[ParameterInfo] = Field(description="Parameters declared by the version.")
    configured_values: dict[str, Any] = Field(
        description="Parameter values configured in OpenHEXA, used by runs that give no "
        "other value. Secrets are masked."
    )


class PipelineDetails(PipelineSummary):
    workspace: str = Field(description="Slug of the workspace.")
    version: VersionDetails | None = Field(
        description="The requested version, the latest by default; null if none was deployed."
    )
    versions: list[VersionSummary] = Field(description="Versions, newest first.")
    total_versions: int = Field(description="Number of versions of the pipeline.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


class RunSummary(BaseModel):
    run_id: str = Field(description="Identifies the run in other tools.")
    status: str = Field(
        description="One of 'queued', 'running', 'terminating', 'success', 'failed', "
        "'stopped' or 'skipped'."
    )
    started_at: datetime | None = Field(description="When the run started, if it has.")
    duration: int = Field(description="Seconds the run took or has taken so far.")
    trigger: str | None = Field(description="'manual', 'scheduled' or 'webhook'.")
    progress: int = Field(description="Progress reported by the pipeline, from 0 to 100.")
    version: str | None = Field(description="Label of the version that ran.")
    version_id: str | None = Field(description="OpenHEXA ID of the version that ran.")
    user: str | None = Field(description="Who started the run, if a person did.")


class RunList(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    pipeline: str = Field(description="Code of the pipeline.")
    runs: list[RunSummary] = Field(description="Runs, newest first.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


class RunMessageInfo(BaseModel):
    priority: str = Field(description="'DEBUG', 'INFO', 'WARNING', 'ERROR' or 'CRITICAL'.")
    message: str = Field(description="The message.")
    timestamp: datetime | None = Field(description="When it was logged, if known.")


class RunOutputInfo(BaseModel):
    type: str = Field(
        description="'file' for a workspace file, 'table' for a workspace database table, "
        "or a type given by the pipeline."
    )
    name: str = Field(description="Path of the file, name of the table, or a given name.")
    size: int | None = Field(description="Size of the file in bytes, if a file.")


class PublishedVersion(BaseModel):
    dataset: str = Field(
        description="The dataset, as the dataset tools take it: its slug, or "
        "'workspace/slug' if another workspace owns it."
    )
    owner_workspace: str = Field(description="Slug of the workspace that owns the dataset.")
    version: str = Field(description="Name of the version.")
    version_id: str = Field(description="OpenHEXA ID of the version.")


class RunInfo(RunSummary):
    workspace: str = Field(description="Slug of the workspace.")
    pipeline: str = Field(description="Code of the pipeline.")
    finished: bool = Field(description="Whether the status will no longer change.")
    parameters: dict[str, Any] = Field(
        description="Parameter values of the run. Secrets are masked."
    )
    messages: list[RunMessageInfo] = Field(
        description="Messages the pipeline logged with current_run.log_*, oldest first."
    )
    outputs: list[RunOutputInfo] = Field(description="Files and tables declared as outputs.")
    published_dataset_versions: list[PublishedVersion] = Field(
        description="Dataset versions the run published."
    )
    log_length: int = Field(description="Number of characters of logs, read with run logs.")
    timeout: int | None = Field(
        description="Seconds the run may last before OpenHEXA stops it. A failed run whose "
        "duration reaches it was most likely stopped for taking too long."
    )
    stopped_by: str | None = Field(description="Who stopped the run, if someone did.")
    note: str | None = Field(description="Explains how to get more results, if truncated.")


class RunChange(RunSummary):
    workspace: str = Field(description="Slug of the workspace.")
    pipeline: str = Field(description="Code of the pipeline.")
    parameters: dict[str, Any] = Field(
        description="Parameter values of the run. Secrets are masked."
    )
    note: str = Field(description="How to follow the run.")


class SourceFileInfo(BaseModel):
    path: str = Field(description="Path of the file in the source, such as 'pipeline.py'.")
    size: int = Field(description="Size in bytes.")
    content: str | None = Field(
        description="Content of the file, or null if not returned or not text."
    )


class PipelineSource(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    pipeline: str = Field(description="Code of the pipeline.")
    version: str = Field(description="Label of the version, such as 'v3'.")
    version_id: str = Field(description="OpenHEXA ID of the version.")
    files: list[SourceFileInfo] = Field(description="Files of the version.")
    note: str | None = Field(description="How to read content not returned, if any.")


class RunLogs(BaseModel):
    workspace: str = Field(description="Slug of the workspace.")
    run_id: str = Field(description="ID of the run.")
    status: str = Field(description="Status of the run; logs grow while it is in progress.")
    logs: str = Field(description="The requested part of the logs.")
    offset: int = Field(description="Position of the first returned character in the logs.")
    total_length: int = Field(description="Number of characters of the whole logs.")
    note: str | None = Field(description="How to read the parts not returned, if any.")


def register(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_list_pipelines",
        title="List OpenHEXA pipelines",
        annotations=READ_ONLY,
    )
    async def list_pipelines(
        workspace: WorkspaceArg,
        last_run_status: Annotated[
            RunStatus | None,
            Field(
                description="Only list pipelines whose latest run has this status, such as "
                "'failed' to find the pipelines that are currently failing."
            ),
        ] = None,
    ) -> PipelineList:
        """List the pipelines of an OpenHEXA workspace, with their description and schedule."""
        items = await access.run(
            workspace, lambda client: client.pipelines.list(last_run_status=last_run_status)
        )
        return PipelineList(workspace=workspace, pipelines=[_pipeline(p) for p in items])

    @server.tool(
        name="openhexa_get_pipeline",
        title="Get an OpenHEXA pipeline",
        annotations=READ_ONLY,
    )
    async def get_pipeline(
        workspace: WorkspaceArg,
        pipeline: PipelineArg,
        version: PipelineVersionArg = None,
        version_limit: Annotated[
            int, Field(ge=1, description="Maximum number of versions to list, newest first.")
        ] = DEFAULT_VERSION_LIMIT,
    ) -> PipelineDetails:
        """Read an OpenHEXA pipeline: a version with its parameters, and its versions."""

        def read(
            client: Client,
        ) -> tuple[Pipeline, list[PipelineVersion], PipelineVersion | None]:
            found = client.pipelines.get(pipeline)
            versions = client.pipelines.versions(pipeline)
            if version is None:
                return found, versions, versions[0] if versions else None
            return found, versions, _find_version(client, pipeline, version, versions)

        found, versions, selected = await access.run(workspace, read)
        return PipelineDetails(
            **_pipeline(found).model_dump(),
            workspace=workspace,
            version=_version_details(selected) if selected else None,
            versions=[_version_summary(v) for v in versions[:version_limit]],
            total_versions=len(versions),
            note=truncation_note(len(versions), version_limit, "versions", "version_limit"),
        )

    @server.tool(
        name="openhexa_list_pipeline_runs",
        title="List runs of an OpenHEXA pipeline",
        annotations=READ_ONLY,
    )
    async def list_pipeline_runs(
        workspace: WorkspaceArg,
        pipeline: PipelineArg,
        limit: Annotated[
            int, Field(ge=1, description="Maximum number of runs to return, newest first.")
        ] = DEFAULT_RUN_LIMIT,
    ) -> RunList:
        """List the runs of an OpenHEXA pipeline, newest first, with their status."""
        items = await access.run(workspace, lambda client: client.runs.list(pipeline, limit=limit))
        note = None
        if len(items) == limit:
            note = (
                f"Showing the latest {limit} runs; there may be older ones. Call again with "
                "a higher limit to see them."
            )
        return RunList(
            workspace=workspace,
            pipeline=pipeline,
            runs=[RunSummary(**_run_fields(r)) for r in items],
            note=note,
        )

    @server.tool(
        name="openhexa_get_pipeline_run",
        title="Get an OpenHEXA pipeline run",
        annotations=READ_ONLY,
    )
    async def get_pipeline_run(
        workspace: WorkspaceArg,
        run: RunArg,
        message_limit: Annotated[
            int, Field(ge=1, description="Maximum number of messages to return, the latest ones.")
        ] = DEFAULT_MESSAGE_LIMIT,
    ) -> RunInfo:
        """Read an OpenHEXA pipeline run: status, parameters, messages and outputs.

        Includes the version that ran and the dataset versions the run published, but
        not the logs, which openhexa_get_pipeline_run_logs returns. To read the code that
        ran, pass version_id to openhexa_get_pipeline_source.
        """

        def read(client: Client) -> tuple[Run, RunDetails, set[str]]:
            found = client.runs.get(run)
            return found, client.runs.details(run), _run_secret_codes(client, found)

        found, details, secrets = await access.run(workspace, read)
        messages = details.messages[-message_limit:]
        note = None
        if len(details.messages) > message_limit:
            note = (
                f"Showing the last {message_limit} of {len(details.messages)} messages. "
                "Call again with a higher message_limit to see earlier ones."
            )
        return RunInfo(
            **_run_fields(found),
            workspace=workspace,
            pipeline=found.pipeline,
            finished=found.finished,
            parameters=_mask(found.parameters, secrets),
            messages=[
                RunMessageInfo(priority=m.priority, message=m.message, timestamp=m.timestamp)
                for m in messages
            ],
            outputs=[RunOutputInfo(type=o.type, name=o.name, size=o.size) for o in details.outputs],
            published_dataset_versions=[
                PublishedVersion(
                    dataset=d.dataset if d.workspace == workspace else f"{d.workspace}/{d.dataset}",
                    owner_workspace=d.workspace,
                    version=d.version,
                    version_id=d.version_id,
                )
                for d in details.dataset_versions
            ],
            log_length=len(details.logs),
            timeout=found.timeout,
            stopped_by=found.stopped_by,
            note=note,
        )

    @server.tool(
        name="openhexa_get_pipeline_run_logs",
        title="Get the logs of an OpenHEXA pipeline run",
        annotations=READ_ONLY,
    )
    async def get_pipeline_run_logs(
        workspace: WorkspaceArg,
        run: RunArg,
        offset: Annotated[
            int | None,
            Field(
                ge=0,
                description="Position of the first character to return. Defaults to "
                "returning the end of the logs.",
            ),
        ] = None,
        length: Annotated[
            int, Field(ge=1, description="Maximum number of characters to return.")
        ] = DEFAULT_LOG_LENGTH,
    ) -> RunLogs:
        """Read the logs of an OpenHEXA pipeline run: the output of the pipeline process.

        Returns the end of the logs by default, where errors usually are; earlier parts
        are read with an offset. Tracebacks refer to the code of the version that ran,
        which openhexa_get_pipeline_source returns.
        """

        def read(client: Client) -> tuple[Run, str]:
            return client.runs.get(run), client.runs.details(run).logs

        found, logs = await access.run(workspace, read)
        total = len(logs)
        start = max(0, total - length) if offset is None else min(offset, total)
        end = min(start + length, total)
        return RunLogs(
            workspace=workspace,
            run_id=found.id,
            status=found.status,
            logs=logs[start:end],
            offset=start,
            total_length=total,
            note=_log_note(start, end, total, length),
        )

    @server.tool(
        name="openhexa_get_pipeline_source",
        title="Get the source code of an OpenHEXA pipeline",
        annotations=READ_ONLY,
    )
    async def get_pipeline_source(
        workspace: WorkspaceArg,
        pipeline: PipelineArg,
        version: PipelineVersionArg = None,
        path: Annotated[
            str | None,
            Field(
                description="Path of one file to read. Defaults to every file, with their "
                "content if the source is small enough."
            ),
        ] = None,
    ) -> PipelineSource:
        """Read the source code of an OpenHEXA pipeline version, such as the one a run ran.

        Content is returned as is, so line numbers match those of tracebacks in run logs.
        """

        def read(client: Client) -> tuple[PipelineVersion, list[SourceFile]]:
            selected = (
                client.pipelines.version(pipeline)
                if version is None
                else _find_version(client, pipeline, version, client.pipelines.versions(pipeline))
            )
            return selected, client.pipelines.files(selected)

        selected, files = await access.run(workspace, read)
        note = None
        if path is not None:
            paths = [f.path for f in files]
            files = [f for f in files if f.path == path]
            if not files:
                raise ToolError(f"The source has no file '{path}'. Files: {format_options(paths)}")
        length = sum(len(f.text) for f in files if f.text is not None)
        if length > MAX_SOURCE_LENGTH and path is None:
            files = [replace(f, text=None) for f in files]
            note = (
                f"The source has {length} characters of text, too many to return at once. "
                "Call again with path to read a file."
            )
        elif length > MAX_SOURCE_LENGTH:
            files = [replace(f, text=(f.text or "")[:MAX_SOURCE_LENGTH]) for f in files]
            note = f"The file is cut after {MAX_SOURCE_LENGTH} of its {length} characters."
        return PipelineSource(
            workspace=workspace,
            pipeline=pipeline,
            version=selected.label,
            version_id=selected.id,
            files=[SourceFileInfo(path=f.path, size=f.size, content=f.text) for f in files],
            note=note,
        )


def register_writes(server: MCPServer, access: Access) -> None:
    @server.tool(
        name="openhexa_start_pipeline_run",
        title="Start an OpenHEXA pipeline run",
        annotations=CREATE,
    )
    async def start_pipeline_run(
        workspace: WorkspaceArg,
        pipeline: PipelineArg,
        version: PipelineVersionArg = None,
        parameters: Annotated[
            dict[str, JsonValue] | None,
            Field(
                description="Parameter values by parameter code, as listed by "
                "openhexa_get_pipeline. Parameters without a value use the value "
                "configured in OpenHEXA, then their default."
            ),
        ] = None,
    ) -> RunChange:
        """Start a run of an OpenHEXA pipeline and return its ID and initial status.

        Returns once OpenHEXA has queued the run, without waiting for it to finish.
        """

        def start(client: Client) -> tuple[Run, set[str]]:
            selected = (
                client.pipelines.version(pipeline)
                if version is None
                else _find_version(client, pipeline, version, client.pipelines.versions(pipeline))
            )
            return client.runs.start(selected, parameters), _secret_codes(selected)

        run, secrets = await access.run(workspace, start)
        return _run_change(workspace, run, secrets)

    @server.tool(
        name="openhexa_stop_pipeline_run",
        title="Stop an OpenHEXA pipeline run",
        annotations=DESTRUCTIVE,
    )
    async def stop_pipeline_run(workspace: WorkspaceArg, run: RunArg) -> RunChange:
        """Stop a queued or running OpenHEXA pipeline run.

        OpenHEXA marks the run 'terminating', then 'stopped' once the process has ended.
        """

        def stop(client: Client) -> tuple[Run, set[str]]:
            stopped = client.runs.stop(run)
            return stopped, _run_secret_codes(client, stopped)

        stopped, secrets = await access.run(workspace, stop)
        return _run_change(workspace, stopped, secrets)


def _find_version(
    client: Client, pipeline: str, version: str, versions: list[PipelineVersion]
) -> PipelineVersion:
    """Find a version by its label among `versions`, else by ID or name."""
    for item in versions:
        if item.label == version:
            return item
    if is_uuid(version):
        return client.pipelines.version(pipeline, version_id=version)
    return client.pipelines.version(pipeline, version)


def _run_change(workspace: str, run: Run, secrets: set[str]) -> RunChange:
    return RunChange(
        **_run_fields(run),
        workspace=workspace,
        pipeline=run.pipeline,
        parameters=_mask(run.parameters, secrets),
        note=RUN_STATUS_NOTE,
    )


def _pipeline(pipeline: Pipeline) -> PipelineSummary:
    return PipelineSummary(
        code=pipeline.code,
        name=pipeline.name,
        description=pipeline.description,
        type=pipeline.type,
        schedule=pipeline.schedule,
        current_version=pipeline.current_version,
    )


def _version_summary(version: PipelineVersion) -> VersionSummary:
    return VersionSummary(
        label=version.label,
        version_id=version.id,
        description=version.description,
        created_at=version.created_at,
    )


def _version_details(version: PipelineVersion) -> VersionDetails:
    secrets = _secret_codes(version)
    return VersionDetails(
        **_version_summary(version).model_dump(),
        timeout=version.timeout,
        parameters=[
            ParameterInfo(
                code=p.code,
                name=p.name,
                type=p.type,
                required=p.required,
                multiple=p.multiple,
                default=SECRET_MASK if p.code in secrets and p.default else p.default,
                choices=p.choices,
                help=p.help,
            )
            for p in version.parameters
        ],
        configured_values=_mask(version.config, secrets),
    )


def _secret_codes(version: PipelineVersion) -> set[str]:
    return {p.code for p in version.parameters if p.type == SECRET_TYPE}


def _run_secret_codes(client: Client, run: Run) -> set[str]:
    """Codes of the secret parameters of the version that `run` ran."""
    if run.version_id is None:
        return set()
    return _secret_codes(client.pipelines.version(run.pipeline, version_id=run.version_id))


def _mask(values: dict[str, Any], secrets: set[str]) -> dict[str, Any]:
    """Replace the non-empty values of secret parameters with `SECRET_MASK`."""
    return {
        code: SECRET_MASK if code in secrets and value else value for code, value in values.items()
    }


def _run_fields(run: Run) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "duration": run.duration,
        "trigger": run.trigger,
        "progress": run.progress,
        "version": run.version,
        "version_id": run.version_id,
        "user": run.user,
    }


def _log_note(start: int, end: int, total: int, length: int) -> str | None:
    parts = []
    if start > 0:
        parts.append(
            f"Characters before {start} are not shown: call again with "
            f"offset={max(0, start - length)} to read the previous part."
        )
    if end < total:
        parts.append(f"Characters from {end} are not shown: call again with offset={end}.")
    return " ".join(parts) or None
