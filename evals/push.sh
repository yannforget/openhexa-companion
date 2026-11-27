#!/bin/sh
# Push workspace/ to an empty OpenHEXA workspace: ./push.sh ENV_FILE
# ENV_FILE holds OPENHEXA_URL, OPENHEXA_WORKSPACE and OPENHEXA_TOKEN.
set -eu
env_file=$(realpath "$1")
hexa() { command hexa --env-file "$env_file" "$@"; }
cd "$(dirname "$0")/workspace"

hexa workspace update --homepage homepage.md

for dir in datasets/*/; do
  name=$(head -n 1 "$dir/dataset.md" | sed 's/^# //')
  description=$(tail -n +3 "$dir/dataset.md")
  slug=$(hexa datasets create "$name" -d "$description")
  for version in "$dir"v*/; do
    [ -d "$version" ] || continue
    hexa datasets publish "$slug" "$version" --name "$(basename "$version")"
  done
done

for dir in pipelines/*/; do
  code=$(hexa pipelines create "$(basename "$dir" | tr _ -)" "$dir")
  hexa pipelines run "$code" --wait || echo "Run of $code failed, as expected for task 3"
done
