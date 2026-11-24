- Do not edit AGENTS.md unless the user explicitely requests it.

## Project

OpenHEXA Companion is a Python library for working with OpenHEXA workspaces, with
two interfaces built on it: the `hexa` CLI for people and coding agents working
locally, and a remote MCP connector for AI clients such as Claude. It needs no
changes to OpenHEXA and is limited to what the GraphQL API allows with a workspace
token.

Read [docs/architecture.md](docs/architecture.md) for the goals, use cases and
architecture.

## Project structure

- `src/openhexa/companion/`: the library. `openhexa` is a namespace package
  shared with `openhexa.sdk`, so never add `src/openhexa/__init__.py`.
- `src/openhexa/companion/cli/`: the `hexa` CLI.
- `src/openhexa/companion/server/`: the MCP connector. Its
  dependencies are in an optional `server` extra; the library and CLI must not
  import it.
- `tests/`: tests.
- `docs/`: design documents.
- `guidelines/`: rules for writing code in this project.
- `plans/`: temporary implementation plans for handing context between sessions.
  Not tracked by Git and not documentation.
- `research/`: short notes on findings from explorations, one Markdown file per
  question. Not tracked by Git.

## Related resources

Sibling checkouts, relative to this repository:

- `../openhexa-app/frontend/schema.generated.graphql`: the full GraphQL schema.
  Resolvers and permission checks are in `../openhexa-app/backend/hexa/<domain>/`.
- `../openhexa-app/docs/en/`: user documentation, also published at
  https://docs.openhexa.com/.
- `../openhexa-sdk-python/`: the `openhexa.sdk` package that pipelines use, which
  shares the `openhexa` namespace with this project.
- `../openhexa-docker-images/images/base/`: the image that runs pipelines, and its
  entrypoint. Relevant to local runs.
- `../openhexa-app/backend/hexa/mcp/`: the native OpenHEXA MCP that the connector
  replaces.
- `../openhexa-dev-env/`: the previous `hexa` CLI, which this project replaces.
  Useful for knowing which GraphQL operations, storage behaviour and edge cases to
  expect, but not a design to copy.

External: the [MCP specification](https://modelcontextprotocol.io/specification),
especially its authorization section.

## Development

Read the relevant guidelines before changing code:

- [guidelines/python.md](guidelines/python.md): any Python code.
- [guidelines/library.md](guidelines/library.md): the library, outside `cli/` and `server/`.
- [guidelines/cli.md](guidelines/cli.md): the `hexa` CLI, in `cli/`.
- [guidelines/mcp.md](guidelines/mcp.md): the MCP connector, in `server/`.

For Python changes, run:

```bash
uv run pytest -q
uv run ruff format .
uv run ruff check .
uv run pyrefly check
```

- Use demo workspace credentials in `.env` for testing (OPENHEXA_URL, OPENHEXA_WORKSPACE, OPENHEXA_TOKEN).
- To test the MCP connector with an AI client such as Claude, see
  [docs/local-development.md](docs/local-development.md).

## Commit conventions

- Prefer squash-merging pull requests.
- Use semantic commit messages, such as `feat: add workspace listing` or
  `fix: handle missing config`. Scopes are optional.
- While the project is in early development, breaking changes do not need a `!` marker.
- Keep commit messages short and simple. Add a body only to explain intent or the
  “why” when it is not obvious from the code; do not restate what the diff shows.
