"""Pipelines of a workspace and their versions."""

import ast
import base64
import builtins
import io
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from openhexa.companion._transport import Transport
from openhexa.companion._util import check_uuid, format_options
from openhexa.companion.errors import InvalidInputError, NotFoundError

PIPELINE_FIELDS = """
  id
  code
  name
  description
  type
  schedule
  currentVersion { name versionNumber }
"""

VERSION_FIELDS = """
  id
  name
  versionNumber
  description
  createdAt
  timeout
  config
  parameters { code name type required multiple default choices help }
  pipeline { code workspace { slug } }
"""

LIST_QUERY = f"""
query Pipelines(
  $workspace: String!, $lastRunStates: [PipelineRunStatus!], $page: Int!, $perPage: Int!
) {{
  pipelines(
    workspaceSlug: $workspace, lastRunStates: $lastRunStates, page: $page, perPage: $perPage
  ) {{
    totalPages
    items {{ {PIPELINE_FIELDS} }}
  }}
}}
"""

GET_QUERY = f"""
query Pipeline($workspace: String!, $code: String!) {{
  pipelineByCode(workspaceSlug: $workspace, code: $code) {{ {PIPELINE_FIELDS} }}
}}
"""

VERSIONS_QUERY = f"""
query PipelineVersions($workspace: String!, $code: String!, $page: Int!, $perPage: Int!) {{
  pipelineByCode(workspaceSlug: $workspace, code: $code) {{
    versions(page: $page, perPage: $perPage) {{
      totalPages
      items {{ {VERSION_FIELDS} }}
    }}
  }}
}}
"""

LATEST_VERSION_QUERY = f"""
query LatestPipelineVersion($workspace: String!, $code: String!) {{
  pipelineByCode(workspaceSlug: $workspace, code: $code) {{
    currentVersion {{ {VERSION_FIELDS} }}
  }}
}}
"""

VERSION_QUERY = f"""
query PipelineVersion($id: UUID!) {{
  pipelineVersion(id: $id) {{ {VERSION_FIELDS} }}
}}
"""

SOURCE_QUERY = """
query PipelineVersionSource($id: UUID!) {
  pipelineVersion(id: $id) {
    zipfile
    pipeline { code workspace { slug } }
  }
}
"""

CREATE_MUTATION = f"""
mutation CreatePipeline($input: CreatePipelineInput!) {{
  createPipeline(input: $input) {{
    success
    errors
    details
    pipelineVersion {{ {VERSION_FIELDS} }}
  }}
}}
"""

DEPLOY_MUTATION = f"""
mutation UploadPipeline($input: UploadPipelineInput!) {{
  uploadPipeline(input: $input) {{
    success
    errors
    details
    pipelineVersion {{ {VERSION_FIELDS} }}
  }}
}}
"""

DELETE_MUTATION = """
mutation DeletePipeline($input: DeletePipelineInput!) {
  deletePipeline(input: $input) {
    success
    errors
  }
}
"""

DELETE_VERSION_MUTATION = """
mutation DeletePipelineVersion($input: DeletePipelineVersionInput!) {
  deletePipelineVersion(input: $input) {
    success
    errors
  }
}
"""

UPDATE_MUTATION = f"""
mutation UpdatePipeline($input: UpdatePipelineInput!) {{
  updatePipeline(input: $input) {{
    success
    errors
    details
    pipeline {{ {PIPELINE_FIELDS} }}
  }}
}}
"""

UPDATE_VERSION_MUTATION = f"""
mutation UpdatePipelineVersion($input: UpdatePipelineVersionInput!) {{
  updatePipelineVersion(input: $input) {{
    success
    errors
    pipelineVersion {{ {VERSION_FIELDS} }}
  }}
}}
"""

