# Local development

## Testing the connector with an AI client

Claude reaches connectors from Anthropic's servers, so the local server needs a public
HTTPS URL, such as a Cloudflare tunnel.

1. Ask the user for the public URL, the local port, and the env file with the
   `COMPANION_*` variables (see `server/config.py`). Never read or print its
   secret key.
2. The user starts the tunnel to that port; you can start the server:

   ```bash
   uv run --env-file <env file> python -m openhexa.companion.server --port <port>
   ```

3. Check through the public URL: `/.well-known/oauth-authorization-server` answers
   200, and `POST /mcp` answers 401 without a token.
4. The user adds `<public URL>/mcp` as a custom connector in Claude, enters workspace
   tokens on the connect page. For dataset file downloads, the user allows the storage
   host in Claude's code sandbox settings (`storage.googleapis.com` for the demo
   instance); it is not allowed by default.

Restart the server after code changes; the connector keeps working as long as the
public URL, secret key and database stay the same. Changing the public URL means
re-adding the connector. Use demo workspace tokens only: tunnels can decrypt traffic.
