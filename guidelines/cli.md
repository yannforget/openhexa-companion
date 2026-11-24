# CLI guidelines

These rules apply to `src/openhexa/companion/cli/`. They keep the `hexa` commands
predictable for people and agents.

- Commands are `hexa <noun> <verb>`. Nouns are OpenHEXA objects in the plural, such as
  `datasets`, `pipelines` and `runs`, plus `local` for the local copy of the workspace.
  `search` is the only top-level verb.
- Reuse the existing verbs before inventing new ones:
  - `list`, `show`, `create`, `update` and `delete` mean what they say.
  - `upload` and `download` copy files.
  - `publish` creates a new version that others see.
  - `run` runs a pipeline.
- Arguments: the identifier of the remote object comes first, then local paths. Remote
  destinations and download locations are options.
- An option name means the same thing everywhere:
  - `--version` selects an existing version.
  - `--name` names what the command creates or renames.
  - `-m/--message` describes a change.
  - `--description` describes the object.
  - `--force` replaces existing files.
  - `-o/--output`, `-n/--limit` and `-y/--yes` work as usual.
- Output:
  - The result goes to stdout; progress and notes go to stderr.
  - Every command accepts `--json`, which `HEXA_JSON=1` turns on. The JSON is the
    object the command names, or an array for lists. Errors are
    `{"error": {"type", "message"}}`.
  - An empty or cut list says so on stderr.
- Exit codes: 1 for an error, 2 for misuse, 3 for not found, 4 for permission denied,
  5 for a run that did not succeed.
- Safety:
  - Deletes ask for confirmation; without a terminal, they need `--yes`.
  - Nothing is overwritten without `--force`.
  - Secrets are hidden without `--show-secrets`.
- Keep commands thin: the logic belongs in the library. Add a command when a use case
  needs it, not to mirror the API. A command may break a rule if following it would
  make the command worse; its help then says why.