# Explanations of the error codes returned when creating or deploying a pipeline.
DEPLOY_ERRORS = {
    "CANNOT_UPDATE_NOTEBOOK_PIPELINE": "notebook pipelines cannot receive new versions",
    "DUPLICATE_PIPELINE_VERSION_NAME": "the pipeline already has a version with this name",
    "INVALID_TIMEOUT_VALUE": "the timeout declared in `@pipeline` exceeds the allowed maximum",
    "INVALID_VERSION_FILES": "the files are not a valid pipeline",
    "PERMISSION_DENIED": "the token may not deploy pipelines in this workspace",
    "PIPELINE_CODE_PARSING_ERROR": "OpenHEXA could not read the `@pipeline` declaration",
    "PIPELINE_DOES_NOT_SUPPORT_PARAMETERS": "the pipeline is scheduled, so every parameter "
    "of the new version needs a default",
    "REQUEST_TOO_LARGE": "the code is larger than this OpenHEXA instance accepts; move "
    "data files to a dataset or the workspace files",
}
# Directory where pipelines mount the workspace files, never part of the source.
WORKSPACE_DIRECTORY = "workspace"


@dataclass(frozen=True)
class Pipeline:
    """A pipeline of the workspace.

    Attributes:
        code: Identifies the pipeline within the workspace.
        name: Display name.
        description: What the pipeline does, if documented.
        type: `zipFile` for pipelines deployed from code, `notebook` for those
            running a notebook of the workspace.
        schedule: Cron expression of the schedule, or `None` if not scheduled.
        current_version: Label of the latest version, such as `v3` or `fix [v3]`, or
            `None` if no version was deployed.
        id: OpenHEXA identifier.
    """

    code: str
    name: str
    description: str
    type: str
    schedule: str | None
    current_version: str | None
    id: str


@dataclass(frozen=True)
class PipelineParameter:
    """A parameter declared with `@parameter` in a pipeline version.

    Attributes:
        code: Name of the argument, used to pass a value when starting a run.
        name: Display name.
        type: Type, such as `str`, `int`, `bool`, `dataset` or `postgresql`.
        required: Whether a run needs a value.
        multiple: Whether the parameter takes a list of values.
        default: Value used when a run gives none.
        choices: Allowed values, if restricted.
        help: Explanation of the parameter, if documented.
    """

    code: str
    name: str
    type: str
    required: bool
    multiple: bool
    default: Any
    choices: builtins.list[Any] | None
    help: str | None


@dataclass(frozen=True)
class PipelineVersion:
    """A deployed version of a pipeline.

    Attributes:
        pipeline: Code of the pipeline.
        number: Number of the version, increasing from 1 within the pipeline.
        name: Name given when deploying, if any.
        description: What changed in this version, if documented.
        created_at: When the version was deployed.
        timeout: Seconds after which runs are stopped, or `None` for the default.
        parameters: Parameters of the version.
        config: Parameter values configured in OpenHEXA, which runs use unless they
            give another value.
        id: OpenHEXA identifier.
    """

    pipeline: str
    number: int
    name: str | None
    description: str | None
    created_at: datetime
    timeout: int | None
    parameters: builtins.list[PipelineParameter]
    config: dict[str, Any]
    id: str

    @property
    def label(self) -> str:
        """Names the version as OpenHEXA shows it, such as `v3` or `fix [v3]`."""
        return version_label(self.name, self.number)


@dataclass(frozen=True)
class SourceFile:
    """A file of the source code of a pipeline version.

    Attributes:
        path: Path of the file in the source, such as `pipeline.py`.
        size: Size of the file in bytes.
        text: Content of the file, or `None` if it is not UTF-8 text.
    """

    path: str
    size: int
    text: str | None


