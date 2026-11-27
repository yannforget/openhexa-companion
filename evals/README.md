# Evals

Manual evals of the companion's tools (the `hexa` CLI, the MCP server and the skills)
and of the official OpenHEXA MCP server, on a workspace of synthetic data.

- `workspace/`: what goes into the test workspace: homepage, datasets and pipelines.
- `push.sh`: pushes `workspace/` to an empty workspace.
- `reset.sh`: undoes what sessions change in the workspace.
- `tasks.md`: the tasks, with the expected results.
- `Containerfile`: the environment of every session: pi, uv, Python 3.13 with data
  libraries (`env/`), and `hexa`.
- `mcp/`: pi configuration of each MCP server, one server per file.
- `pi.sh`: starts a session for a task and configuration, in a container.
- `summary.py`: time, tokens and cost of the saved sessions.
- `results/`: summaries of eval runs.

## Test workspace

The data imitates DRC health data for three provinces. It includes traps that agents
should avoid by reading the dataset READMEs, changelogs and metadata: a partial dataset
version, a renamed column, two health zones with the same name, survey calculations
described in a README, and legacy datasets (`ecv-2025`, `formations-sanitaires`).

To set it up, create an empty workspace on the demo instance, get a workspace token
under **Pipelines → Create**, write the credentials in `.env.eval` at the repository
root (ignored by Git), then:

```bash
evals/push.sh .env.eval
```

The pipeline's first run fails: task 3 is about fixing it.

`workspace/datasets/<slug>/` holds `dataset.md` (name on the first line, then the
description) and one folder per version, published in order. Edit or replace the files
by any means; a dataset or pipeline added there is pushed too.

## Setup

Build the image, from the repository root, and again after changing `hexa`, `env/` or
the pi version:

```bash
podman build -t companion-evals -f evals/Containerfile --ignorefile evals/Containerfile.dockerignore .
```

The hosted companion MCP server serves app.openhexa.org, not the demo instance, so the
evals use a local one. Write its configuration in `.env.eval-server` (ignored by Git):

```bash
COMPANION_PUBLIC_URL=http://localhost:8765
COMPANION_OPENHEXA_URL=https://app.demo.openhexa.org
COMPANION_SECRET_KEY=<random, at least 32 characters>
COMPANION_DATABASE=<path of a SQLite file outside the repository>
```

and keep it running during the sessions. It runs the code of the checkout:

```bash
uv run --env-file .env.eval-server python -m openhexa.companion.server --port 8765
```

Sign in to each MCP server once, on your machine, choosing the test workspace. From the
repository root:

```bash
mkdir -p evals/login/.pi && cp evals/mcp/companion.json evals/login/.pi/mcp.json
cd evals/login && pi --no-extensions -e builtin:mcp --approve   # /mcp login companion
```

and the same with `official.json` and `/mcp login official`. Delete `evals/login/`
afterwards. Logins last an hour: `pi.sh` renews them on your machine before starting a
session, so sessions can run in parallel, in several panes. If one reports that a server
needs to sign in, do it again.

## Running a session

```bash
evals/pi.sh 1 mcp-skill --model openai/gpt-6.2-sol
```

starts pi for task 1 in the `mcp-skill` configuration. Paste the task's prompt from
`tasks.md`, let the agent work, and answer briefly if it asks a question. Quit when it's
done: the session is saved in `evals/sessions/` (ignored by Git), named `1 mcp-skill`.
Options after the configuration go to pi: use the same `--model` for a whole series.

| Configuration | What the agent gets |
| --- | --- |
| `cli-skill` | `hexa`, with `.env.eval` as `.env`, and the `openhexa-cli` skill |
| `mcp-skill` | the local companion MCP server and the `openhexa-mcp` skill |
| `official` | the official OpenHEXA MCP server of the demo instance |

Models: `openai/gpt-6.2-sol` and `openai/gpt-6.1-terra`.

Each session runs in a new container, thrown away at the end. It starts in a uv project
with Python 3.13, polars, pandas, pyarrow and duckdb, which the agent is told about. It
sees no file of your machine except the skills, and gets only the credentials it needs:
the key of the model's provider and, for the MCP configurations, the sign-in of one
server. Your own skills, extensions and `AGENTS.md` are turned off. Each `mcp/*.json`
has `"exposure": "direct"`, so that the agent sees the tools as a chat client does.

The container shares your machine's network, to reach the local companion server.

In `cli-skill`, `hexa local run` works: it starts the pipeline image with your machine's
Podman, through its socket. The session then works in a folder of `/tmp`, deleted at the
end. Pull the pipeline image beforehand (`podman pull
docker.io/blsq/openhexa-blsq-environment:latest`, about 9 GB), so that the first session
doesn't spend minutes downloading it.

## Grading

```bash
python3 evals/summary.py
```

prints one line per session: time, turns, tool calls and errors, tokens and cost.
`pi --session <file>` reopens a session; `pi --export <file>` renders it as HTML.
Record, in a spreadsheet:

- **outcome:** `done`, `declined` (says clearly it can't be done here), `failed`
  (gives up or gets stuck) or `wrong` (answers confidently but wrongly);
- **quality:** 0 to 2: method explained, caveats, clarity;
- **notes:** what went wrong.

After task 3, and after `official` sessions, run `evals/reset.sh .env.eval`: agents
deploy pipelines and publish dataset versions.
