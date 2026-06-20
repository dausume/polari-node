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
#
# Re-runs the compose `up -d` for the chosen services with --build, then
# `restart prf-proxy`. Output is whatever docker compose prints; the
# script's own logging is the colored brackets.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.staging-nip.yml"
ENV_FILE="$SCRIPT_DIR/.generated/.env.staging"

# Colors
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; B='\033[0;34m'; NC='\033[0m'

if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo -e "${R}[error]${NC} compose file not found: $COMPOSE_FILE"
    exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
    echo -e "${R}[error]${NC} env file not found: $ENV_FILE"
    echo -e "        run ./staging-setup.sh first"
    exit 1
fi

# Default services to rebuild when no args are passed.
DEFAULT_SERVICES=(backend frontend)

PROXY_ONLY=false
ALL=false
FRESH_DATA=false
SERVICES=()
for arg in "$@"; do
    case "$arg" in
        --proxy-only)  PROXY_ONLY=true ;;
        --all)         ALL=true ;;
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

# --fresh-data: tear down + drop the backend object-DB volume so polari.db
# is recreated and ALL seeds re-run (with any new schema fields). Other
# volumes (MariaDB, MinIO) are left intact. Compose project name defaults to
# the directory name, so the volume is "<dir>_backend-data".
if $FRESH_DATA; then
    PROJECT="$(basename "$SCRIPT_DIR")"
    DATA_VOLUME="${PROJECT}_backend-data"
    echo -e "${Y}[fresh-data]${NC} wiping object DB volume ${DATA_VOLUME} — all data re-seeds on boot."
    sudo docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down
    sudo docker volume rm "$DATA_VOLUME" 2>/dev/null \
        || echo -e "${Y}[fresh-data]${NC} volume ${DATA_VOLUME} not present (already clean)."
fi

if ! $PROXY_ONLY; then
    if [[ ${#SERVICES[@]} -eq 0 ]]; then
        echo -e "${B}[rebuild]${NC} building ALL services (with --build)…"
        sudo docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
            up -d --build
    else
        echo -e "${B}[rebuild]${NC} building: ${SERVICES[*]}"
        sudo docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
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
sudo docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

echo -e "${B}[proxy]${NC} bouncing prf-proxy to re-resolve upstream IPs…"
sudo docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    restart prf-proxy

echo -e "${G}[done]${NC} stack is up."

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