class Pipelines:
    """Reads, deploys and configures the pipelines of a workspace."""

    def __init__(self, transport: Transport, workspace: str) -> None:
        self._transport = transport
        self._workspace = workspace

    def list(
        self, *, last_run_status: str | None = None, limit: int | None = None
    ) -> builtins.list[Pipeline]:
        """List the pipelines of the workspace, by name.

        Args:
            last_run_status: Only list pipelines whose latest run has this status, such
                as `failed`.
            limit: Maximum number of pipelines, or `None` for all of them.
        """
        states = [last_run_status] if last_run_status else None

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            variables = {
                "workspace": self._workspace,
                "lastRunStates": states,
                "page": page,
                "perPage": per_page,
            }
            return self._transport.query(LIST_QUERY, variables)["pipelines"]

        return [_pipeline(item) for item in self._transport.paginate(fetch_page, limit)]

    def get(self, code: str) -> Pipeline:
        """Get the pipeline `code`.

        Raises:
            NotFoundError: The workspace has no pipeline `code`.
        """
        return _pipeline(self._find(GET_QUERY, code))

    def versions(
        self, pipeline: str, *, limit: int | None = None
    ) -> builtins.list[PipelineVersion]:
        """List the versions of a pipeline, newest first.

        Args:
            pipeline: Code of the pipeline.
            limit: Maximum number of versions, or `None` for all of them.

        Raises:
            NotFoundError: The workspace has no such pipeline.
        """

        def fetch_page(page: int, per_page: int) -> dict[str, Any]:
            return self._find(VERSIONS_QUERY, pipeline, page=page, perPage=per_page)["versions"]

        return [_version(item) for item in self._transport.paginate(fetch_page, limit)]

    def version(
        self, pipeline: str, name: str | None = None, *, version_id: str | None = None
    ) -> PipelineVersion:
        """Get a version of a pipeline: by name, by ID, or the latest one by default.

        `name` matches the name given when deploying, or the number as `v3`.

        Raises:
            InvalidInputError: Both `name` and `version_id` are given, or `version_id` is not
                an OpenHEXA identifier.
            NotFoundError: The pipeline or version does not exist.
        """
        if name is not None and version_id is not None:
            raise InvalidInputError("Give either a version name or a version ID, not both")
        if name is not None:
            versions = self.versions(pipeline)
            # A name given when deploying wins over a number, since a version may be named `v2`.
            for version in versions:
                if version.name == name:
                    return version
            for version in versions:
                if f"v{version.number}" == name:
                    return version
            raise NotFoundError(
                f"Pipeline '{pipeline}' in workspace '{self._workspace}' has no version "
                f"'{name}'. Versions, newest first: {format_options(v.label for v in versions)}"
            )
        if version_id is not None:
            check_uuid(version_id, "version ID")
            data = self._transport.query(VERSION_QUERY, {"id": version_id})["pipelineVersion"]
            if data is None or not self._belongs(data, pipeline):
                raise NotFoundError(
                    f"Pipeline '{pipeline}' in workspace '{self._workspace}' has no version "
                    f"with ID '{version_id}'"
                )
            return _version(data)
        data = self._find(LATEST_VERSION_QUERY, pipeline)["currentVersion"]
        if data is None:
            raise NotFoundError(
                f"Pipeline '{pipeline}' in workspace '{self._workspace}' has no version yet"
            )
        return _version(data)

    def source(self, version: PipelineVersion) -> bytes:
        """Download the source code of a pipeline version, as a ZIP archive.

        Raises:
            NotFoundError: The version no longer exists.
        """
        data = self._transport.query(SOURCE_QUERY, {"id": version.id})["pipelineVersion"]
        if data is None or not self._belongs(data, version.pipeline):
            raise NotFoundError(
                f"Version '{version.label}' of pipeline '{version.pipeline}' no longer exists "
                f"in workspace '{self._workspace}'"
            )
        return base64.b64decode(data["zipfile"])

    def files(self, version: PipelineVersion) -> builtins.list[SourceFile]:
        """Read the files of the source code of a pipeline version.

        Raises:
            NotFoundError: The version no longer exists.
        """
        with zipfile.ZipFile(io.BytesIO(self.source(version))) as archive:
            return [
                _source_file(archive, entry) for entry in archive.infolist() if not entry.is_dir()
            ]

    def create(
        self,
        name: str,
        source: Path,
        *,
        description: str | None = None,
        version_name: str | None = None,
        version_description: str | None = None,
    ) -> PipelineVersion:
        """Create a pipeline from the code in the directory `source`.

        OpenHEXA derives the pipeline code from `name`. See `deploy` for the files sent.

        Args:
            name: Name of the pipeline.
            source: Directory with `pipeline.py` at its root.
            description: What the pipeline does.
            version_name: Name of the first version. Defaults to its number, `v1`.
            version_description: Description of the first version.

        Returns:
            The first version, whose `pipeline` is the code of the new pipeline.

        Raises:
            InvalidInputError: `source` is not a pipeline directory.
            PermissionDeniedError: The token may not create pipelines.
            APIError: OpenHEXA rejected the code, with the reason in the message.
        """
        archive, timeout = package_pipeline(source)
        version = {
            "name": version_name,
            "description": version_description,
            "zipfile": archive,
            "timeout": timeout,
        }
        result = self._transport.mutate(
            "createPipeline",
            CREATE_MUTATION,
            {
                "input": {
                    "workspaceSlug": self._workspace,
                    "name": name,
                    "description": description,
                    "version": version,
                }
            },
            action=f"create pipeline '{name}' in workspace '{self._workspace}'",
            reasons=DEPLOY_ERRORS,
        )
        return _version(result["pipelineVersion"])

    def deploy(
        self,
        pipeline: str,
        source: Path,
        *,
        version_name: str | None = None,
        description: str | None = None,
    ) -> PipelineVersion:
        """Deploy the code in the directory `source` as a new version of a pipeline.

        Every file of `source` is sent, except hidden files and directories (such as
        `.env`, `.git` or `.venv`), `__pycache__` directories and the `workspace`
        directory where pipelines mount the workspace files. OpenHEXA reads the
        parameters from `pipeline.py`; the timeout declared in `@pipeline` is sent too.

        Args:
            pipeline: Code of the pipeline.
            source: Directory with `pipeline.py` at its root.
            version_name: Name of the version. Defaults to its number, such as `v4`.
            description: What changed in this version.

        Raises:
            InvalidInputError: `source` is not a pipeline directory.
            NotFoundError: The workspace has no such pipeline.
            PermissionDeniedError: The token may not deploy the pipeline.
            APIError: OpenHEXA rejected the version, with the reason in the message.
        """
        archive, timeout = package_pipeline(source)
        result = self._transport.mutate(
            "uploadPipeline",
            DEPLOY_MUTATION,
            {
                "input": {
                    "workspaceSlug": self._workspace,
                    "pipelineCode": pipeline,
                    "name": version_name,
                    "description": description,
                    "zipfile": archive,
                    "timeout": timeout,
                }
            },
            action=f"deploy a new version of pipeline '{pipeline}' in workspace "
            f"'{self._workspace}'",
            reasons={**DEPLOY_ERRORS, "PIPELINE_NOT_FOUND": "the pipeline does not exist"},
        )
        return _version(result["pipelineVersion"])

    def update(
        self,
        pipeline: str,
        *,
        name: str | None = None,
        description: str | None = None,
        schedule: str | None = None,
        unschedule: bool = False,
    ) -> Pipeline:
        """Change the name, description or schedule of a pipeline; `None` keeps a field.

        A scheduled pipeline runs its latest version with the default parameter values of
        that version, so OpenHEXA refuses a schedule while a required parameter has no
        value: set it first with `set_defaults`.

        Args:
            pipeline: Code of the pipeline.
            name: The new display name. The code does not change.
            description: What the pipeline does.
            schedule: Cron expression to run the pipeline on, such as `0 6 * * 1`.
            unschedule: Stop running the pipeline on a schedule.

        Raises:
            InvalidInputError: No change is given, or both `schedule` and `unschedule`.
            NotFoundError: The workspace has no such pipeline.
            PermissionDeniedError: The token may not update the pipeline: only workspace
                editors and admins can.
            APIError: OpenHEXA rejected the change. Code `MISSING_VERSION_CONFIG` means
                required parameters have no value; the message names them.
        """
        if schedule is not None and unschedule:
            raise InvalidInputError("Give either a schedule or unschedule, not both")
        changes: dict[str, Any] = {"name": name, "description": description, "schedule": schedule}
        changes = {key: value for key, value in changes.items() if value is not None}
        if unschedule:
            changes["schedule"] = None
        if not changes:
            raise InvalidInputError(f"No change given for pipeline '{pipeline}'")
        result = self._transport.mutate(
            "updatePipeline",
            UPDATE_MUTATION,
            {"input": {"id": self.get(pipeline).id, **changes}},
            action=f"update pipeline '{pipeline}' in workspace '{self._workspace}'",
            reasons={
                "INVALID_CONFIG": "the schedule is not a valid cron expression",
                "MISSING_VERSION_CONFIG": "the pipeline cannot be scheduled while required "
                "parameters have no default value",
                "PERMISSION_DENIED": "the token may not update this pipeline; only workspace "
                "editors and admins can",
            },
        )
        return _pipeline(result["pipeline"])

    def set_defaults(self, version: PipelineVersion, values: dict[str, Any]) -> PipelineVersion:
        """Set default parameter values of a pipeline version, keeping the others.

        Runs use these values unless they give another one; scheduled runs always do.
        OpenHEXA checks each value against the type of its parameter.

        Args:
            version: The version, such as the latest one from `version`.
            values: Values by parameter code. `None` removes a default value.

        Raises:
            InvalidInputError: No values are given, or a code is not a parameter of the
                version.
            NotFoundError: The version no longer exists.
            PermissionDeniedError: The token may not update the pipeline: only workspace
                editors and admins can.
            APIError: A value does not match the type of its parameter (code
                `INVALID_CONFIG`).
        """
        what = f"version '{version.label}' of pipeline '{version.pipeline}'"
        if not values:
            raise InvalidInputError(f"No parameter values given for {what}")
        codes = [parameter.code for parameter in version.parameters]
        unknown = [code for code in values if code not in codes]
        if unknown:
            raise InvalidInputError(
                f"{what.capitalize()} has no parameter {format_options(unknown)}. "
                f"Parameters: {format_options(codes)}"
            )
        result = self._transport.mutate(
            "updatePipelineVersion",
            UPDATE_VERSION_MUTATION,
            {"input": {"id": version.id, "config": {**version.config, **values}}},
            action=f"set default parameter values of {what} in workspace '{self._workspace}'",
            reasons={
                "INVALID_CONFIG": "a value does not match the type of its parameter",
                "NOT_FOUND": "the version no longer exists",
                "PERMISSION_DENIED": "the token may not update this pipeline; only workspace "
                "editors and admins can",
            },
        )
        return _version(result["pipelineVersion"])

    def delete(self, pipeline: str) -> None:
        """Delete a pipeline with its versions and runs.

        Raises:
            NotFoundError: The workspace has no such pipeline.
            PermissionDeniedError: The token may not delete the pipeline.
        """
        pipeline_id = self.get(pipeline).id
        self._transport.mutate(
            "deletePipeline",
            DELETE_MUTATION,
            {"input": {"id": pipeline_id}},
            action=f"delete pipeline '{pipeline}' in workspace '{self._workspace}'",
            reasons={"PERMISSION_DENIED": "the token may not delete this pipeline"},
        )

    def delete_version(self, version: PipelineVersion) -> None:
        """Delete a version of a pipeline with its runs.

        If it was the latest version, the previous one becomes the latest. OpenHEXA
        refuses to delete the only version of a pipeline: delete the pipeline instead.

        Raises:
            NotFoundError: The version no longer exists.
            PermissionDeniedError: The token may not delete versions of the pipeline, or
                the version is the pipeline's only one.
        """
        self._transport.mutate(
            "deletePipelineVersion",
            DELETE_VERSION_MUTATION,
            {"input": {"id": version.id}},
            action=f"delete version '{version.label}' of pipeline '{version.pipeline}' in "
            f"workspace '{self._workspace}'",
            reasons={
                "PERMISSION_DENIED": "the token may not delete versions of this pipeline, or "
                "it is the pipeline's only version",
                "PIPELINE_VERSION_NOT_FOUND": "the version no longer exists",
            },
        )

    def _find(self, query: str, code: str, **variables: Any) -> dict[str, Any]:
        return find_pipeline(self._transport, self._workspace, query, code, **variables)

    def _belongs(self, version: dict[str, Any], pipeline: str) -> bool:
        """Whether a version found by ID belongs to `pipeline` in the workspace."""
        owner = version["pipeline"]
        return owner["code"] == pipeline and owner["workspace"]["slug"] == self._workspace


