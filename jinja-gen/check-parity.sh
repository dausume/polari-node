#!/usr/bin/env bash
# jinja-gen/check-parity.sh — a rendered compose file may only replace a
# handwritten one when `docker compose config` agrees they mean the same
# stack. Run after: ansible-playbook jinja-gen/playbook.yml
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"

# Compose-time interpolation vars — both sides must canonicalize with the
# SAME values (`docker compose config` resolves ${...} from the shell).
export LOCAL_IP="${LOCAL_IP:-192.168.0.210}"                       # staging backend MSCI_ENGINES_URL
export POLARI_PEER_TOKEN="${POLARI_PEER_TOKEN:-parity-check-token}" # twin-b required var

# docker-compose.prod.yml lists .generated/.env.prod as env_file; on a
# machine where prod-setup.sh never ran, `config` fails on the missing
# file. Give BOTH sides the same empty placeholder, removed afterwards.
placeholders=()
for f in .generated/.env.prod; do
    if [[ ! -f "$f" ]]; then touch "$f" && placeholders+=("$f"); fi
done
cleanup() { ((${#placeholders[@]})) && rm -f "${placeholders[@]}"; }
trap cleanup EXIT

fail=0
canon_hand() {  # canonicalize handwritten file(s): canon_hand <file>... -- [args]
    local files=()
    while [[ "$1" != "--" ]]; do files+=(-f "$1"); shift; done; shift
    $DOCKER compose "${files[@]}" "$@" config 2>/dev/null | grep -v '^name:'
}
canon_gen() {   # canonicalize with the LAST file swapped for jinja-build/<file>
    local files=()
    while [[ "$1" != "--" ]]; do files+=("$1"); shift; done; shift
    local last=$((${#files[@]} - 1)) args=()
    files[$last]="jinja-build/${files[$last]}"
    local f; for f in "${files[@]}"; do args+=(-f "$f"); done
    # Generated files are consumed from the repo root:
    #   docker compose --project-directory . -f jinja-build/<file> ...
    # so relative paths and default network names match the handwritten run.
    $DOCKER compose --project-directory . "${args[@]}" "$@" config 2>/dev/null | grep -v '^name:'
}
check() {   # <compose-file>... [extra compose args...]  — last file is the
            # one under test; leading files are base layers (overlays).
    local files=() label
    while (($#)) && [[ "$1" == *.yml ]]; do files+=("$1"); shift; done
    label="${files[*]}"
    local gen="${files[-1]}"
    if [[ ! -f "jinja-build/$gen" ]]; then
        echo "MISSING jinja-build/$gen (render first)"; fail=1; return
    fi
    local a b
    a=$(canon_hand "${files[@]}" -- "$@")
    b=$(canon_gen "${files[@]}" -- "$@")
    if [[ -z "$a" || -z "$b" ]]; then
        echo "PARITY $label: could not canonicalize (compose config failed)"; fail=1
    elif [[ "$a" == "$b" ]]; then
        echo "PARITY OK  $label == jinja-build/$gen"
    else
        echo "PARITY DIFF $label vs jinja-build/$gen:"
        diff <(echo "$a") <(echo "$b") | head -20
        fail=1
    fi
}

# --- the full compose family --------------------------------------------
check docker-compose.yml
check docker-compose.stateless.yml
check docker-compose.staging-nip.yml
check docker-compose.prod.yml
check docker-compose.fullstack-test.yml
check docker-compose.twin-b.yml
check docker-compose.dask.yml
# dbcombo is an OVERLAY on twin-b — it only canonicalizes combined, so
# compare handwritten(twin-b + dbcombo) vs handwritten(twin-b) + generated.
check docker-compose.twin-b.yml docker-compose.dbcombo.yml
check docker-compose.msci-engines.yml
check docker-compose.remote-worker.yml --profile dask

exit $fail
