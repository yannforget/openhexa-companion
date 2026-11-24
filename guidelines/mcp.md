# MCP guidelines

These rules apply to the MCP server's tools and server instructions, in
`src/openhexa/companion/server/`. They keep the tools predictable for models that see
only a list of names and descriptions.

- Guidance has three places, each with its own job:
  - Tool descriptions and responses state facts: what the tool does, what its
    arguments mean, what it returns and its limits. They may name the tool that
    provides an input or comes next. They don't tell the model how to behave with the
    user or describe workflows across tools.
  - Server instructions are a short orientation: what OpenHEXA is, where to start,
    asking before writes, and where to get the skill.
  - The `openhexa-mcp` skill holds the workflows and judgement.
- Naming:
  - Tools are `openhexa_<verb>_<noun>`, reusing `list`, `get`, `create`, `update`,
    `delete`, `set`, `start` and `stop`.
  - The title and first sentence of a description make sense on their own and name
    OpenHEXA.
  - An identifier in a response has the name of the argument it feeds, such as
    `dataset`, `pipeline` or `run`, so it can be passed back as is.
  - A field or argument name means the same thing in every tool.
- Responses:
  - Return structured results. Include what the next call or the answer needs, such
    as the exact version, and leave out the rest.
  - Bound what a tool returns. When a result is cut, say so in `note` and how to get
    the rest.
  - Never return secrets: mask secret parameters, never include the OpenHEXA token.
- Errors say what went wrong and, when it helps, the valid options or how to fix it.
- Annotations match what the tool does: read-only, creating something new, or
  overwriting or removing.
- Keep tools thin: the logic belongs in the library. Add a tool when a use case needs
  it, not to mirror the API. A tool may break a rule if following it would make the
  tool worse; its code then says why.
