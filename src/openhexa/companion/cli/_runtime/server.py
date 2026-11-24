"""Local OpenHEXA API for a local pipeline run.

Runs inside the pipeline image, with its Python (3.13 or later), so it uses only the
standard library and `graphql-core`, a dependency of the SDK. It answers the SDK's
GraphQL requests from the local copy of the workspace, forwards the others to
OpenHEXA with the real token, and refuses remote writes.
"""

# ruff: noqa: N802, T201 (handlers are named after GraphQL fields; the run prints)

import json
import mimetypes
import re
import secrets
import shutil
import sys
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from graphql import GraphQLError, OperationDefinitionNode, parse, value_from_ast_untyped
from graphql.language import FieldNode

# URL path of the local dataset files, for downloads and uploads.
LOCAL_FILES_PATH = "/local/datasets/"
# Mutations that only read, forwarded like queries.
READ_MUTATIONS = {"prepareVersionFileDownload"}
# GraphQL type of a connection, by connection type.
CONNECTION_TYPENAMES = {
    "CUSTOM": "CustomConnection",
    "DHIS2": "DHIS2Connection",
    "GCS": "GCSConnection",
    "IASO": "IASOConnection",
    "POSTGRESQL": "PostgreSQLConnection",
    "S3": "S3Connection",
}
# Seconds to wait for OpenHEXA to answer a forwarded request.
FORWARD_TIMEOUT = 120
OK = {"success": True, "errors": []}


class RefusedError(Exception):
    """A request the local server will not answer or forward."""


class Field:
    """A root field of a request, with its arguments and those of its nested fields."""

    def __init__(self, node: FieldNode, variables: dict[str, Any]) -> None:
        self.alias = node.alias.value if node.alias else node.name.value
        self.name = node.name.value
        self.arguments = _arguments(node, variables)
        self.nested: dict[str, dict[str, Any]] = {}
        stack = [node]
        while stack:
            current = stack.pop()
            for child in current.selection_set.selections if current.selection_set else ():
                if isinstance(child, FieldNode):
                    self.nested.setdefault(child.name.value, _arguments(child, variables))
                    stack.append(child)


def _arguments(node: FieldNode, variables: dict[str, Any]) -> dict[str, Any]:
    return {a.name.value: value_from_ast_untyped(a.value, variables) for a in node.arguments}


def read_request(body: dict[str, Any]) -> tuple[str, list[Field]]:
    """Return the operation type and root fields of a request holding one operation.

    Raises:
        RefusedError: The request is not valid GraphQL, holds several operations or
            uses fragments at its root.
    """
    try:
        document = parse(body.get("query") or "")
    except GraphQLError as err:
        raise RefusedError(f"the request is not valid GraphQL ({err.message})") from None
    operations = [d for d in document.definitions if isinstance(d, OperationDefinitionNode)]
    if len(operations) != 1:
        raise RefusedError("the request must hold exactly one operation")
    selections = operations[0].selection_set.selections
    if not all(isinstance(s, FieldNode) for s in selections):
        raise RefusedError("the request uses fragments at its root")
    variables = body.get("variables") or {}
    fields = [Field(s, variables) for s in selections if isinstance(s, FieldNode)]
    return operations[0].operation.value, fields


def natural_key(name: str) -> list[Any]:
    """Sort key putting `v2` before `v10`; dates sort as text."""
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower()) for part in re.split(r"(\d+)", name)
    ]


def _timestamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0).isoformat()


def _content_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _is_name(name: object) -> bool:
    """Whether `name` can be a single folder or file name."""
    return isinstance(name, str) and name not in {"", ".", ".."} and "/" not in name


def _inside(root: Path, relative: object) -> Path | None:
    """Return `root / relative` when it is strictly inside `root`, else None."""
    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        return None
    root = root.resolve()
    path = (root / relative).resolve()
    return path if path.is_relative_to(root) and path != root else None


def _local_id(value: object, kind: str) -> list[str] | None:
    """Split a local ID such as `local:version:population/v4`; None for a remote ID."""
    prefix = f"local:{kind}:"
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    return value.removeprefix(prefix).split("/", 2)


