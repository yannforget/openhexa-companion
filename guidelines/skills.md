# Skill guidelines

These rules apply to the skills in `skills/`. The `hexa` CLI and the MCP connector
document each command and tool on their own, through help text, tool descriptions and
error messages; the user documentation explains how to install and use them. The skills
fill the remaining gap: they teach an agent how to do real work with OpenHEXA across
several commands or tools, such as how the data model fits together, which version to
use and cite, and when to ask before writing. `openhexa-cli` is for coding agents with a
shell, `openhexa-connector` for chat clients with the connector.

- The description says what the skill is for and in which environment, so the right
  one loads.
- Only write what the tools can't say; don't repeat help text, tool descriptions or
  errors.
- Keep a line only if it prevents a real mistake or a wasted step.
- When a tool causes the mistake, fix the tool rather than adding text.
- Name commands and tools exactly as they are.
- Give decision rules and facts, with the reason when it isn't obvious, not emphatic
  commands.
- Keep `SKILL.md` short, and say when to read each reference.
- Write for every supported client: no client-specific features, secrets, instance URLs
  or workspace names.
- Sections shared by both skills are marked as such; change them in both.
