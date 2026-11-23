"""Runs of the pipelines of a workspace."""

import builtins
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openhexa.companion._transport import Transport
from openhexa.companion._util import check_uuid
from openhexa.companion.errors import InvalidInputError, NotFoundError
from openhexa.companion.pipelines import (
    Pipelines,
    PipelineVersion,
    find_pipeline,
    version_label,
)

# Statuses after which a run no longer changes.
FINISHED_STATUSES = frozenset({"success", "failed", "stopped", "skipped"})

RUN_FIELDS = """
  id
  status
  executionDate
  duration
  triggerMode
  progress
  config
  user { displayName }
  version { id name versionNumber }
  pipeline { code workspace { slug } }
"""

LIST_QUERY = f"""
query PipelineRuns($workspace: String!, $code: String!, $page: Int!, $perPage: Int!) {{
  pipelineByCode(workspaceSlug: $workspace, code: $code) {{
    runs(page: $page, perPage: $perPage, orderBy: EXECUTION_DATE_DESC) {{
      totalPages
      items {{ {RUN_FIELDS} }}
    }}
  }}
}}
"""

GET_QUERY = f"""
query PipelineRun($id: UUID!) {{
  pipelineRun(id: $id) {{ {RUN_FIELDS} }}
}}
"""

DETAILS_QUERY = """
query PipelineRunDetails($id: UUID!) {
  pipelineRun(id: $id) {
    pipeline { code workspace { slug } }
    logs
    messages { message priority timestamp }
    outputs {
      __typename
      ... on BucketObject { key size }
      ... on DatabaseTable { tableName: name }
      ... on GenericOutput { genericName: name genericType: type uri }
    }
    datasetVersions { id name dataset { slug workspace { slug } } }
  }
}
"""

RUN_MUTATION = f"""
mutation RunPipeline($input: RunPipelineInput!) {{
  runPipeline(input: $input) {{
    success
    errors
    run {{ {RUN_FIELDS} }}
  }}
}}
"""

STOP_MUTATION = """
mutation StopPipeline($input: StopPipelineInput!) {
  stopPipeline(input: $input) {
    success
    errors
  }
}
"""

# Explanations of the error codes returned when starting or stopping a run.
RUN_ERRORS = {
    "INVALID_CONFIG": "a parameter value does not match the type of its parameter",
    "PERMISSION_DENIED": "the token may not run this pipeline",
    "PIPELINE_ALREADY_COMPLETED": "the run has already finished",
    "PIPELINE_ALREADY_STOPPED": "the run is already stopped or stopping",
    "PIPELINE_NOT_FOUND": "the pipeline or run does not exist",
    "PIPELINE_RUNS_LIMIT_REACHED": "the organization has reached its limit of runs",
    "PIPELINE_VERSION_NOT_FOUND": "the version does not exist",
}


@dataclass(frozen=True)
class Run:
    """A run of a pipeline.

    Attributes:
        id: Identifies the run.
        pipeline: Code of the pipeline.
        status: One of `queued`, `running`, `terminating`, `success`, `failed`,
            `stopped` or `skipped`.
        started_at: When the run was started, or `None` if not started yet.
        duration: Seconds the run took or has taken so far.
        trigger: How the run was started: `manual`, `scheduled` or `webhook`.
        progress: Progress reported by the pipeline, from 0 to 100.
        parameters: Parameter values of the run.
        version: Label of the version that ran, such as `v3` or `fix [v3]`.
        version_id: OpenHEXA identifier of the version that ran.
        user: Name of the person who started the run, if any.
    """

    id: str
    pipeline: str
    status: str
    started_at: datetime | None
    duration: int
    trigger: str | None
    progress: int
    parameters: dict[str, Any]
    version: str | None
    version_id: str | None
    user: str | None

    @property
    def finished(self) -> bool:
        """Whether the run has finished, so its status will no longer change."""
        return self.status in FINISHED_STATUSES


@dataclass(frozen=True)
class RunMessage:
    """A message logged by a pipeline with `current_run.log_*`.

    Attributes:
        priority: One of `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`.
        message: The message.
        timestamp: When the message was logged, if known.
    """

    priority: str
    message: str
    timestamp: datetime | None


@dataclass(frozen=True)
class RunOutput:
    """Something a run produced and declared as an output.

    Attributes:
        type: `file` for a workspace file, `table` for a database table, or the type
            given by the pipeline for other outputs.
        name: Path of the file in the workspace, name of the table, or name given by
            the pipeline.
        uri: Location given by the pipeline for other outputs.
        size: Size of the file in bytes, if a file.
    """

    type: str
    name: str
    uri: str | None = None
    size: int | None = None


@dataclass(frozen=True)
class RunDatasetVersion:
    """A dataset version a run published.

    Attributes:
        dataset: Slug of the dataset.
        workspace: Slug of the workspace that owns the dataset.
        version: Name of the version.
        version_id: OpenHEXA identifier of the version.
    """

    dataset: str
    workspace: str
    version: str
    version_id: str


@dataclass(frozen=True)
class RunDetails:
    """What a run reported: logs, messages and outputs.

    Logs and messages grow while the run is in progress; get them again to follow it.

    Attributes:
        logs: Output of the pipeline process.
        messages: Messages logged with `current_run.log_*`, oldest first.
        outputs: Files and tables the run declared as outputs.
        dataset_versions: Dataset versions the run published.
    """

    logs: str
    messages: builtins.list[RunMessage]
    outputs: builtins.list[RunOutput]
    dataset_versions: builtins.list[RunDatasetVersion]


