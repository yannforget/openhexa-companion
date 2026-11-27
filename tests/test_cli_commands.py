"""The shape of the `hexa` commands: arguments, options and what they print."""

import base64
import io
import json
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from openhexa.companion.cli import main

# Answers a GraphQL request body with the `data` of the response, or an HTTP response.
Handler = Callable[[dict[str, Any]], dict[str, Any] | httpx2.Response]
FakeOpenHexa = Callable[[Handler], list[str]]

PIPELINE_SOURCE = {"pipeline.py": "print('hello')\n", "lib/util.py": "X = 1\n"}


def _invoke(*args: str) -> Result:
    return CliRunner().invoke(main, list(args))


def _pipeline_version(**fields: Any) -> dict[str, Any]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in PIPELINE_SOURCE.items():
            archive.writestr(path, content)
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": None,
        "versionNumber": 1,
        "description": None,
        "createdAt": "2026-11-01T10:00:00+00:00",
        "timeout": None,
        "config": {},
        "parameters": [],
        "pipeline": {"code": "etl", "workspace": {"slug": "ws"}},
        "zipfile": base64.b64encode(buffer.getvalue()).decode("ascii"),
        **fields,
    }


def _pipelines(body: dict[str, Any]) -> dict[str, Any]:
    """Answer lookups of pipeline `etl` and its creation or deployment."""
    query = body["query"]
    for mutation in ("createPipeline", "uploadPipeline"):
        if f"{mutation}(" in query:
            version = body["variables"]["input"].get("version", body["variables"]["input"])
            created = _pipeline_version(
                name=version["name"],
                description=version["description"],
                externalLink=version.get("externalLink"),
                versionNumber=2,
            )
            return {mutation: {"success": True, "errors": [], "pipelineVersion": created}}
    if "pipelineVersion(" in query:
        return {"pipelineVersion": _pipeline_version()}
    return {"pipelineByCode": {"currentVersion": _pipeline_version()}}


def test_pipelines_run_points_folders_to_local_run(fake_openhexa: FakeOpenHexa, tmp_path: Path):
    def no_pipeline(body: dict[str, Any]) -> dict[str, Any]:
        if "pipelineByCode(" in body["query"]:
            return {"pipelineByCode": None}
        return {"pipelines": {"totalPages": 1, "items": []}}

    fake_openhexa(no_pipeline)

    result = _invoke("pipelines", "run", str(tmp_path))

    assert result.exit_code == 2
    assert "hexa local run" in result.stderr


