#!/bin/sh
# Start a pi session for an eval in a container: evals/pi.sh TASK CONFIG [PI OPTIONS]
# For example: evals/pi.sh 1 cli-skill --model openai/gpt-6.2-sol
# The session is saved in evals/sessions/. Run `podman build` first: see README.md.
set -eu
[ $# -ge 2 ] || { sed -n 2,4p "$0"; exit 2; }
task=$1 config=$2
shift 2
evals=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$evals")
model=$(printf '%s\n' "$@" | sed -n '/^--model$/{n;p;q}')
model=${model:-$(jq -r '.defaultProvider + "/" + .defaultModel' ~/.pi/agent/settings.json)}
provider=${model%%/*}
server=
case $config in
  cli-skill) ;;
  mcp-skill) server=companion ;;
  official) server=official ;;
  *)
    echo "Unknown configuration '$config': cli-skill, mcp-skill or official" >&2
    exit 2
    ;;
esac
agent=$(mktemp -d)
work=
trap 'rm -rf "$agent" ${work:+"$work"}' EXIT

# Logins last an hour and renewing one replaces its refresh token, so containers must not
# renew them: parallel sessions would invalidate each other. Renew here, under a lock,
# any login that would expire within 30 minutes, then give the container a copy.
soon=$(($(date +%s) * 1000 + 30 * 60 * 1000))
exec 9> /tmp/pi-evals.lock
flock 9
if jq -e --arg p "$provider" --argjson soon "$soon" '.[$p].expires // 1e15 < $soon' \
  ~/.pi/agent/auth.json > /dev/null; then
  pi auth print-bearer-token --provider "$provider" > /dev/null
fi
jq --arg p "$provider" '{($p): .[$p]}' ~/.pi/agent/auth.json > "$agent/auth.json"
if [ -n "$server" ]; then
  cp "$evals/mcp/$server.json" "$agent/mcp.json"
  key="mcp__$server|$(jq -r ".mcpServers.$server.url" "$agent/mcp.json")"
  jq --arg k "$key" '{($k): .[$k]}' ~/.pi/agent/mcp-auth.json > "$agent/mcp-auth.json"
  if jq -e --arg k "$key" --argjson soon "$soon" '.[$k].tokensExpireAt < $soon' \
    "$agent/mcp-auth.json" > /dev/null; then
    # pi renews an expired login when it connects; keep the result.
    jq --arg k "$key" '.[$k].tokensExpireAt = 0' "$agent/mcp-auth.json" > "$agent/expired.json"
    mv "$agent/expired.json" "$agent/mcp-auth.json"
    (cd "$agent" && PI_CODING_AGENT_DIR="$agent" pi mcp list > /dev/null)
    jq -s '.[0] * .[1]' ~/.pi/agent/mcp-auth.json "$agent/mcp-auth.json" > "$agent/merged.json"
    cat "$agent/merged.json" > ~/.pi/agent/mcp-auth.json
  fi
fi
flock -u 9

mkdir -p "$evals/sessions"
set -- -v "$agent:/root/.pi/agent" -v "$evals/sessions:/sessions" \
  -v "$repo/skills:/skills:ro" companion-evals pi \
  --no-skills --no-extensions --no-context-files --session-dir /sessions \
  --name "$task $config" --append-system-prompt "The current directory is a uv project \
with Python 3.13, polars, pandas, pyarrow and duckdb installed. Run Python with \`uv run\` \
and add packages with \`uv add\`." "$@"
case $config in
  cli-skill)
    # `hexa local run` starts the pipeline image with this machine's Podman, which mounts
    # folders by their path here: work in a folder of this machine, at the same path.
    work=$(mktemp -d /tmp/pi-evals-work.XXXXXX)
    cp "$evals/env/pyproject.toml" "$evals/env/uv.lock" "$work/"
    cp "$repo/.env.eval" "$work/.env"
    socket=$(podman info --format '{{.Host.RemoteSocket.Path}}')
    set -- -v "$work:$work" -w "$work" -e UV_PROJECT_ENVIRONMENT=/work/.venv \
      -v "$socket:/run/podman.sock" -e CONTAINER_HOST=unix:///run/podman.sock \
      -e HEXA_CONTAINER_ENGINE=podman-remote -e XDG_CACHE_HOME="$work/.cache" \
      -e UV_CACHE_DIR=/root/.cache/uv "$@" --no-mcp --skill /skills/openhexa-cli
    ;;
  mcp-skill) set -- "$@" -e builtin:mcp --skill /skills/openhexa-mcp ;;
  official) set -- "$@" -e builtin:mcp ;;
esac

podman run --rm -it --network host --security-opt label=disable "$@"
