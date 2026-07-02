#!/usr/bin/env bash
# ==============================================================================
# rebuild-staging.sh — one-shot rebuild + restart for the staging stack
# ==============================================================================
#
# Rebuilds the requested services (defaults to backend + frontend) AND
# always restarts the proxy afterward. The proxy's nginx config resolves
# upstream container IPs at startup, so when backend or frontend get new
# IPs from a fresh build, the proxy needs a bounce or it 504s.
#
# USAGE
#   ./rebuild-staging.sh                       # build backend + frontend, bounce proxy
#   ./rebuild-staging.sh backend               # only backend, bounce proxy
#   ./rebuild-staging.sh frontend              # only frontend, bounce proxy
#   ./rebuild-staging.sh backend frontend keycloak   # arbitrary service list
#   ./rebuild-staging.sh --all                 # rebuild every service
#   ./rebuild-staging.sh --proxy-only          # skip rebuilds, just bounce proxy
#   ./rebuild-staging.sh --no-prune            # keep dangling images/build cache
#
# After a successful build the script prunes DANGLING images + build cache
# (on by default) so repeated rebuilds don't fill the disk. --no-prune skips it.
#
# Re-runs the compose `up -d` for the chosen services with --build, then
# `restart prf-proxy`. Output is whatever docker compose prints; the
# script's own logging is the colored brackets.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.staging-nip.yml"
ENV_FILE="$SCRIPT_DIR/.generated/.env.staging"

# Use plain docker when the daemon is reachable without sudo (user in the
# docker group — also what lets headless/agent shells run this script);
# fall back to sudo docker otherwise (needs a terminal for the password).
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi

# Colors
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; B='\033[0;34m'; NC='\033[0m'

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo -e "${R}[error]${NC} compose file not found: $COMPOSE_FILE"
    exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
    # First run on this box: bootstrap the env (IP auto-detect, nginx conf, KC
    # creds, frontend config) instead of bailing — staging-setup.sh is fully
    # non-interactive, so the main shell can run it. Override the IP with
    # OVERRIDE_IP=x.x.x.x if auto-detect picks the wrong interface.
    if [[ -f "$SCRIPT_DIR/staging-setup.sh" ]]; then
        echo -e "${Y}[bootstrap]${NC} no env yet — running staging-setup.sh to generate it…"
        bash "$SCRIPT_DIR/staging-setup.sh"
    fi
    if [[ ! -f "$ENV_FILE" ]]; then
        echo -e "${R}[error]${NC} env file still not found: $ENV_FILE"
        echo -e "        run ./staging-setup.sh manually (or set OVERRIDE_IP) and retry"
        exit 1
    fi
fi

# Default services to rebuild when no args are passed.
DEFAULT_SERVICES=(backend frontend)

PROXY_ONLY=false
ALL=false
FRESH_DATA=false
NO_PRUNE=false
SERVICES=()
for arg in "$@"; do
    case "$arg" in
        --proxy-only)  PROXY_ONLY=true ;;
        --all)         ALL=true ;;
        # Skip the post-build prune of dangling images + build cache. The
        # prune is ON by default because repeated `up -d --build` runs leave
        # the previous (now untagged) image behind every time and fill the
        # disk (we hit ENOSPC). Pass --no-prune to keep old layers around.
        --no-prune)    NO_PRUNE=true ;;
        # Wipe the backend object DB (SQLite in the backend-data volume) so
        # the next boot re-seeds EVERYTHING from scratch. Needed whenever a
        # *SimState / definition class gains new fields — seeding is
        # idempotent-by-name and won't backfill new columns onto existing
        # rows, so stale rows otherwise render with default (0) values.
        --fresh-data)  FRESH_DATA=true ;;
        -*)            echo -e "${R}[error]${NC} unknown flag: $arg"; exit 1 ;;
        *)             SERVICES+=("$arg") ;;
    esac
done

if $PROXY_ONLY; then
    SERVICES=()
elif $ALL; then
    SERVICES=()  # empty = up -d --build (all services)
