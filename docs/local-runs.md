# Local pipeline runs

`hexa local run` runs a pipeline from a local directory, in the same image as runs on
OpenHEXA, without deploying it. Datasets and connections are read from the workspace
unless you have a local copy; workspace files are always local copies. The run can't
change the workspace unless you allow it.

```console
$ hexa local pull datasets/population connections/dhis2-prod
$ hexa local run pipelines/import_data -p country=BFA
Image: docker.io/blsq/openhexa-blsq-environment:latest (default: the workspace sets none, ...)
Local: files, dataset population (v4), connection dhis2-prod
Remote writes: refused (--allow-remote-writes to allow)
...
Run succeeded in 21 s.
  Messages: 3 info, 1 warning
  Outputs: reports/summary.csv
  New local versions: population v5 (2 files)
```

When the pipeline works, deploy it with `hexa pipelines publish`.

## Requirements

| Platform | Container engine |
| --- | --- |
| Linux | Docker, or rootless Podman 4.3 or later |
| macOS | Docker Desktop, with Rosetta enabled on Apple Silicon |
| Windows | Docker in WSL 2, with the project in the WSL filesystem |

macOS with Podman and native Windows aren't supported. `hexa` uses Docker or Podman,
whichever answers first; choose one with `--engine` or
`HEXA_CONTAINER_ENGINE`. In a Distrobox or toolbox container, use the host's engine:

```bash
export HEXA_CONTAINER_ENGINE="distrobox-host-exec podman"
```

The pipeline runs in the workspace's image, or `blsq/openhexa-blsq-environment:latest`
if the workspace sets none, or the one given with `--image`. The first run pulls the
image, which is several GB.

## Local copies

Local copies live in `.hexa/workspace/`, in the project root, next to `.env`:

```
.hexa/workspace/
  files/                      # workspace files
  datasets/<slug>/<version>/  # dataset versions
  connections/<slug>.json     # connections, with their secrets
```

`hexa` keeps this folder out of Git. Fill it with `hexa local pull`:

```bash
hexa local pull files/reports                 # a folder of the workspace files
hexa local pull datasets/population           # the latest version
hexa local pull datasets/population@v3        # a given version
hexa local pull connections/dhis2-prod
```

You can also add, edit or remove copies by hand, for example to point a connection at a
test server. `hexa local status` shows what the folder holds, and `hexa local clean`
removes copies.

## What the pipeline sees

| | With a local copy | Without |
| --- | --- | --- |
| Workspace files | Read and written locally | Not available |
| Datasets | Read locally; new versions are written locally | Read from the workspace |
| Connections | Read locally | Read from the workspace |

`--ignore-local-datasets` and `--ignore-local-connections` read from the workspace even
when a copy exists.

Changes to the workspace, such as creating a dataset or a version of a remote dataset,
are refused unless you pass `--allow-remote-writes`. To publish a version the pipeline
created locally:

```bash
hexa datasets publish population .hexa/workspace/datasets/population/v5 --name v5
```

The workspace database isn't available to local runs.

Local runs prevent mistakes, but they don't sandbox the pipeline: reads from the
workspace are real, and connections reach real systems. A DHIS2 connection still writes
to that DHIS2.

## Troubleshooting

`hexa local run --dry-run` checks the setup and prints the container command without
running anything.

| Problem | Fix |
| --- | --- |
| `No container engine answers` | Check that `docker version` or `podman version` works, or pass `--engine` |
| `Podman runs as root` | Run `hexa` as your user: local runs need rootless Podman |
| `Permission denied` on a mounted folder | On macOS, keep the project under your home folder |
| Very slow on Apple Silicon | Enable Rosetta in Docker Desktop's settings |
| `Dataset ... does not exist` | Check the slug, or pull it with `hexa local pull datasets/SLUG` |
| A file the pipeline reads is missing | Pull it with `hexa local pull files/PATH` |
