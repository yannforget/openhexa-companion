# OpenHEXA Companion

Work with [OpenHEXA](https://www.openhexa.org) workspaces from your terminal, a coding
agent or an AI chat client.

- **`hexa`** is a CLI for people and coding agents. Find, download and publish data, run
  pipelines locally against real or local data, deploy them, investigate runs and
  publish webapps.
- **An MCP server** for AI chat clients such as Claude. Explore workspaces, analyse
  complete dataset files, query the database and investigate runs.
- **`openhexa.companion`** is the Python library both are built on.

Everything goes through the OpenHEXA API with a workspace token: no changes to OpenHEXA
are needed, and you can do what your token allows, nothing more.

> The project is in early development: commands, tools and the Python API may change.

## The `hexa` CLI

### Installation

`hexa` requires Python 3.15 or later. With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/yannforget/openhexa-companion
```

Running pipelines locally also needs Docker or Podman.

### Configuration

`hexa` works with the workspace set in `.env`, in the current directory:

```bash
OPENHEXA_URL=https://app.openhexa.org
OPENHEXA_WORKSPACE=my-workspace
OPENHEXA_TOKEN=...
```

You get a workspace token in OpenHEXA, under **Pipelines → Create**. Check the setup
with `hexa workspace show`.

### Usage

Commands are grouped by what they work on: `workspace`, `search`, `datasets`, `files`,
`database`, `queries`, `connections`, `pipelines`, `runs`, `local` and `webapps`. See
the [CLI reference](docs/cli.md), or run `hexa <command> --help`.

Find and read data:

```bash
hexa search malaria paludisme
hexa datasets show population
hexa datasets files population --version v4
hexa datasets read population districts.csv --version v4 | head
hexa datasets download population --version v4 -o data/
hexa files download reports/2025/summary.csv
```

Query the workspace database:

```bash
hexa database tables
hexa queries sql monthly-cases > monthly_cases.sql
hexa database exec -- psql -c "select district, sum(cases) from cases group by 1"
hexa database exec -- python analyse.py   # with PG* variables set, no password to copy
```

Publish a dataset version:

```bash
hexa datasets publish population data/ --name 2026-q1 -m "Add Q1 estimates"
```

Develop, test and deploy a pipeline:

```bash
hexa local run pipelines/import_data -p country=BFA
hexa pipelines publish import-data pipelines/import_data -m "Handle missing districts"
hexa pipelines run import-data -p country=BFA --wait
```

Investigate a run:

```bash
hexa runs list import-data
hexa runs show <run-id>
hexa runs logs <run-id> -f
hexa pipelines source import-data --version v7
```

Publish a webapp:

```bash
hexa webapps publish district-dashboard dist/
```

Every command accepts `--json`, for scripts and agents.

### Local pipeline runs

`hexa local run` runs a pipeline on your machine, in the same image as on OpenHEXA,
without deploying it. It can work with the real workspace, with local copies of its data,
or with both:

- Datasets and connections are read from the workspace, unless you have a local copy.
  Copies let you test with other data, or point a connection at a test server.
- Workspace files are always local copies.
- The run can't change the workspace unless you pass `--allow-remote-writes`.

```bash
hexa local pull datasets/population files/reports connections/dhis2-test
hexa local run pipelines/import_data
hexa local status
```

See [Local pipeline runs](docs/local-runs.md).

### Coding agents

Coding agents can use `hexa` as is. The [`openhexa-cli` skill](skills/openhexa-cli/)
helps them use it well, from finding data to writing and deploying pipelines:

```bash
cp -r skills/openhexa-cli ~/.claude/skills/   # Claude Code
```

## MCP

The remote MCP server gives AI chat clients tools to:

- list and search workspaces, and read or edit their homepage;
- browse datasets and download complete files into the client's code sandbox;
- list tables, run read-only SQL queries, and read and save Data Studio queries;
- read pipelines, their code, runs and logs, and start or stop runs;
- read webapps.

See the [MCP tool reference](docs/mcp-tools.md) for every tool.

To use it in Claude:

1. Add `https://<companion host>/mcp` as a custom MCP server.
2. Select **Connect**, and enter a slug and token for each workspace Claude may use.
3. To analyse dataset files, allow the storage host in Claude's code sandbox settings:
   `storage.googleapis.com` for hosted OpenHEXA.

You can manage your connected clients and workspaces at `https://<companion host>/account`.

The [`openhexa-mcp` skill](skills/openhexa-mcp/) helps Claude analyse data
and investigate runs. Download it from the
[latest release](https://github.com/yannforget/openhexa-companion/releases/latest).

See [MCP](docs/mcp.md) for how access works and its limits, and
[Deploying the MCP server](docs/deployment.md) to run your own.

## The Python library

```python
from openhexa.companion import Client

with Client("https://app.openhexa.org", "my-workspace", token) as client:
    version = client.datasets.version("population", "v4")
    file = client.datasets.file(version, "districts.csv")
    with client.datasets.read(file) as stream:
        data = stream.read()
```

The client also covers files, the database, saved queries, connections, pipelines, runs
and webapps. Use the [OpenHEXA SDK](https://github.com/BLSQ/openhexa-sdk-python) for code
running inside pipelines.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff format . && uv run ruff check . && uv run pyrefly check
```

Tests run offline by default. `-m workspace` runs the tests that use the workspace in
`.env`, and `-m container` those that run pipelines with Docker or Podman.

See [Architecture](docs/architecture.md) for how the project is organised.
