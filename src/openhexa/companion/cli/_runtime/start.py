"""Start a local pipeline run inside the pipeline image.

`hexa run` runs `python /hexa/runtime/start.py` in the container and writes the run
settings, including the real token, as one JSON line on stdin. This script starts the
local OpenHEXA API on the container's loopback, runs the image's entrypoint with
`pipeline run`, writes `/hexa/run/summary.json` and exits with the pipeline's code.
"""

import base64
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from server import LocalServer  # pyrefly: ignore[missing-import]

# Folder with the local `datasets/` and `connections/`.
LOCAL_WORKSPACE = Path("/hexa/workspace")
# Folder of the workspace files, as in hosted runs.
FILES = Path("/home/jovyan/workspace")
SUMMARY = Path("/hexa/run/summary.json")
ENTRYPOINT = "/usr/local/bin/entrypoint.sh"


def main() -> int:
    settings = json.loads(sys.stdin.readline())
    sys.stdin.close()
    server = LocalServer(settings, LOCAL_WORKSPACE, FILES)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HEXA_", "WORKSPACE_"))
    }
    environment.update(
        {
            "HEXA_ENVIRONMENT": "LOCAL_PIPELINE",
            "HEXA_SERVER_URL": server.url,
            "HEXA_TOKEN": server.run_token,
            "HEXA_WORKSPACE": settings["workspace"],
            "HEXA_RUN_ID": settings["run_id"],
            "HEXA_PIPELINE_CODE": settings["pipeline_code"],
            "HEXA_PIPELINE_NAME": settings["pipeline_code"],
            "HEXA_PIPELINE_TYPE": "zipFile",
            # `current_run.add_file_output` builds an output URI from it.
            "WORKSPACE_BUCKET_NAME": "local",
            "PYTHONUNBUFFERED": "1",
        }
    )
    config = base64.b64encode(json.dumps(settings["parameters"]).encode()).decode()
    started = time.monotonic()
    # A session of its own, so that a signal sent to the group reaches the pipeline's
    # process: the entrypoint's shell does not forward signals.
    child = subprocess.Popen(
        [ENTRYPOINT, "pipeline", "run", f"--config={config}"],
        cwd="/home/jovyan",
        env=environment,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    def forward(signum: int, _frame: object) -> None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)
    code = child.wait()
    code = code if code >= 0 else 128 - code
    summary = {
        **server.router.summary,
        "exit_code": code,
        "duration_seconds": round(time.monotonic() - started, 1),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    server.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