def _page(items: list[Any], arguments: dict[str, Any]) -> dict[str, Any]:
    page = int(arguments.get("page") or 1)
    per_page = int(arguments.get("perPage") or 15)
    start = (page - 1) * per_page
    return {
        "items": items[start : start + per_page],
        "totalPages": max(1, -(-len(items) // per_page)),
        "totalItems": len(items),
        "pageNumber": page,
    }


def refusal(names: str) -> str:
    return (
        f"Local run refused the remote write {names}: local runs do not change the "
        "workspace. Use a local copy (for example `hexa datasets download SLUG`), "
        "or run again with `hexa run --allow-remote-writes`."
    )


class Router:
    """Answers each GraphQL request locally, forwards it to OpenHEXA, or refuses it.

    Args:
        settings: `openhexa_url`, `token`, `workspace` and `allow_remote_writes`.
        workspace: Folder with the local `datasets/` and `connections/`, each optional.
        files: Folder of the workspace files.
        url: URL of the server itself, for download and upload links.
    """

    def __init__(self, settings: dict[str, Any], workspace: Path, files: Path, url: str) -> None:
        self.settings = settings
        self.datasets = workspace / "datasets"
        self.connections = workspace / "connections"
        self.files = files
        self.url = url
        self.lock = threading.Lock()
        self.summary: dict[str, list[Any]] = {
            "messages": [],
            "outputs": [],
            "new_versions": [],
            "refused": [],
        }

    def handle(self, body: dict[str, Any]) -> tuple[int, bytes]:
        """Return the HTTP status and JSON body answering a GraphQL request."""
        try:
            kind, fields = read_request(body)
        except RefusedError as err:
            return _refused_request(str(err))
        answers = [self.answer(field) for field in fields]
        local = [answer for answer in answers if answer is not None]
        if not local:
            return self.forward_or_refuse(kind, fields, body)
        if len(fields) > 1:
            return _refused_request(
                "it mixes fields answered from the local copy with others; send them in "
                "separate requests"
            )
        return _json({"data": {fields[0].alias: local[0]["value"]}})

    def answer(self, field: Field) -> dict[str, Any] | None:
        """Return `{"value": ...}` when answered locally, None to forward."""
        handler = getattr(self, f"on_{field.name}", None)
        return handler(field) if handler else None

    def record(self, key: str, item: object) -> None:
        with self.lock:
            self.summary[key].append(item)

    def forward_or_refuse(
        self, kind: str, fields: list[Field], body: dict[str, Any]
    ) -> tuple[int, bytes]:
        names = ", ".join(field.name for field in fields)
        writes = kind != "query" and not {f.name for f in fields} <= READ_MUTATIONS
        if writes and not self.settings.get("allow_remote_writes"):
            self.record("refused", names)
            message = refusal(names)
            print(f"hexa: {message}", file=sys.stderr, flush=True)
            return _json({"errors": [{"message": message}]})
        status, content = self.forward(body)
        if status == 200 and any(f.name == "workspace" for f in fields):
            response = json.loads(content)
            for field in fields:
                if field.name == "workspace" and response.get("data"):
                    self.add_local_datasets(response["data"].get(field.alias))
            return _json(response)
        return status, content

    def forward(self, body: dict[str, Any]) -> tuple[int, bytes]:
        request = urllib.request.Request(  # noqa: S310 (the URL is the OpenHEXA instance)
            self.settings["openhexa_url"].rstrip("/") + "/graphql/",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.settings['token']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=FORWARD_TIMEOUT) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()
        except urllib.error.URLError as err:
            return _json({"errors": [{"message": f"Cannot reach OpenHEXA: {err.reason}"}]})

    def local_dataset(self, slug: object) -> bool:
        return _is_name(slug) and (self.datasets / str(slug)).is_dir()

    def local_version(self, version_id: object) -> Path | None:
        parts = _local_id(version_id, "version")
        if parts is None or len(parts) != 2 or not self.local_dataset(parts[0]):
            return None
        path = self.datasets / parts[0] / parts[1]
        return path if _is_name(parts[1]) and path.is_dir() else None

    def versions(self, slug: str) -> list[Path]:
        paths = [p for p in (self.datasets / slug).iterdir() if p.is_dir()]
        return sorted(paths, key=lambda p: natural_key(p.name))

    def version_data(self, path: Path) -> dict[str, Any]:
        return {
            "id": f"local:version:{path.parent.name}/{path.name}",
            "name": path.name,
            "description": None,
            "changelog": None,
            "createdAt": _timestamp(path),
        }

    def dataset_data(self, slug: str) -> dict[str, Any]:
        versions = self.versions(slug)
        return {
            "id": f"local:dataset:{slug}",
            "slug": slug,
            "name": slug,
            "description": "Local copy",
            "workspace": {"slug": self.settings["workspace"]},
            "latestVersion": self.version_data(versions[-1]) if versions else None,
        }

    def file_data(self, version: Path, path: Path) -> dict[str, Any]:
        relative = path.relative_to(version.resolve()).as_posix()
        uri = f"{version.parent.name}/{version.name}/{relative}"
        return {
            "id": f"local:file:{uri}",
            "uri": uri,
            "filename": path.name,
            "contentType": _content_type(path.name),
            "size": path.stat().st_size,
            "createdAt": _timestamp(path),
        }

    def add_local_datasets(self, workspace: dict[str, Any] | None) -> None:
        """Add the local datasets to a forwarded `workspace { datasets }` answer."""
        if not workspace or "datasets" not in workspace or not self.datasets.is_dir():
            return
        local = sorted(p.name for p in self.datasets.iterdir() if p.is_dir())
        items = [i for i in workspace["datasets"]["items"] if i["dataset"]["slug"] not in local]
        items += [{"id": f"local:link:{s}", "dataset": self.dataset_data(s)} for s in local]
        workspace["datasets"]["items"] = items

    def on_logPipelineMessage(self, field: Field) -> dict[str, Any]:
        data = field.arguments["input"]
        level, message = data.get("priority", "INFO"), str(data.get("message", ""))
        self.record("messages", {"priority": level, "message": message})
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        print(f"{now} {level} {message}", flush=True)
        return {"value": OK}

    def on_addPipelineOutput(self, field: Field) -> dict[str, Any]:
        data = field.arguments["input"]
        if data.get("type") == "file":
            path = _inside(self.files, data.get("name"))
            if path is None or not path.exists():
                return {"value": {"success": False, "errors": ["FILE_NOT_FOUND"]}}
        self.record("outputs", {"type": data.get("type"), "name": data.get("name")})
        return {"value": OK}

    def on_updatePipelineHeartbeat(self, _field: Field) -> dict[str, Any]:
        return {"value": OK}

    def on_getFileByPath(self, field: Field) -> dict[str, Any]:
        path = _inside(self.files, field.arguments.get("path"))
        if path is None or not path.exists():
            return {"value": None}
        key = path.relative_to(self.files.resolve()).as_posix()
        return {
            "value": {
                "key": key,
                "name": path.name,
                "path": key,
                "size": path.stat().st_size if path.is_file() else None,
                "type": "FILE" if path.is_file() else "DIRECTORY",
                "updatedAt": _timestamp(path),
            }
        }

    def on_connectionBySlug(self, field: Field) -> dict[str, Any] | None:
        slug = field.arguments.get("connectionSlug")
        if field.arguments.get("workspaceSlug") != self.settings["workspace"] or not _is_name(slug):
            return None
        path = self.connections / f"{slug}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        kind = data["type"].upper()
        return {
            "value": {
                "__typename": CONNECTION_TYPENAMES[kind],
                "type": kind,
                "fields": [{"code": c, "value": v} for c, v in data["fields"].items()],
            }
        }

    def on_datasetLinkBySlug(self, field: Field) -> dict[str, Any] | None:
        slug = field.arguments.get("datasetSlug")
        if field.arguments.get("workspaceSlug") != self.settings["workspace"]:
            return None
        if not self.local_dataset(slug):
            return None
        return {"value": {"id": f"local:link:{slug}", "dataset": self.dataset_data(str(slug))}}

    def on_dataset(self, field: Field) -> dict[str, Any] | None:
        parts = _local_id(field.arguments.get("id"), "dataset")
        if parts is None:
            return None
        if not self.local_dataset(parts[0]):
            return {"value": None}
        data = self.dataset_data(parts[0])
        versions = [self.version_data(p) for p in reversed(self.versions(parts[0]))]
        data["versions"] = _page(versions, field.nested.get("versions", {}))
        return {"value": data}

    def on_datasetVersion(self, field: Field) -> dict[str, Any] | None:
        if _local_id(field.arguments.get("id"), "version") is None:
            return None
        version = self.local_version(field.arguments["id"])
        if version is None:
            return {"value": None}
        data = self.version_data(version)
        files = sorted(p for p in version.resolve().rglob("*") if p.is_file())
        data["files"] = _page(
            [self.file_data(version, p) for p in files], field.nested.get("files", {})
        )
        if "fileByName" in field.nested:
            path = _inside(version, field.nested["fileByName"].get("name"))
            found = path is not None and path.is_file()
            data["fileByName"] = self.file_data(version, path) if path and found else None
        return {"value": data}

    def on_prepareVersionFileDownload(self, field: Field) -> dict[str, Any] | None:
        parts = _local_id(field.arguments["input"].get("fileId"), "file")
        if parts is None:
            return None
        url = f"{self.url}{LOCAL_FILES_PATH}{urllib.parse.quote('/'.join(parts))}"
        return {"value": {**OK, "downloadUrl": url}}

    def on_createDatasetVersion(self, field: Field) -> dict[str, Any] | None:
        data = field.arguments["input"]
        parts = _local_id(data.get("datasetId"), "dataset")
        if parts is None:
            return None
        name = str(data.get("name", ""))
        if not self.local_dataset(parts[0]):
            return {"value": {"success": False, "errors": ["DATASET_NOT_FOUND"], "version": None}}
        if not _is_name(name):
            return {"value": {"success": False, "errors": ["INVALID_NAME"], "version": None}}
        path = self.datasets / parts[0] / name
        try:
            path.mkdir()
        except FileExistsError:
            return {"value": {"success": False, "errors": ["DUPLICATE_NAME"], "version": None}}
        self.record("new_versions", {"dataset": parts[0], "version": name})
        return {"value": {**OK, "version": self.version_data(path)}}

    def on_generateDatasetUploadUrl(self, field: Field) -> dict[str, Any] | None:
        data = field.arguments["input"]
        if _local_id(data.get("versionId"), "version") is None:
            return None
        version = self.local_version(data["versionId"])
        path = _inside(version, data.get("uri")) if version else None
        if path is None:
            return {"value": {"success": False, "errors": ["INVALID_URI"]}}
        if path.exists():
            return {"value": {"success": False, "errors": ["ALREADY_EXISTS"]}}
        relative = path.relative_to(self.datasets.resolve()).as_posix()
        return {
            "value": {
                **OK,
                "uploadUrl": f"{self.url}{LOCAL_FILES_PATH}{urllib.parse.quote(relative)}",
                "headers": {"Content-Type": data.get("contentType") or _content_type(path.name)},
            }
        }

    def on_createDatasetVersionFile(self, field: Field) -> dict[str, Any] | None:
        data = field.arguments["input"]
        if _local_id(data.get("versionId"), "version") is None:
            return None
        version = self.local_version(data["versionId"])
        path = _inside(version, data.get("uri")) if version else None
        if version is None or path is None or not path.is_file():
            return {"value": {"success": False, "errors": ["FILE_NOT_UPLOADED"], "file": None}}
        return {"value": {**OK, "file": self.file_data(version, path)}}


def _refused_request(reason: str) -> tuple[int, bytes]:
    return _json({"errors": [{"message": f"Local run refused a request: {reason}."}]})


def _json(value: object) -> tuple[int, bytes]:
    return 200, json.dumps(value).encode()


class Handler(BaseHTTPRequestHandler):
    """GraphQL at `/graphql/`, and the local dataset files at `/local/datasets/`."""

    server: "LocalServer"  # pyrefly: ignore[bad-override-mutable-attribute]
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (overridden signature)
        pass

    def reply(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_file(self) -> Path | None:
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if not path.startswith(LOCAL_FILES_PATH):
            return None
        return _inside(self.server.router.datasets, path.removeprefix(LOCAL_FILES_PATH))

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if urllib.parse.urlsplit(self.path).path.rstrip("/") != "/graphql":
            self.reply(404)
            return
        expected = f"Bearer {self.server.run_token}"
        if not secrets.compare_digest(self.headers.get("Authorization", ""), expected):
            self.reply(401, b'{"errors": [{"message": "Invalid token."}]}', "application/json")
            return
        try:
            status, content = self.server.router.handle(json.loads(body or b"{}"))
        except Exception as err:  # noqa: BLE001 (the boundary: the SDK gets an error to raise)
            traceback.print_exc()
            status, content = _json({"errors": [{"message": f"Local server error: {err!r}"}]})
        self.reply(status, content, "application/json")

    def do_GET(self) -> None:
        path = self.local_file()
        if path is None or not path.is_file():
            self.reply(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", _content_type(path.name))
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile)

    def do_PUT(self) -> None:
        """Write a new file into an existing local version; never replace one."""
        length = int(self.headers.get("Content-Length") or 0)
        path = self.local_file()
        datasets = self.server.router.datasets
        parts = path.relative_to(datasets.resolve()).parts if path else ()
        if path is None or len(parts) < 3 or not datasets.joinpath(*parts[:2]).is_dir():
            self.rfile.read(length)
            self.reply(404)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        remaining = length
        try:
            with path.open("xb") as target:
                while remaining > 0 and (chunk := self.rfile.read(min(remaining, 1 << 20))):
                    target.write(chunk)
                    remaining -= len(chunk)
        except FileExistsError:
            self.rfile.read(length)
            self.reply(409, b"File already exists")
            return
        if remaining > 0:
            path.unlink()
            self.reply(400, b"Incomplete upload")
            return
        self.reply(200)


class LocalServer(ThreadingHTTPServer):
    """The local API, on a free port of the loopback interface.

    Args:
        settings: See `Router`.
        workspace: Folder with the local `datasets/` and `connections/`.
        files: Folder of the workspace files.
    """

    daemon_threads = True

    def __init__(self, settings: dict[str, Any], workspace: Path, files: Path) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server_port}"
        # The only token the server accepts, given to the pipeline instead of the real one.
        self.run_token = secrets.token_urlsafe(32)
        self.router = Router(settings, workspace, files, self.url)
