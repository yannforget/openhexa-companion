"""Run the companion service: `python -m openhexa.companion.server`.

Configuration comes from the `COMPANION_*` environment variables.
"""

import argparse
import sys

import uvicorn

from openhexa.companion.server.app import build_app
from openhexa.companion.server.config import Config, ConfigError
from openhexa.companion.server.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m openhexa.companion.server")
    parser.add_argument("--host", default="127.0.0.1", help="Interface to listen on.")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on.")
    args = parser.parse_args()
    try:
        config = Config.from_env()
    except ConfigError as err:
        sys.exit(f"Error: {err}")
    store = Store(config.database, config.fernet_key())
    try:
        uvicorn.run(build_app(config, store), host=args.host, port=args.port)
    finally:
        store.close()


if __name__ == "__main__":
    main()
