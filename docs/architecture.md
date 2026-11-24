# Architecture

The companion is one Python package with three parts:

```
terminal, coding agent ──▶ hexa CLI ───────┐
                                           ├──▶ library ──▶ OpenHEXA API
AI chat client ─────────▶ MCP server ──────┘
```

| Part | Code | Role |
| --- | --- | --- |
| Library | `src/openhexa/companion/` | Workspace operations over the OpenHEXA GraphQL API |
| CLI | `src/openhexa/companion/cli/` | The `hexa` command, including local pipeline runs |
| MCP server | `src/openhexa/companion/server/` | MCP server with OAuth, for AI chat clients |

The CLI and the MCP server both call the library. The server's dependencies are in
the `server` extra, so installing the CLI doesn't pull them in.

## Principles

- **No changes to OpenHEXA.** Everything goes through the public API, with workspace
  tokens, so the companion can do what a token allows and nothing more.
- **One workspace at a time.** A library client, and a `hexa` project, work with one
  workspace. The MCP server holds one token per workspace.
- **No hosted compute.** The MCP server doesn't process data: AI clients download
  dataset files from OpenHEXA's storage and analyse them in their own sandbox. Local
  runs use your machine.

## Library

`Client` is the entry point, with an attribute per area: `datasets`, `files`,
`database`, `saved_queries`, `connections`, `pipelines`, `runs` and `webapps`. It takes
credentials explicitly, never reads configuration, and raises the exceptions in
`errors.py`.

## CLI

One module per command group. Configuration is read from the environment and `.env`.

`hexa local run` starts the pipeline image with a small stand-in for the OpenHEXA API,
which serves the local copies and forwards everything else to the workspace. The
pipeline runs unchanged, with the image's own SDK. The code is in `cli/local/` and
`cli/_runtime/`.

## MCP server

An ASGI application built on the official MCP Python SDK. It serves:

- `/mcp`, the MCP endpoint, with the tools in `server/tools/`;
- an OAuth authorization server for AI clients;
- `/connect`, where users enter workspace tokens when connecting a client;
- `/account`, where users manage their clients and tokens.

Workspace tokens are stored encrypted in SQLite. AI clients receive the server's own
OAuth tokens, never OpenHEXA's.

## Skills

`skills/` holds the `openhexa-cli` and `openhexa-mcp` agent skills. They are
attached to each GitHub release.

## Stack

Python 3.15, uv, click, httpx2, the MCP Python SDK with Starlette and uvicorn, Jinja2,
SQLite and cryptography. Tests use pytest; linting and type checks use ruff and pyrefly.
