# MCP

The companion's remote MCP server lets AI chat clients such as Claude work with your
OpenHEXA workspaces. To add it to a client, see the [README](../README.md#mcp).

## Access

When you connect a client, the server opens a page where you enter a token for each
workspace the client may use. The client gets its own credentials for the server, never
your OpenHEXA tokens, and can do in each workspace what your token allows.

The account page, at `/account`, shows how to connect a client. There you can also choose
which workspaces each client reaches, disconnect a client, or forget a token. Forgetting a
token doesn't revoke it in OpenHEXA: if it may have leaked, revoke it there too.

The server's operator can access the tokens it stores. Use a server run by someone you
trust, or run your own.

A client that isn't used for 30 days must connect again.

Tools that change a workspace are marked as such, so clients can ask you before using
them. SQL queries are read-only and recorded in OpenHEXA's audit log under your name.

## Analysing dataset files

The server gives the client temporary links to the files of a dataset version, valid
for 10 minutes to 1 hour depending on the storage. The client reads the files from its
code sandbox, either whole or only the parts it needs, such as columns of a Parquet
file, and analyses them there. The files don't go through the server, except the
README of a version, which the server returns with the dataset.

The sandbox must be allowed to reach OpenHEXA's storage. For hosted OpenHEXA, add
`storage.googleapis.com` to the allowed websites of Claude's code sandbox, in its
settings. On Team and Enterprise plans, an organization admin does this. For other
instances, ask the administrator where dataset files are stored.

To query Parquet files in place with DuckDB, the sandbox also needs DuckDB's `httpfs`
extension, which DuckDB downloads from `extensions.duckdb.org` the first time it is used:
allow that host too. Polars reads the files without any extension.

## Limits

- File analysis needs storage that the client's sandbox can reach. It doesn't work with
  instances that keep files on their own disk or in private storage.
- Each workspace needs its own token.
- One server serves one OpenHEXA instance.
- It can't publish datasets, upload files or deploy pipelines: use `hexa` for that.
- It replaces OpenHEXA's built-in MCP server; don't enable both in the same client.
