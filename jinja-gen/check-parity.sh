#!/usr/bin/env bash
# jinja-gen/check-parity.sh — a rendered compose file may only replace a
# handwritten one when `docker compose config` agrees they mean the same
# stack. Run after: ansible-playbook jinja-gen/playbook.yml
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"

fail=0
check() {   # <handwritten> <generated> [extra compose args...]
    local hand="$1" gen="$2"; shift 2
    if [[ ! -f "jinja-build/$gen" ]]; then
        echo "MISSING jinja-build/$gen (render first)"; fail=1; return
    fi
    # Generated files are consumed from the repo root:
    #   docker compose --project-directory . -f jinja-build/<file> ...
    # so relative build contexts and default network names match.
    local a b
    a=$($DOCKER compose -f "$hand" "$@" config 2>/dev/null | grep -v '^name:')
    b=$($DOCKER compose --project-directory . -f "jinja-build/$gen" "$@" config 2>/dev/null | grep -v '^name:')
    if [[ -z "$a" || -z "$b" ]]; then
        echo "PARITY $hand: could not canonicalize (compose config failed)"; fail=1
    elif [[ "$a" == "$b" ]]; then
        echo "PARITY OK  $hand == jinja-build/$gen"
    else
        echo "PARITY DIFF $hand vs jinja-build/$gen:"
        diff <(echo "$a") <(echo "$b") | head -20
        fail=1
    fi
}

check docker-compose.msci-engines.yml docker-compose.msci-engines.yml
check docker-compose.remote-worker.yml docker-compose.remote-worker.yml --profile dask

exit $fail
