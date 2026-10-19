#!/usr/bin/env bash
# build.sh - sim-2: build prf-bridge-engines:trixie ON the engines host (default: the ssh alias isle-core; BRIDGE_BUILD_HOST overrides).
# Writes warm/ from the framework's pom template (python only, nothing built here), ships this directory over ssh and runs `docker build` there.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
host="${BRIDGE_BUILD_HOST:-isle-core}"
modules="${FRAMEWORK_MODULES:-$here/../polari-framework/modules}"
rm -rf "$here/warm"
python3 "$here/warm_project.py" "$modules" "$here/warm"
trap 'rm -rf "$here/warm"' EXIT
tar -C "$here" --exclude=build.sh --exclude=__pycache__ -cf - . | ssh "$host" 'docker build -t prf-bridge-engines:trixie -'
ssh "$host" "docker image inspect prf-bridge-engines:trixie --format 'image {{.Id}} {{.Size}} bytes'"
