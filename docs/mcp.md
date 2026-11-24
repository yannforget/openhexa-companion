# MCP

The companion's remote MCP server lets AI chat clients such as Claude work with your
OpenHEXA workspaces. To add it to a client, see the [README](../README.md#mcp).

## Access

When you connect a client, the server opens a page where you enter a token for each
workspace the client may use. The client gets its own credentials for the server, never
your OpenHEXA tokens, and can do in each workspace what your token allows.

On the account page, at `/account`, you can choose which workspaces each client reaches,
disconnect a client, or forget a token. Forgetting a token doesn't revoke it in OpenHEXA:
if it may have leaked, revoke it there too.

A client that isn't used for 30 days must connect again.

Tools that change a workspace are marked as such, so clients can ask you before using
them. SQL queries are read-only and recorded in OpenHEXA's audit log under your name.

## Analysing dataset files

The server gives the client a temporary download link for a file in a given dataset
version, valid for a few minutes. The client downloads the complete file into its code
sandbox and analyses it there; the data doesn't go through the server.

The sandbox must be allowed to reach OpenHEXA's storage. For hosted OpenHEXA, add
`storage.googleapis.com` to the allowed websites of Claude's code sandbox, in its
settings. On Team and Enterprise plans, an organization admin does this. For other
instances, ask the administrator where dataset files are stored.

## Limits

- File analysis needs storage that the client's sandbox can reach. It doesn't work with
  instances that keep files on their own disk or in private storage.
- Each workspace needs its own token.
- One server serves one OpenHEXA instance.
- It can't publish datasets, upload files or deploy pipelines: use `hexa` for that.
- It replaces OpenHEXA's built-in MCP server; don't enable both in the same client.
