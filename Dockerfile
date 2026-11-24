# The MCP server. Configure it with the COMPANION_* environment variables.
FROM ghcr.io/astral-sh/uv:0.13-python3.15-trixie-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1
WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --extra server --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --locked --extra server

EXPOSE 8000
CMD ["/app/.venv/bin/python", "-m", "openhexa.companion.server", "--host", "0.0.0.0", "--port", "8000"]
