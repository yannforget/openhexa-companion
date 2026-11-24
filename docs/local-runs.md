# Local runs

`hexa run` runs a pipeline from a local directory, without deploying it, in the same
image as hosted runs. Workspace files, datasets and connections can be local copies, so
you can iterate without changing the workspace; anything not copied is read from the
real workspace. When the pipeline works, deploy it with `hexa pipelines deploy`.

```console
$ hexa datasets download population
$ hexa run pipelines/import_data -p country=BFA
Image: docker.io/blsq/openhexa-blsq-environment:latest (default: the workspace sets none, ...)
Local: files, dataset population (v4), connection dhis2-prod
Remote writes: refused (--allow-remote-writes to allow)
...
Run succeeded in 21 s.
  Messages: 3 info, 1 warning
  Outputs: reports/summary.csv
  New local versions: population v5 (2 files)
```

## Setup

You need a container engine:

| Platform | Engine | Notes |
| --- | --- | --- |
| Linux | Docker, or Podman 4.3 or later (rootless) | Files the pipeline writes belong to you |
| Windows | WSL 2 with Docker (Docker Desktop's WSL integration, or Docker Engine in the distribution) | Keep the project in the WSL filesystem, not under `/mnt/c` |
| macOS | Docker Desktop | On Apple Silicon, enable "Use Rosetta for x86/amd64 emulation". Keep the project under your home folder |

Not supported for now: macOS with Podman, native Windows. OrbStack, Colima and Podman
in WSL may work but are not tested.

`hexa run` uses `--engine`, else the `HEXA_CONTAINER_ENGINE` variable, else the first
of `docker` and `podman` that answers. Inside a Distrobox or toolbox container, use the
host's engine:

```bash
export HEXA_CONTAINER_ENGINE="distrobox-host-exec podman"
```

The project must then be in a folder the host sees, such as your home folder.

### The image

The pipeline runs in the workspace's image (`hexa workspace` shows it), else
`blsq/openhexa-blsq-environment:latest`, or the image given with `--image`. The API
does not expose the instance's default image, so when the workspace sets none, hosted
runs may use another one: `hexa run` says which image it uses and why.

The first run pulls the image, which takes several GB. Later runs reuse it. To pull it
yourself beforehand:

```bash
docker pull --platform linux/amd64 blsq/openhexa-blsq-environment:latest
```

The image is always run as `linux/amd64`, emulated on Apple Silicon.

## Project layout

```
my-project/
  .env                          # OPENHEXA_URL, OPENHEXA_WORKSPACE, OPENHEXA_TOKEN
  .hexa/workspace/              # local copy of parts of the workspace
    files/                      # workspace files
    datasets/<slug>/<version>/  # one folder per dataset version
    connections/<slug>.json     # one file per connection
  pipelines/import_data/pipeline.py
```

Run `hexa` from the project root, where `.env` is. `.hexa/workspace/` holds data and
connection secrets: `hexa` creates a `.gitignore` in it so Git ignores it.

Downloads go there by default, so `hexa run` uses what you download:

```bash
hexa files download reports/        # → .hexa/workspace/files/reports/
hexa datasets download population   # → .hexa/workspace/datasets/population/v4/ (latest version)
hexa datasets download population --version v3
```

Use `-o DIR` to save a download elsewhere, out of reach of `hexa run`. Downloads go to
the `.hexa/` folder of the current directory, so run them from the project root.

Connections are saved from the output of `hexa connections show`, with their secrets:

```bash
mkdir -p .hexa/workspace/connections
hexa connections show dhis2-prod --show-secrets --json > .hexa/workspace/connections/dhis2-prod.json
```

Only `type` and `fields` are read. `hexa run` refuses a connection file with missing
values, such as one saved without `--show-secrets`.

You can also create, edit or delete the folders by hand. Delete a dataset's folder to
read it from the workspace again.

## Running

```bash
hexa run [SOURCE] [-p CODE=VALUE ...] [--params-file FILE] [--image IMAGE]
         [--engine COMMAND] [--ignore-local-datasets] [--ignore-local-connections]
         [--allow-remote-writes] [--dry-run] [--json]
```

- `SOURCE` is the directory with `pipeline.py`, the current directory by default. The
  files `hexa pipelines deploy` would send are copied and run; hidden files, `.env` and
  a `workspace/` folder are left out. A `requirements.txt` is installed at each run, as
  in hosted runs.
- Parameters are given as in `hexa runs start`: `-p CODE=VALUE`, with VALUE read as
  JSON when it can be (`-p year=2025`, `-p 'districts=["A","B"]'`), or `--params-file`
  with a JSON object. Datasets, connections and files are given by slug or path.
- The output of the pipeline is printed as it runs, then a summary: messages, outputs,
  new local dataset versions and refused remote writes. With `--json`, the pipeline's
  output goes to stderr and the summary is printed on stdout as JSON.
- The exit code is the pipeline's: 0 on success. Ctrl-C interrupts the pipeline, so its
  `finally` blocks run, stops the container if it has not ended after 10 seconds, and
  exits with 130.

## What is local, what is real

| | Local copy | Otherwise |
| --- | --- | --- |
| Workspace files (`workspace.files_path`, `workspace.get_file`, `File` parameters) | Always `.hexa/workspace/files/`, read and written by the pipeline | Not available: download what the pipeline reads |
| Datasets (`workspace.get_dataset`, `Dataset` parameters, `list_datasets`) | `.hexa/workspace/datasets/<slug>/`, unless `--ignore-local-datasets` | Read from the workspace |
| Connections (`workspace.get_connection`, connection parameters) | `.hexa/workspace/connections/<slug>.json`, unless `--ignore-local-connections` | Read from the workspace |
| `current_run.log_*`, `add_file_output` | Printed and summarised | |

Local datasets:

- The latest version is the greatest folder name in natural order: `v2` before `v10`;
  dates sort correctly.
- `create_version` creates a folder, and `add_file` writes into it. Neither replaces
  an existing version or file. Unlike OpenHEXA, any version accepts new files, not only
  the latest. Publish a version with
  `hexa datasets upload population .hexa/workspace/datasets/population/v5 --version v5`.
- A dataset shared from another workspace is saved under its slug, as pipelines name
  it. Calls naming its source workspace (`source_workspace_slug`) read the workspace.

Changes to the workspace, such as creating a dataset or a version of a remote dataset,
are refused: the SDK raises an error saying so. Use a local copy, or
`--allow-remote-writes` to let the pipeline change the workspace.

Local runs prevent mistakes; they don't isolate untrusted code:

- Remote reads are real, and connections point to real systems: a DHIS2 connection
  still writes to that DHIS2. Only the OpenHEXA API is intercepted.
- The pipeline gets a per-run token that only the local API accepts. Code that calls
  OpenHEXA without the SDK fails. The real token is in the container, in the startup
  process, so code that deliberately looks for it can find it.
- The workspace database is not available, nor is the progress of the run (the SDK
  prints it instead).

## Troubleshooting

Start with `--dry-run`: it runs the checks (credentials, engine, image, local copy)
and prints the engine command without starting anything.

| Problem | Fix |
| --- | --- |
| `No container engine answers` | Check that `docker version` or `podman version` works, or pass `--engine` |
| `Podman runs as root` | Run `hexa` as your user; local runs need rootless Podman |
| `Permission denied` on a mounted folder | With SELinux, check that `--dry-run` shows `:z` on mounts. On macOS, keep the project under your home folder |
| The pull or the run is very slow on Apple Silicon | Enable Rosetta in Docker Desktop's settings |
| `Local run refused the remote write ...` | Expected: use a local copy, or `--allow-remote-writes` |
| `Dataset ... does not exist` | Check the slug, or download it with `hexa datasets download SLUG` |
| A file the pipeline reads is missing | Download it with `hexa files download PATH` |