def find_pipeline(
    transport: Transport, workspace: str, query: str, code: str, **variables: Any
) -> dict[str, Any]:
    """Run a query on the pipeline `code` of `workspace` and return the pipeline object.

    Raises:
        NotFoundError: The workspace has no pipeline `code`; lists the valid codes.
    """
    data = transport.query(query, {"workspace": workspace, "code": code, **variables})
    if data["pipelineByCode"] is None:
        codes = (pipeline.code for pipeline in Pipelines(transport, workspace).list())
        raise NotFoundError(
            f"Pipeline '{code}' not found in workspace '{workspace}'. "
            f"Pipelines: {format_options(codes)}"
        )
    return data["pipelineByCode"]


def package_pipeline(source: Path) -> tuple[str, int | None]:
    """Zip the pipeline in `source` for a deployment request.

    Returns:
        The base64-encoded archive, and the timeout declared in `@pipeline`, if any.

    Raises:
        InvalidInputError: `source` has no `pipeline.py`, or it is not valid Python.
    """
    entrypoint = source / "pipeline.py"
    files = source_files(source)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(source).as_posix())
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    try:
        tree = ast.parse(entrypoint.read_text(encoding="utf-8"), filename=str(entrypoint))
    except SyntaxError as err:
        raise InvalidInputError(
            f"Cannot deploy {entrypoint}: line {err.lineno}: {err.msg}"
        ) from err
    return encoded, _declared_timeout(tree)


