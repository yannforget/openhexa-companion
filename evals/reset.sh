#!/bin/sh
# Undo what eval sessions change in the test workspace: ./reset.sh ENV_FILE
# Deletes the versions of aggregate-population after v1, every other pipeline, and the
# versions of population-health-zones, then lists what is left.
set -eu
env_file=$(realpath "$1")
h() { hexa --env-file "$env_file" "$@"; }

h pipelines versions list aggregate-population --json | jq -r '.[] | select(.number > 1) | "v\(.number)"' |
  while read -r v; do h pipelines versions delete aggregate-population "$v" -y; done
h pipelines list --json | jq -r '.[] | select(.code != "aggregate-population") | .code' |
  while read -r p; do h pipelines delete "$p" -y; done
h datasets versions list population-health-zones --json 2> /dev/null | jq -r '.[].name' |
  while read -r v; do h datasets versions delete population-health-zones "$v" -y; done

h pipelines list
h datasets versions list population-health-zones
