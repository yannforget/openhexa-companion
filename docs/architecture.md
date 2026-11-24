# OpenHEXA Companion

OpenHEXA Companion is a Python library for working with OpenHEXA workspaces, with two interfaces built on it: a command-line tool for people and coding agents working locally, and a remote Model Context Protocol (MCP) connector for people using AI clients such as Claude Web or Desktop.

It replaces two existing tools: the `hexa` CLI from `openhexa-dev-env`, whose commands it is free to redesign, and, for users who enable the connector, the native OpenHEXA MCP. Users are not expected to enable both connectors at once.

The companion requires no changes to OpenHEXA. It can do what the OpenHEXA GraphQL API allows a workspace token to do, and nothing more.

## Naming

| Surface | Name |
| --- | --- |
| Project and connector display name | OpenHEXA Companion |
| GitHub repository and Python distribution | `openhexa-companion` |
| Python import namespace | `openhexa.companion` (alongside `openhexa.sdk`) |
| CLI command | `hexa` |

## Use cases

### Analysing complete datasets from an AI client

This is the use case that motivates the connector. A user should be able to ask “Which district had the highest total last year?” and get an answer computed from a specific dataset version, informed by what the workspace documents about the data, such as its description or homepage. Browsing metadata or showing sample rows helps with discovery, but does not meet this need.

### Writing and deploying pipelines

A person or a coding agent writes a pipeline in a local project linked to a workspace, runs it locally against real workspace data, then deploys it to OpenHEXA.

### Investigating runs

A user finds out what a pipeline run did and why it failed: its parameters, logs, outputs and the code version that ran.

### Publishing webapps

A user publishes or updates a static webapp in a workspace.

### Interfaces per use case

Each use case will be supported through the CLI, the MCP connector or both, whichever is simpler. This will be decided later.

## Architecture

| Component | Responsibility |
| --- | --- |
| **Python library** | A workspace-scoped client usable directly from Python. Given explicit credentials, it performs workspace operations, such as reading a file from a dataset version. It does not decide how credentials are collected, how results are displayed or where the bytes go. |
| **CLI** | The local interface: loads configuration, presents results, works with files on disk and runs pipelines locally. It calls the library directly and works without the companion service. |
| **MCP connector** | The hosted interface for AI clients. It calls the same library directly, not through a shell, and is deployed separately. |

The project starts as one Python distribution, with the service’s dependencies optional.

### Workspace access

A user selects **Connect** in the AI client and enters an OpenHEXA token on the companion’s authorization page, never in chat. The companion verifies access, stores the token encrypted and binds it to a revocable grant. Through OAuth, the AI client receives a credential for the companion, not for OpenHEXA. What the user can do through the connector is what their OpenHEXA token allows.

Ideally one grant holds several workspaces, each with its own token. One connector per workspace is an acceptable simplification.

A workspace token proves access to a workspace, not an individual OpenHEXA identity.

### Full files without hosted compute

When the assistant selects a dataset file, the companion returns the signed download URL that OpenHEXA issues for that file in that exact version. The AI client downloads it from storage directly, so file bytes never pass through the companion. The URL is a temporary bearer credential that expires within minutes (10 on GCS); it never contains the OpenHEXA token.

The AI client’s own Python environment downloads and analyses the file. A follow-up question can reuse the same file, and the answer can cite the dataset version and path. The companion runs no analysis itself.

The CLI shares the same library operation, but saves the bytes to disk.

**Main risk:** the AI client’s execution environment must reach the storage host, such as `storage.googleapis.com` on hosted OpenHEXA. Claude’s sandbox blocks it by default: users, or Team and Enterprise admins, must allow it. Storage that only OpenHEXA can reach, such as filesystem storage or a private MinIO, is not supported.

## Left for later

The Python interface, the CLI commands and the MCP tool catalogue will be designed in separate documents.
