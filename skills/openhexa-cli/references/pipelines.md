# Writing SDK pipelines

Read this before writing or changing a pipeline. The SDK documentation is at
<https://docs.openhexa.com/writing-pipelines/>.

## Shape

A pipeline is a directory with `pipeline.py` at its root:

```python
from openhexa.sdk import Dataset, DHIS2Connection, current_run, parameter, pipeline, workspace


@pipeline(name="Extract registry", timeout=3600)
@parameter("source", name="Source", type=DHIS2Connection)
@parameter("output", name="Output dataset", type=Dataset, default="registry")
@parameter("years", name="Years", type=int, multiple=True, required=False)
def extract_registry(source, output, years):
    path = fetch(source, years)
    publish(path, output)


@extract_registry.task
def fetch(source, years) -> str:
    ...
    return str(path)


@extract_registry.task
def publish(path: str, output: Dataset) -> None: ...


if __name__ == "__main__":
    extract_registry()
```

- OpenHEXA reads the decorators without running the code, so keep their arguments
  literal: no variables, computed values or aliased imports. `@pipeline` goes above the
  `@parameter` decorators.
- Parameter codes are `snake_case` and match the function's arguments. `name` is the
  label of the web form; add `help` when the expected value isn't obvious.
- Import sibling modules as plain modules (`from helpers import x`): the pipeline
  directory is on the import path, and relative imports fail.

## Tasks

The pipeline function only builds a graph: calling a task returns a placeholder, not
its result. Tasks then run in separate processes, each once its inputs are ready.

- Pass a placeholder directly as an argument to declare a dependency. Placeholders
  inside lists or dicts are not resolved, and indexing or branching on one doesn't work.
  Branching on parameter values is fine.
- Arguments and results must be picklable. Create HTTP clients and database connections
  in the task that uses them.
- Tasks writing and reading the same file don't depend on each other unless one passes
  the path to the other.
- A logged error doesn't fail the run; raise an exception.

## Parameters

- `str`, `int`, `float` and `bool` take literal values; `choices=[...]` must be literal
  too.
- `Dataset` takes a dataset slug and gives a `Dataset` object.
- `File` takes a path in the workspace files and gives an object with `.path`.
- Connection types (`DHIS2Connection`, `IASOConnection`, `PostgreSQLConnection`,
  `S3Connection`, `GCSConnection`, `CustomConnection`) take a connection slug and give an
  object with its fields.
- `Secret` gives a string, masked in OpenHEXA; don't give it a default.
- `required=False` gives `None` when omitted, or `[]` with `multiple=True`.

## Workspace resources

- **Workspace files**: `Path(workspace.files_path) / "data/cases.csv"`. Write outputs
  under a directory of their own rather than at the root, and do heavy intermediate work
  in `workspace.tmp_path`.
- **Connections**: through a parameter, or `workspace.dhis2_connection("slug")` and
  similar, or `workspace.get_connection("slug")`. Never log them.
- **Database**: `workspace.database_url`, with any PostgreSQL client. Not available in
  local runs.
- **Datasets**: `workspace.get_dataset("slug")`, then `.latest_version` (`None` if
  there is none) or `.versions`; a version has `.files` and `.get_file(path)`, and a file
  has `.read()`.
- **Publishing a dataset version**: `version = dataset.create_version("v5")`, then
  `version.add_file(local_path, filename="sub/dir/name.csv")` for each file. A new
  version starts empty: add every file it should contain. Pick the name after looking
  at the existing versions. If publication fails part-way, the version is left
  incomplete.
- **Reporting**: `current_run.log_info`, `log_warning` and `log_error` show messages in
  the run. `current_run.add_file_output(path)` and `add_database_output(table)` record
  outputs that already exist; they don't write anything.

Use the SDK for datasets, files and connections rather than calling the API directly.

## Local and hosted runs

Hosted runs read and write the real workspace files, datasets and database. Local runs
(`hexa local run`) use `.hexa/workspace/` for what was pulled, and the workspace for the
rest. Code that works locally can therefore write to the shared workspace files once
deployed. Don't branch on `HEXA_SERVER_URL` or other environment details to tell them
apart.

## Packaging

`hexa pipelines publish` sends the whole directory except hidden files,
`__pycache__` and `workspace/`, so keep data and secrets out of it. A `requirements.txt`
is installed before each run, on top of the image's packages. Load bundled files
relative to `__file__`.