def test_pipelines_source_saves_to_the_pipeline_folder(
    fake_openhexa: FakeOpenHexa, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    fake_openhexa(_pipelines)
    (tmp_path / "etl").mkdir()
    (tmp_path / "etl" / "notes.md").write_text("mine")

    first = _invoke("pipelines", "source", "etl")
    (tmp_path / "etl" / "pipeline.py").write_text("changed")
    refused = _invoke("pipelines", "source", "etl")
    forced = _invoke("pipelines", "source", "etl", "--force", "--json")

    assert first.exit_code == 0, first.output
    assert refused.exit_code == 1
    assert "--force" in refused.stderr
    assert forced.exit_code == 0, forced.output
    assert sorted(json.loads(forced.stdout)) == [
        str(Path("etl/lib/util.py")),
        str(Path("etl/pipeline.py")),
    ]
    assert (tmp_path / "etl" / "pipeline.py").read_text() == PIPELINE_SOURCE["pipeline.py"]
    assert (tmp_path / "etl" / "notes.md").read_text() == "mine"


@pytest.mark.parametrize(
    ("args", "mutation"),
    [
        (["create", "ETL", "-d", "Loads cases"], "createPipeline"),
        (["publish", "etl"], "uploadPipeline"),
    ],
)
def test_pipelines_create_and_publish_take_source_name_and_message(
    fake_openhexa: FakeOpenHexa, tmp_path: Path, args: list[str], mutation: str
):
    (tmp_path / "pipeline.py").write_text("")
    bodies: list[dict[str, Any]] = []

    def record(body: dict[str, Any]) -> dict[str, Any]:
        bodies.append(body)
        return _pipelines(body)

    fake_openhexa(record)

    result = _invoke(
        "pipelines",
        *args,
        str(tmp_path),
        "--name",
        "v2",
        "-m",
        "Faster",
        "--link",
        "https://example.org/c/1",
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "etl\n"
    sent = bodies[-1]["variables"]["input"]
    version = sent.get("version", sent)
    assert mutation in bodies[-1]["query"]
    assert (version["name"], version["description"]) == ("v2", "Faster")
    assert version["externalLink"] == "https://example.org/c/1"


def test_queries_show_prints_details_and_sql_prints_only_sql(fake_openhexa: FakeOpenHexa):
    saved = {
        "slug": "cases-by-district",
        "name": "Cases by district",
        "description": "Monthly totals",
        "visibility": "WORKSPACE",
        "createdBy": {"displayName": "Ada"},
        "updatedAt": "2026-11-01T10:00:00+00:00",
        "content": "select 1\n",
        "workspace": {"slug": "ws"},
    }
    fake_openhexa(lambda _body: {"savedQueryBySlug": saved})

    shown = _invoke("queries", "show", "cases-by-district")
    sql = _invoke("queries", "sql", "cases-by-district")

    assert "Cases by district" in shown.stdout
    assert "Monthly totals" in shown.stdout
    assert shown.stdout.endswith("select 1\n")
    assert shown.stderr == ""
    assert sql.stdout == "select 1\n"


def test_datasets_create_needs_sources_for_version_options(fake_openhexa: FakeOpenHexa):
    queries = fake_openhexa(lambda _body: {})

    result = _invoke("datasets", "create", "Cases", "--name", "v1")

    assert result.exit_code == 2
    assert "give SOURCES" in result.stderr
    assert queries == []


def test_datasets_show_lists_the_newest_versions(fake_openhexa: FakeOpenHexa):
    versions = [
        {
            "id": f"00000000-0000-0000-0000-00000000000{n}",
            "name": f"v{n}",
            "changelog": f"Change {n}",
            "createdAt": "2026-11-01T10:00:00+00:00",
        }
        for n in range(9, 0, -1)
    ]

    def dataset(body: dict[str, Any]) -> dict[str, Any]:
        per_page = body["variables"].get("perPage", 0)
        return {
            "datasetLinkBySlug": {
                "dataset": {
                    "id": "d1",
                    "slug": "pop",
                    "name": "Population",
                    "description": "About",
                    "workspace": {"slug": "ws"},
                    "targetId": "d1",
                    "latestVersion": {"name": "v9"},
                    "attributes": [],
                    "versions": {"totalPages": 2, "items": versions[:per_page]},
                }
            }
        }

    fake_openhexa(dataset)

    text = _invoke("datasets", "show", "pop")
    as_json = _invoke("datasets", "show", "pop", "--json")

    assert text.exit_code == 0, text.output
    assert "v5" in text.stdout
    assert "Change 5" in text.stdout
    assert "v4" not in text.stdout
    assert "hexa datasets versions list pop" in text.stderr
    assert [v["name"] for v in json.loads(as_json.stdout)["versions"]] == [
        "v9",
        "v8",
        "v7",
        "v6",
        "v5",
    ]


def _workspace_and_user(body: dict[str, Any]) -> dict[str, Any]:
    if "me {" in body["query"]:
        return {"me": {"user": {"id": "u1", "email": "ana@example.org"}}}
    workspace = {
        "slug": "ws",
        "name": "Workspace",
        "description": "# Home\n",
        "configuration": {},
        "dockerImage": None,
        "publicFiles": {"url": "https://openhexa.test/files/ws/public/"},
    }
    return {"workspace": workspace}


def test_workspace_show_checks_the_configuration(fake_openhexa: FakeOpenHexa):
    fake_openhexa(_workspace_and_user)

    text = _invoke("workspace", "show")
    as_json = _invoke("workspace", "show", "--json")

    assert text.exit_code == 0, text.output
    assert "OpenHEXA: https://openhexa.test" in text.stdout
    assert "User: ana@example.org" in text.stdout
    assert "Public files: https://openhexa.test/files/ws/public/" in text.stdout
    shown = json.loads(as_json.stdout)
    assert shown["slug"] == "ws"
    assert shown["url"] == "https://openhexa.test"
    assert shown["user"] == {"id": "u1", "email": "ana@example.org"}


def test_a_rejected_token_says_where_to_get_a_new_one(fake_openhexa: FakeOpenHexa):
    fake_openhexa(lambda _body: httpx2.Response(401))

    text = _invoke("workspace", "show")
    as_json = _invoke("workspace", "show", "--json")

    assert text.exit_code == 4
    assert "https://openhexa.test/user/account" in text.stderr
    assert "OPENHEXA_TOKEN in .env" in text.stderr
    error = json.loads(as_json.stderr)["error"]
    assert error["type"] == "AuthenticationError"
    assert "/user/account" in error["message"]


def _workspace_files(body: dict[str, Any]) -> dict[str, Any] | httpx2.Response:
    """Answer lookups and downloads of file `data/cases.csv` in directory `data/`."""
    if "url" in body:
        return httpx2.Response(200, content=iter([b"district,cases\nA,1\n"]))
    if "prepareObjectDownload(" in body["query"]:
        url = "https://storage.test/cases.csv"
        return {"prepareObjectDownload": {"success": True, "errors": [], "downloadUrl": url}}
    key = body["variables"].get("key")
    if key == "data/cases.csv":
        item = {"key": key, "type": "FILE", "size": 19, "updatedAt": None}
    elif key == "data/":
        item = {"key": key, "type": "DIRECTORY", "size": 0, "updatedAt": None}
    else:
        item = None
    return {"workspace": {"bucket": {"object": item}}}


def test_files_read_prints_the_file_as_is(fake_openhexa: FakeOpenHexa):
    fake_openhexa(_workspace_files)

    result = CliRunner().invoke(main, ["files", "read", "data/cases.csv"])
    directory = _invoke("files", "read", "data")

    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == b"district,cases\nA,1\n"
    assert directory.exit_code == 2
    assert "is a directory" in directory.stderr


RUN_ID = "00000000-0000-0000-0000-0000000000aa"


def _run_with_outputs(body: dict[str, Any]) -> dict[str, Any]:
    pipeline = {"code": "etl", "workspace": {"slug": "ws"}}
    if "logs" in body["query"]:
        outputs = [
            {"__typename": "BucketObject", "key": "out/cases.csv", "size": 19},
            {"__typename": "DatabaseTable", "tableName": "cases"},
            {
                "__typename": "GenericOutput",
                "genericName": "dashboard",
                "genericType": "link",
                "uri": "https://example.org/dashboard",
            },
        ]
        details = {
            "pipeline": pipeline,
            "logs": "",
            "messages": [],
            "outputs": outputs,
            "datasetVersions": [],
        }
        return {"pipelineRun": details}
    run = {
        "id": RUN_ID,
        "status": "success",
        "executionDate": "2026-11-01T10:00:00+00:00",
        "duration": 3,
        "triggerMode": "manual",
        "progress": 100,
        "config": {},
        "timeout": None,
        "user": None,
        "stoppedBy": None,
        "version": None,
        "pipeline": pipeline,
    }
    return {"pipelineRun": run}


def test_runs_show_prints_file_outputs_as_workspace_paths(fake_openhexa: FakeOpenHexa):
    fake_openhexa(_run_with_outputs)

    text = _invoke("runs", "show", RUN_ID)
    as_json = _invoke("runs", "show", RUN_ID, "--json")

    assert text.exit_code == 0, text.output
    assert "Files:\n  out/cases.csv  (19 B)\n" in text.stdout
    assert "Tables:\n  cases\n" in text.stdout
    assert "dashboard (link): https://example.org/dashboard" in text.stdout
    [file, *_] = json.loads(as_json.stdout)["outputs"]
    assert file == {"type": "file", "name": "out/cases.csv", "uri": None, "size": 19}


def _database_credentials(body: dict[str, Any]) -> dict[str, Any]:
    def credentials(username: str) -> dict[str, Any]:
        url = f"postgresql://{username}:secret-{username}@db.test:5432/ws"
        return {
            "host": "db.test",
            "port": 5432,
            "dbName": "ws",
            "username": username,
            "password": f"secret-{username}",
            "url": url,
        }

    assert "credentials" in body["query"]
    database = {"credentials": credentials("ws"), "readOnlyCredentials": credentials("ws_ro")}
    return {"workspace": {"database": database}}


# Saves the variables `database exec` sets to the file given as argument, then exits
# with code 7. The command's output does not go through the runner, so it is a file.
SAVE_ENV = (
    "import json, os, sys; "
    "keys = ['PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD', 'DATABASE_URL']; "
    "open(sys.argv[1], 'w').write(json.dumps({key: os.environ[key] for key in keys})); "
    "sys.exit(7)"
)


def test_database_exec_runs_a_command_with_the_credentials(
    fake_openhexa: FakeOpenHexa, tmp_path: Path
):
    fake_openhexa(_database_credentials)
    saved = tmp_path / "env.json"

    read_only = _invoke("database", "exec", "--", sys.executable, "-c", SAVE_ENV, str(saved))
    read_only_env = json.loads(saved.read_text())
    write = _invoke(
        "database", "exec", "--write", sys.executable, "-c", SAVE_ENV, str(saved), "--json"
    )

    assert read_only.exit_code == 7, read_only.output
    assert read_only_env == {
        "PGHOST": "db.test",
        "PGPORT": "5432",
        "PGDATABASE": "ws",
        "PGUSER": "ws_ro",
        "PGPASSWORD": "secret-ws_ro",
        "DATABASE_URL": "postgresql://ws_ro:secret-ws_ro@db.test:5432/ws",
    }
    assert write.exit_code == 7, write.output
    assert json.loads(saved.read_text())["PGUSER"] == "ws"


def test_database_exec_says_who_gets_credentials(fake_openhexa: FakeOpenHexa):
    nothing = {"credentials": None, "readOnlyCredentials": None}
    fake_openhexa(lambda _body: {"workspace": {"database": nothing}})

    result = _invoke("database", "exec", "--", "psql")

    assert result.exit_code == 4
    assert "only workspace editors and admins" in result.stderr


def _saved_query(*, update: bool, update_visibility: bool) -> Handler:
    """Answer lookups of a private saved query `cases` with the given permissions."""

    def handler(body: dict[str, Any]) -> dict[str, Any]:
        query = body["query"]
        if "updateSavedQuery(" in query or "deleteSavedQuery(" in query:
            pytest.fail("No mutation should be sent")
        item = {
            "id": "q1",
            "slug": "cases",
            "name": "Cases",
            "description": None,
            "visibility": "PRIVATE",
            "createdBy": {"displayName": "Ana"},
            "updatedAt": "2026-11-01T10:00:00+00:00",
            "content": "select 1",
            "permissions": {
                "update": update,
                "delete": update,
                "updateVisibility": update_visibility,
            },
            "workspace": {"slug": "ws"},
        }
        return {"savedQueryBySlug": item}

    return handler


@pytest.mark.parametrize(
    ("args", "update", "message"),
    [
        (["update", "cases", "--name", "New"], False, "only its author may change a private"),
        (["update", "cases", "--shared"], True, "only its author may share or unshare"),
        (["delete", "cases", "--yes"], False, "only its author may delete a private"),
    ],
)
def test_queries_changes_say_who_may_make_them(
    fake_openhexa: FakeOpenHexa, args: list[str], update: bool, message: str
):
    fake_openhexa(_saved_query(update=update, update_visibility=False))

    result = _invoke("queries", *args)

    assert result.exit_code == 4, result.output
    assert message in result.stderr


def _schedulable_pipeline(mutations: list[dict[str, Any]], *, missing: bool) -> Handler:
    """Answer lookups of pipeline `etl`, recording its update mutations."""

    def handler(body: dict[str, Any]) -> dict[str, Any]:
        query = body["query"]
        parameters = [
            {
                "code": code,
                "name": code,
                "type": "str",
                "required": True,
                "multiple": False,
                "default": None,
                "choices": None,
                "help": None,
            }
            for code in ("country", "region")
        ]
        version = _pipeline_version(parameters=parameters, config={"region": "north"})
        pipeline = {
            "id": "p1",
            "code": "etl",
            "name": "ETL",
            "description": "",
            "type": "zipFile",
            "schedule": None,
            "currentVersion": {"name": None, "versionNumber": 1},
        }
        if "updatePipelineVersion(" in query:
            mutations.append(body["variables"]["input"])
            config = body["variables"]["input"]["config"]
            updated = {**version, "config": config}
            return {
                "updatePipelineVersion": {"success": True, "errors": [], "pipelineVersion": updated}
            }
        if "updatePipeline(" in query:
            mutations.append(body["variables"]["input"])
            if missing:
                result = {
                    "success": False,
                    "errors": ["MISSING_VERSION_CONFIG"],
                    "details": "country",
                }
            else:
                scheduled = {**pipeline, "schedule": body["variables"]["input"]["schedule"]}
                result = {"success": True, "errors": [], "details": None, "pipeline": scheduled}
            return {"updatePipeline": result}
        return {"pipelineByCode": {**pipeline, "currentVersion": version}}

    return handler


def test_pipelines_update_sets_defaults_before_the_schedule(fake_openhexa: FakeOpenHexa):
    mutations: list[dict[str, Any]] = []
    fake_openhexa(_schedulable_pipeline(mutations, missing=False))

    result = _invoke(
        "pipelines", "update", "etl", "--schedule", "0 6 * * 1", "-p", "country=BE", "--json"
    )

    assert result.exit_code == 0, result.output
    assert mutations == [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "config": {"region": "north", "country": "BE"},
        },
        {"id": "p1", "schedule": "0 6 * * 1"},
    ]
    shown = json.loads(result.stdout)
    assert shown["schedule"] == "0 6 * * 1"
    assert shown["version"]["config"] == {"region": "north", "country": "BE"}


def test_pipelines_update_names_parameters_missing_for_a_schedule(fake_openhexa: FakeOpenHexa):
    fake_openhexa(_schedulable_pipeline([], missing=True))

    result = _invoke("pipelines", "update", "etl", "--schedule", "0 6 * * 1")

    assert result.exit_code == 1
    assert "(country)" in result.stderr
    assert "--param CODE=VALUE" in result.stderr


WEBAPP_VERSIONS = ["c3" * 20, "b2" * 20, "a1" * 20]


def _webapp(mutations: list[dict[str, Any]]) -> Handler:
    """Answer lookups of static webapp `site`, private with read scopes, recording updates."""
    webapp = {
        "id": "w1",
        "slug": "site",
        "name": "Site",
        "description": "",
        "type": "STATIC",
        "url": "https://site.test/",
        "isPublic": False,
        "allowedOperations": ["DATASETS_READ"],
        "source": {"__typename": "GitSource", "publishedVersion": WEBAPP_VERSIONS[0]},
    }

    def handler(body: dict[str, Any]) -> dict[str, Any]:
        query = body["query"]
        if "updateWebapp(" in query:
            changes = body["variables"]["input"]
            mutations.append(changes)
            updated = {**webapp, **{k: v for k, v in changes.items() if k in webapp}}
            if "publishedVersionId" in changes:
                source = {
                    "__typename": "GitSource",
                    "publishedVersion": changes["publishedVersionId"],
                }
                updated["source"] = source
            return {"updateWebapp": {"success": True, "errors": [], "webapp": updated}}
        if "versions(" in query:
            items = [
                {"id": v, "message": "m", "date": "2026-11-01T10:00:00+00:00", "authorName": "Ana"}
                for v in WEBAPP_VERSIONS
            ]
            return {"webapp": {"versions": {"items": items}}}
        return {"webapp": webapp}

    return handler


def test_webapps_update_warns_about_public_access_and_write_scopes(fake_openhexa: FakeOpenHexa):
    mutations: list[dict[str, Any]] = []
    fake_openhexa(_webapp(mutations))

    result = _invoke("webapps", "update", "site", "--public", "--scope", "files_write")

    assert result.exit_code == 0, result.output
    assert mutations == [{"id": "w1", "isPublic": True, "allowedOperations": ["FILES_WRITE"]}]
    assert "anyone can now open https://site.test/" in result.stderr
    assert "may now FILES_WRITE" in result.stderr


def test_webapps_update_serves_an_earlier_version(fake_openhexa: FakeOpenHexa):
    mutations: list[dict[str, Any]] = []
    fake_openhexa(_webapp(mutations))

    result = _invoke("webapps", "update", "site", "--version", "a1a1", "--name", "Old", "--json")
    unknown = _invoke("webapps", "update", "site", "--version", "")

    assert result.exit_code == 0, result.output
    # OpenHEXA drops other changes made with the version, so they are sent separately.
    assert mutations == [
        {"id": "w1", "name": "Old"},
        {"id": "w1", "publishedVersionId": WEBAPP_VERSIONS[2]},
    ]
    assert json.loads(result.stdout)["version"] == WEBAPP_VERSIONS[2]
    assert "Warning" not in result.stderr
    assert unknown.exit_code == 3


def test_webapps_download_saves_what_it_can_and_lists_the_rest(
    fake_openhexa: FakeOpenHexa, tmp_path: Path
):
    def handler(body: dict[str, Any]) -> dict[str, Any] | httpx2.Response:
        if "files(" in body["query"]:
            files = [
                {
                    "path": "big.bin",
                    "type": "file",
                    "size": 12_000_000,
                    "encoding": "BASE64",
                    "tooLarge": True,
                    "content": None,
                },
                {
                    "path": "index.html",
                    "type": "file",
                    "size": 4,
                    "encoding": "TEXT",
                    "tooLarge": False,
                    "content": "<h1>",
                },
            ]
            return {"webapp": {"files": files}}
        return _webapp([])(body)

    fake_openhexa(handler)
    output = tmp_path / "site"

    result = _invoke("webapps", "download", "site", "-o", str(output), "--json")

    assert result.exit_code == 1
    assert (output / "index.html").read_text() == "<h1>"
    assert not (output / "big.bin").exists()
    assert json.loads(result.stdout) == [
        {"path": "big.bin", "size": 12_000_000, "local_path": None, "skipped": True},
        {
            "path": "index.html",
            "size": 4,
            "local_path": str(output / "index.html"),
            "skipped": False,
        },
    ]
    assert "Skipped big.bin (12.0 MB)" in result.stderr
    assert json.loads(result.stderr.splitlines()[-1])["error"]["type"] == "Error"