def source_files(source: Path) -> list[Path]:
    """List the files of the pipeline in `source` that a deployment sends, sorted.

    Hidden files and directories (such as `.env`, `.git` or `.venv`), `__pycache__`
    directories and the `workspace` directory are left out.

    Raises:
        InvalidInputError: `source` has no `pipeline.py`.
    """
    if not (source / "pipeline.py").is_file():
        raise InvalidInputError(f"No pipeline.py at the root of {source}")
    return sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and _is_source(path.relative_to(source).parts)
    )


def _is_source(parts: tuple[str, ...]) -> bool:
    if parts[0] == WORKSPACE_DIRECTORY and len(parts) > 1:
        return False
    return not any(part.startswith(".") or part == "__pycache__" for part in parts)


def _declared_timeout(tree: ast.Module) -> int | None:
    """Read the `timeout` argument of the `@pipeline` decorator, if given as a literal."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "pipeline":
                continue
            for keyword in decorator.keywords:
                if (
                    keyword.arg == "timeout"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, int)
                ):
                    return keyword.value.value
    return None


def version_label(name: str | None, number: int) -> str:
    """Name a version as OpenHEXA shows it, such as `v3` or `fix [v3]`."""
    return f"{name} [v{number}]" if name else f"v{number}"


def _source_file(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> SourceFile:
    content = archive.read(entry)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    return SourceFile(path=entry.filename, size=entry.file_size, text=text)


def _pipeline(data: dict[str, Any]) -> Pipeline:
    current = data["currentVersion"]
    return Pipeline(
        code=data["code"],
        name=data["name"] or data["code"],
        description=data["description"] or "",
        type=data["type"],
        schedule=data["schedule"],
        current_version=version_label(current["name"], current["versionNumber"])
        if current
        else None,
        id=data["id"],
    )


def _version(data: dict[str, Any]) -> PipelineVersion:
    return PipelineVersion(
        pipeline=data["pipeline"]["code"],
        number=data["versionNumber"],
        name=data["name"],
        description=data["description"],
        created_at=datetime.fromisoformat(data["createdAt"]),
        timeout=data["timeout"],
        parameters=[_parameter(item) for item in data["parameters"]],
        config=data["config"] or {},
        id=data["id"],
    )


def _parameter(data: dict[str, Any]) -> PipelineParameter:
    return PipelineParameter(
        code=data["code"],
        name=data["name"],
        type=data["type"],
        required=data["required"],
        multiple=data["multiple"],
        default=data["default"],
        choices=data["choices"],
        help=data["help"],
    )