class Runs:
    """Starts, stops and inspects the runs of the pipelines of a workspace."""

    def __init__(self, transport: Transport, workspace: str, pipelines: Pipelines) -> None:
        self._transport = transport
        self._workspace = workspace
        self._pipelines = pipelines

    def list(self, pipeline: str, *, limit: int | None = 20) -> builtins.list[Run]:
        """List the runs of a pipeline, newest first.

        Args:
            pipeline: Code of the pipeline.
            limit: Maximum number of runs, or `None` for all of them.

        Raises:
            NotFoundError: The workspace has no such pipeline.
        """

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            data = find_pipeline(
                self._transport,
                self._workspace,
                LIST_QUERY,
                pipeline,
                page=page,
                perPage=per_page,
            )
            return data["runs"]

        return [_run(item) for item in self._transport.paginate(fetch_page, limit)]

    def get(self, run_id: str) -> Run:
        """Get a run, to check its status.

        Raises:
            InvalidInputError: `run_id` is not a run ID.
            NotFoundError: The workspace has no such run.
        """
        return _run(self._find(GET_QUERY, run_id))

    def details(self, run_id: str) -> RunDetails:
        """Get the logs, messages and outputs of a run.

        Raises:
            InvalidInputError: `run_id` is not a run ID.
            NotFoundError: The workspace has no such run.
        """
        data = self._find(DETAILS_QUERY, run_id)
        return RunDetails(
            logs=data["logs"] or "",
            messages=[_message(item) for item in data["messages"]],
            outputs=[_output(item) for item in data["outputs"]],
            dataset_versions=[
                RunDatasetVersion(
                    dataset=item["dataset"]["slug"],
                    workspace=item["dataset"]["workspace"]["slug"],
                    version=item["name"],
                    version_id=item["id"],
                )
                for item in data["datasetVersions"]
            ],
        )

    def start(
        self,
        version: PipelineVersion,
        parameters: dict[str, Any] | None = None,
        *,
        send_notifications: bool = False,
    ) -> Run:
        """Start a run of a pipeline version.

        OpenHEXA checks the types of the values but not that required parameters have
        one: parameters without a value use the version's configured value, then the
        default declared in the code.

        Args:
            version: The version to run.
            parameters: Values of parameters, by code.
            send_notifications: Email the pipeline's recipients when the run finishes.

        Raises:
            InvalidInputError: `parameters` includes codes the version does not declare.
            PermissionDeniedError: The token may not run the pipeline.
            APIError: OpenHEXA refused to start the run, with the reason in the message.
        """
        parameters = parameters or {}
        declared = {parameter.code for parameter in version.parameters}
        unknown = sorted(parameters.keys() - declared)
        if unknown:
            raise InvalidInputError(
                f"Version '{version.label}' of pipeline '{version.pipeline}' has no parameter "
                f"{', '.join(repr(code) for code in unknown)}. Parameters: "
                f"{', '.join(repr(code) for code in sorted(declared)) or 'none'}"
            )
        pipeline_id = self._pipelines.get(version.pipeline).id
        result = self._transport.mutate(
            "runPipeline",
            RUN_MUTATION,
            {
                "input": {
                    "id": pipeline_id,
                    "versionId": version.id,
                    "config": parameters,
                    "sendMailNotifications": send_notifications,
                }
            },
            action=f"start a run of pipeline '{version.pipeline}' version '{version.label}' "
            f"in workspace '{self._workspace}'",
            reasons=RUN_ERRORS,
        )
        return _run(result["run"])

    def stop(self, run_id: str) -> None:
        """Ask OpenHEXA to stop a run; its status becomes `terminating`, then `stopped`.

        Raises:
            InvalidInputError: `run_id` is not a run ID.
            NotFoundError: The workspace has no such run.
            PermissionDeniedError: The token may not stop the run.
            APIError: The run has already finished or is stopping.
        """
        self._find(GET_QUERY, run_id)
        self._transport.mutate(
            "stopPipeline",
            STOP_MUTATION,
            {"input": {"runId": run_id}},
            action=f"stop run '{run_id}' in workspace '{self._workspace}'",
            reasons=RUN_ERRORS,
        )

    def _find(self, query: str, run_id: str) -> dict[str, Any]:
        """Run a query on the run `run_id`, checking it belongs to the workspace."""
        check_uuid(run_id, "run ID")
        data = self._transport.query(query, {"id": run_id})["pipelineRun"]
        if data is None or data["pipeline"]["workspace"]["slug"] != self._workspace:
            raise NotFoundError(f"Run '{run_id}' not found in workspace '{self._workspace}'")
        return data


def _run(data: dict[str, Any]) -> Run:
    version = data["version"]
    started_at = data["executionDate"]
    return Run(
        id=data["id"],
        pipeline=data["pipeline"]["code"],
        status=data["status"],
        started_at=datetime.fromisoformat(started_at) if started_at else None,
        duration=data["duration"] or 0,
        trigger=data["triggerMode"],
        progress=data["progress"],
        parameters=data["config"] or {},
        version=version_label(version["name"], version["versionNumber"]) if version else None,
        version_id=version["id"] if version else None,
        user=data["user"]["displayName"] if data["user"] else None,
    )


def _message(data: dict[str, Any]) -> RunMessage:
    timestamp = data["timestamp"]
    return RunMessage(
        priority=data["priority"],
        message=data["message"],
        timestamp=datetime.fromisoformat(timestamp) if timestamp else None,
    )


def _output(data: dict[str, Any]) -> RunOutput:
    match data["__typename"]:
        case "BucketObject":
            return RunOutput(type="file", name=data["key"], size=data["size"])
        case "DatabaseTable":
            return RunOutput(type="table", name=data["tableName"])
        case _:
            return RunOutput(
                type=data["genericType"], name=data["genericName"] or "", uri=data["uri"]
            )