elif [[ ${#SERVICES[@]} -eq 0 ]]; then
    SERVICES=("${DEFAULT_SERVICES[@]}")
fi

cd "$SCRIPT_DIR"

# LOCAL_IP drives the staging hostnames AND the cert SANs. Read it early so the
# cert step below and the access-URL block at the end both use it.
LOCAL_IP=$(grep -oP '^LOCAL_IP=\K.*' "$ENV_FILE" 2>/dev/null | tr -d '"' || true)

# ---- ensure staging TLS certs exist (step-ca) BEFORE building ----------------
# The proxy/keycloak Dockerfiles COPY these certs at build time, so they must be
# in place first. We detect missing / expiring / wrong-IP certs and (re)issue
# them via the rf-node's own CA toolkit (ca/setup-ca.sh), then wire them into the
# compose mount locations. Idempotent — a no-op when valid certs already match.
# So you never run the CA setup separately: the main staging shell handles it.
ensure_staging_certs() {
    local ca="$SCRIPT_DIR/ca"
    [[ -f "$ca/setup-ca.sh" ]] || { echo -e "${Y}[certs]${NC} no ca/ toolkit — skipping (using existing certs)"; return 0; }
    [[ -n "$LOCAL_IP" ]]       || { echo -e "${Y}[certs]${NC} LOCAL_IP unknown — skipping cert auto-setup"; return 0; }
    local base="${LOCAL_IP}.nip.io"
    local crt="$ca/issued/prf-proxy.crt"
    # (Re)issue when the proxy cert is absent, within 7 days of expiry, or its
    # SANs don't include this server's hostname (e.g. the IP changed).
    if [[ ! -f "$crt" ]] \
       || ! openssl x509 -in "$crt" -checkend 604800 >/dev/null 2>&1 \
       || ! openssl x509 -in "$crt" -noout -ext subjectAltName 2>/dev/null | grep -q "prf.${base}"; then
        echo -e "${B}[certs]${NC} issuing staging certs (step-ca) for ${base}…"
        BASE_DOMAIN="$base" DEPLOY_ENV=staging bash "$ca/setup-ca.sh" staging --non-interactive
    else
        echo -e "${G}[certs]${NC} staging certs present, valid, match ${base} (skip)"
    fi
    # Place issued certs where the proxy + keycloak Dockerfiles COPY them.
    install -D -m644 "$ca/issued/prf-proxy.crt" "$SCRIPT_DIR/prf-proxy/certs/prf-proxy.crt"
    install -D -m600 "$ca/issued/prf-proxy.key" "$SCRIPT_DIR/prf-proxy/certs/prf-proxy.key"
    install -D -m644 "$ca/root_ca.crt"          "$SCRIPT_DIR/prf-proxy/certs/ca/prf-ca.crt"
    install -D -m644 "$ca/issued/prf-kc.crt"    "$SCRIPT_DIR/prf-keycloak/certs/prf-kc.crt"
    install -D -m600 "$ca/issued/prf-kc.key"    "$SCRIPT_DIR/prf-keycloak/certs/prf-kc.key"
    echo -e "${G}[certs]${NC} wired certs into prf-proxy/ + prf-keycloak/"
}
ensure_staging_certs

# --fresh-data: tear down + drop the backend object-DB volume so polari.db
# is recreated and ALL seeds re-run (with any new schema fields). Other
# volumes (MariaDB, MinIO) are left intact. Compose project name defaults to
# the directory name, so the volume is "<dir>_backend-data".
if $FRESH_DATA; then
    PROJECT="$(basename "$SCRIPT_DIR")"
    DATA_VOLUME="${PROJECT}_backend-data"
    echo -e "${Y}[fresh-data]${NC} wiping object DB volume ${DATA_VOLUME} — all data re-seeds on boot."
    $DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down
    $DOCKER volume rm "$DATA_VOLUME" 2>/dev/null \
        || echo -e "${Y}[fresh-data]${NC} volume ${DATA_VOLUME} not present (already clean)."
fi

if ! $PROXY_ONLY; then
    if [[ ${#SERVICES[@]} -eq 0 ]]; then
        echo -e "${B}[rebuild]${NC} building ALL services (with --build)…"
        $DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
            up -d --build
    else
        echo -e "${B}[rebuild]${NC} building: ${SERVICES[*]}"
        $DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
            up -d --build "${SERVICES[@]}"
    fi
fi

# Cold-start safety: a targeted rebuild (e.g. `backend frontend`) only starts
# the named services plus their depends_on — it leaves prf-proxy and
# prf-keycloak down on a fresh stack, and the proxy bounce below would then
# have no container to restart. A full `up -d` (no --build) brings up anything
# not already running and is a no-op when the stack is already healthy. This is
# what makes the script work from both a cold start and a warm rebuild.
echo -e "${B}[stack]${NC} ensuring all services are up…"
$DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

echo -e "${B}[proxy]${NC} bouncing prf-proxy to re-resolve upstream IPs…"
$DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    restart prf-proxy

echo -e "${G}[done]${NC} stack is up."

# Reclaim disk from the rebuild. Every `up -d --build` retags the service
# image and leaves the PREVIOUS build dangling (untagged), and the buildkit
# cache grows unbounded — repeated rebuilds otherwise fill the disk (ENOSPC).
# We prune ONLY dangling images + build cache here, AFTER the stack is up, so
# the just-built images (now tagged and held by running containers) and all
# named volumes (backend-data, MariaDB, MinIO) are never touched.
if ! $NO_PRUNE; then
    echo -e "${B}[prune]${NC} reclaiming dangling images + build cache…"
    $DOCKER image prune -f   >/dev/null 2>&1 || true
    $DOCKER builder prune -f >/dev/null 2>&1 || true
fi

# Detect the active LOCAL_IP from the generated env file. Drives both
# the access-URL block and the sanity probe below.
LOCAL_IP=$(grep -oP '^LOCAL_IP=\K.*' "$ENV_FILE" 2>/dev/null | tr -d '"' || true)

if [[ -n "$LOCAL_IP" ]]; then
    echo ""
    echo "  - PRF Frontend:   https://prf.${LOCAL_IP}.nip.io"
    echo "  - PRF API:        https://api.prf.${LOCAL_IP}.nip.io"
    echo "  - Keycloak:       https://auth.prf.${LOCAL_IP}.nip.io"
    echo "  - MinIO Console:  https://files.prf.${LOCAL_IP}.nip.io"
    echo "  - MinIO S3 API:   https://s3.prf.${LOCAL_IP}.nip.io"
    echo ""
fi

# Quick sanity probe — fail loud if the proxy still can't reach the backend.
if command -v curl >/dev/null 2>&1 && [[ -n "$LOCAL_IP" ]]; then
    sleep 1
    code=$(curl -k -s -o /dev/null -w '%{http_code}' --max-time 5 \
                "https://api.prf.${LOCAL_IP}.nip.io/api/simulations/runs" || echo "000")
    if [[ "$code" == "200" ]]; then
        echo -e "${G}[probe]${NC} api OK ($code)"
    elif [[ "$code" == "000" ]]; then
        echo -e "${Y}[probe]${NC} api unreachable — backend may still be starting"
    else
        echo -e "${Y}[probe]${NC} api returned $code — check 'docker logs prf-backend'"
    fi
fi
