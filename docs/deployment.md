# Deploying the MCP server

The MCP server is a Python web service. It must be reachable over HTTPS from the
internet, since AI clients call it from their own servers.

## Running

Install it with the `server` extra:

```bash
uv tool install "openhexa-companion[server] @ git+https://github.com/yannforget/openhexa-companion"
```

Configure it with environment variables:

| Variable | Description |
| --- | --- |
| `COMPANION_PUBLIC_URL` | Public address of the server, such as `https://companion.example.org`, without a path |
| `COMPANION_OPENHEXA_URL` | The OpenHEXA instance it serves |
| `COMPANION_SECRET_KEY` | Random secret of at least 32 characters, encrypting stored tokens |
| `COMPANION_DATABASE` | Path of its SQLite database |

Then start it behind a reverse proxy that terminates TLS:

```bash
python -m openhexa.companion.server --host 127.0.0.1 --port 8000
```

Run a single instance, and keep the database on a persistent disk. Users connect with
`<public URL>/mcp`.

## Operating

- **Backups**: back up the database with `sqlite3 companion.db ".backup backup.db"`. Its
  tokens can only be decrypted with the secret key, so store the key separately.
- **Secret key**: changing it makes stored tokens unreadable. Delete the database and
  ask users to connect again.
- **Public URL**: changing it means users have to add the server again.

## Testing with Claude

To try a change with Claude, run the server locally and expose it through an HTTPS
tunnel, such as a Cloudflare Tunnel:

```bash
uv run --env-file dev.env python -m openhexa.companion.server --port 8000
```

Set `COMPANION_PUBLIC_URL` to the tunnel's URL, then add `<tunnel URL>/mcp` as a custom
MCP server in Claude. After a code change, restart the server: Claude stays connected as
long as the URL, secret key and database don't change.

Use demo workspaces only: the tunnel provider can see the tokens you enter.
