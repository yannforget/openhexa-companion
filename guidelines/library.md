# Library guidelines

These rules apply to `src/openhexa/companion/` outside `cli/` and `server/`.

- The library never reads environment variables, `.env` files or configuration:
  callers pass credentials explicitly.
- A client is scoped to one workspace. Queries go through that workspace, never
  through global lookups by ID.
- Error messages must give an agent enough context to act: what was attempted,
  where, what went wrong and, when useful, the valid options. Interface-specific
  hints, such as command or tool names, belong to the CLI or the server.
- Never include tokens or storage URLs in messages or logs. A signed storage URL is
  only returned by a method that exists to return it, such as
  `Datasets.download_url`.
- Use dataclasses for internal data; keep Pydantic at API boundaries.
