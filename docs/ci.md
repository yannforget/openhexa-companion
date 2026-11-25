# Deploying pipelines from CI

`hexa pipelines publish` deploys the code in a directory as a new version of an existing
pipeline, so a CI job can deploy a pipeline whenever its code changes on `main`. The
examples use GitHub Actions.

## Before you start

**Create each pipeline once, by hand.** OpenHEXA derives the code of a pipeline from its
name, and adds a random suffix if the code is already taken, so CI should not create
pipelines: it would not know which code it got.

```console
$ hexa pipelines create "Standardize ECV" pipelines/standardize_ecv
Created pipeline version v1 of standardize-ecv
standardize-ecv
```

Use the printed code (`standardize-ecv`) in the workflow. If the pipeline does not exist,
`publish` fails and creates nothing.

**Add a workspace token as a secret.** Under **Settings → Secrets and variables →
Actions**, add `OPENHEXA_TOKEN`. The token must be allowed to deploy pipelines in the
workspace.

## What each version records

- **Name:** left out, so versions are numbered `v1`, `v2`, and so on.
- **Message (`-m`):** the commit message.
- **Link (`--link`):** the URL of the commit, shown by `hexa pipelines show` and in
  OpenHEXA.

The commit message goes through an environment variable and not into the script itself:
it is untrusted input, and `${{ ... }}` in a `run:` is pasted into the shell command.

## A repository with one pipeline

Deploy on every push to `main`, from the root of the repository (or from the directory
that holds `pipeline.py`).

```yaml
# .github/workflows/deploy.yml
name: Deploy pipeline

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv tool install git+https://github.com/yannforget/openhexa-companion@<tag-or-sha>
      - run: >
          hexa pipelines publish standardize-ecv .
          -m "$COMMIT_MESSAGE"
          --link "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/commit/$GITHUB_SHA"
        env:
          COMMIT_MESSAGE: ${{ github.event.head_commit.message }}
          OPENHEXA_URL: https://app.openhexa.org
          OPENHEXA_WORKSPACE: my-workspace
          OPENHEXA_TOKEN: ${{ secrets.OPENHEXA_TOKEN }}
```

Pin `<tag-or-sha>` so that a change to `hexa` cannot break your deployments.

## A repository with several pipelines

Use one workflow per pipeline, with a `paths` filter so that it runs only when that
pipeline's directory, or the workflow itself, changes.

```yaml
# .github/workflows/deploy-standardize-ecv.yml
name: Deploy standardize-ecv

on:
  push:
    branches: [main]
    paths:
      - pipelines/standardize_ecv/**
      - .github/workflows/deploy-standardize-ecv.yml
  workflow_dispatch: # Deploy by hand.

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv tool install git+https://github.com/yannforget/openhexa-companion@<tag-or-sha>
      - run: >
          hexa pipelines publish standardize-ecv pipelines/standardize_ecv
          -m "$COMMIT_MESSAGE"
          --link "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/commit/$GITHUB_SHA"
        env:
          COMMIT_MESSAGE: ${{ github.event.head_commit.message }}
          OPENHEXA_URL: https://app.openhexa.org
          OPENHEXA_WORKSPACE: my-workspace
          OPENHEXA_TOKEN: ${{ secrets.OPENHEXA_TOKEN }}
```

To add a pipeline, create it by hand, copy the file and change the code, the directory
and the name. A pipeline that depends on files outside its directory needs those paths
in `paths` as well.

## When a deployment fails

The step prints the reason from OpenHEXA, such as a missing pipeline, an invalid
`@pipeline` declaration or a token without permission. See the
[CLI reference](cli.md#hexa-pipelines) for the options.
