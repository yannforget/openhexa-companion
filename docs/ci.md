# Deploying from CI

`hexa pipelines publish` and `hexa webapps publish` deploy a directory as a new version of
an existing pipeline or webapp, so a CI job can deploy whenever the code changes on
`main`. The examples use GitHub Actions. They cover [pipelines](#pipelines) first, then
[webapps](#webapps).

## Pipelines

### Before you start

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
Actions**, add `OPENHEXA_TOKEN`. The token must be allowed to deploy pipelines (or
webapps) in the workspace.

### What each version records

- **Name:** left out, so versions are numbered `v1`, `v2`, and so on.
- **Message (`-m`):** the commit message.
- **Link (`--link`):** the URL of the commit, shown by `hexa pipelines show` and in
  OpenHEXA.

The commit message goes through an environment variable and not into the script itself:
it is untrusted input, and `${{ ... }}` in a `run:` is pasted into the shell command.

### A repository with one pipeline

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

### A repository with several pipelines

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

## Webapps

A webapp deploys the same way, with `hexa webapps publish`. Differences from pipelines:

- **Create the webapp once, by hand**, as for a pipeline: `hexa webapps create "District
  dashboard" dist/` prints the slug to use in the workflow. Its scopes and visibility are
  set then, or with `hexa webapps update`; `publish` never changes them.
- **The new version is live at once**, and it replaces the files of the previous one, so
  publish only the build output.
- **There is no `--link`.** A version only has a message, so the message holds the commit
  message, then the URL of the commit.
- **Build first.** A webapp is usually a build output, so the workflow builds it and
  `paths` lists the source, not the output.

```yaml
# .github/workflows/deploy-district-dashboard.yml
name: Deploy district-dashboard

on:
  push:
    branches: [main]
    paths:
      - webapps/district_dashboard/**
      - .github/workflows/deploy-district-dashboard.yml
  workflow_dispatch: # Deploy by hand.

jobs:
  deploy:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: webapps/district_dashboard
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: webapps/district_dashboard/package-lock.json
      - run: npm ci && npm run build
      - uses: astral-sh/setup-uv@v5
      - run: uv tool install git+https://github.com/yannforget/openhexa-companion@<tag-or-sha>
      - run: |
          hexa webapps publish district-dashboard dist \
            -m "$COMMIT_MESSAGE"$'\n\n'"$COMMIT_URL"
        env:
          COMMIT_MESSAGE: ${{ github.event.head_commit.message }}
          COMMIT_URL: ${{ github.server_url }}/${{ github.repository }}/commit/${{ github.sha }}
          OPENHEXA_URL: https://app.openhexa.org
          OPENHEXA_WORKSPACE: my-workspace
          OPENHEXA_TOKEN: ${{ secrets.OPENHEXA_TOKEN }}
```

Use your project's package manager and its matching setup step, with a frozen lockfile:
`pnpm install --frozen-lockfile`, `yarn install --immutable` or `bun install
--frozen-lockfile`. For a site without a build step, publish its directory as is and drop
the Node steps. To deploy several webapps, use one workflow per webapp, as for pipelines.

### Several webapps in one repository, with shared code

When several webapps share a root lockfile and packages (here as a pnpm workspace, a
monorepo layout), install at the root and build only the webapp. The `paths` must also
list the shared code and the lockfile, or a change to them would not redeploy the webapps
that use them.

```yaml
# .github/workflows/deploy-district-dashboard.yml
name: Deploy district-dashboard

on:
  push:
    branches: [main]
    paths:
      - webapps/district_dashboard/**
      - packages/**
      - pnpm-lock.yaml
      - .github/workflows/deploy-district-dashboard.yml
  workflow_dispatch: # Deploy by hand.

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - run: pnpm --filter district-dashboard build
      - uses: astral-sh/setup-uv@v5
      - run: uv tool install git+https://github.com/yannforget/openhexa-companion@<tag-or-sha>
      - run: |
          hexa webapps publish district-dashboard webapps/district_dashboard/dist \
            -m "$COMMIT_MESSAGE"$'\n\n'"$COMMIT_URL"
        env:
          COMMIT_MESSAGE: ${{ github.event.head_commit.message }}
          COMMIT_URL: ${{ github.server_url }}/${{ github.repository }}/commit/${{ github.sha }}
          OPENHEXA_URL: https://app.openhexa.org
          OPENHEXA_WORKSPACE: my-workspace
          OPENHEXA_TOKEN: ${{ secrets.OPENHEXA_TOKEN }}
```

`--filter` takes the `name` in the webapp's `package.json`.

## When a deployment fails

The step prints the reason from OpenHEXA, such as a missing pipeline or webapp, an invalid
`@pipeline` declaration, a missing `index.html` or a token without permission. See the
[CLI reference](cli.md) for the options.
