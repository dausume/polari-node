#!/usr/bin/env bash
# ==============================================================================
# twin-polari-build.sh — launch instance B beside the running staging stack
# and wire the peer handshake (Track 2 of DISTRIBUTED_COMPUTE_PLAN.md).
#
# USAGE
#   ./twin-polari-build.sh            # up: network, wiring, B, handshake
#   ./twin-polari-build.sh down       # stop instance B (A untouched)
#   ./twin-polari-build.sh status     # peers as A and B each see them
#
# WHAT IT DOES (idempotent; safe to re-run — and SHOULD be re-run after any
# rebuild of instance A, since docker update/network connect/token-file do
# not survive container re-creation):
#   1. Ensures the `polari-link` peer network exists.
#   2. Wires instance A (the running prf-backend) into the twin:
#        - joins it to polari-link,
#        - raises its memory to 1 GB (dask sizing; live, no recreation),
#        - drops the shared peer token into its data volume
#          (/app/data/peer_token — read as the env-var fallback).
#   3. Generates B's frontend runtime config (plain-HTTP ports).
#   4. Brings up instance B (project `polari-twin-b`) — same images, own
#      data volume, shared Keycloak/MariaDB/MinIO over A's network.
#   5. Links the pair via the PeerAgreement admission flow (B join-requests,
#      this script approves on A as the operator, B registers with its
#      per-child token); the shared token is only the deprecated fallback.
#
# Instance A stays fully intact: same URLs, same flow, same rebuild script.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_B="$SCRIPT_DIR/docker-compose.twin-b.yml"
ENV_FILE="$SCRIPT_DIR/.generated/.env.staging"
TOKEN_FILE="$SCRIPT_DIR/.generated/.polari-peer-token"
PROJECT_B="polari-twin-b"

G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; R='\033[0;31m'; NC='\033[0m'

if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi

LOCAL_IP=$(grep -oP '^LOCAL_IP=\K.*' "$ENV_FILE" 2>/dev/null | tr -d '"' || true)
[[ -n "$LOCAL_IP" ]] || { echo -e "${R}[error]${NC} LOCAL_IP not found — run ./staging-setup.sh first"; exit 1; }

A_API="https://api.prf.${LOCAL_IP}.nip.io"
B_API="http://${LOCAL_IP}:8081"

cmd="${1:-up}"

if [[ "$cmd" == "down" ]]; then
    echo -e "${B}[twin]${NC} stopping instance B (instance A untouched)…"
    POLARI_PEER_TOKEN=unused $DOCKER compose -p "$PROJECT_B" -f "$COMPOSE_B" down
    echo -e "${G}[twin]${NC} instance B down."
    exit 0
fi

if [[ "$cmd" == "status" ]]; then
    echo -e "${B}[twin]${NC} peers as instance A sees them:"
    curl -k -s "$A_API/api/peers" | python3 -m json.tool || true
    echo -e "${B}[twin]${NC} peers as instance B sees them:"
    curl -s "$B_API/api/peers" | python3 -m json.tool || true
    exit 0
fi

# ---- dask stack: one scheduler (A-side) + one worker per instance side ----
COMPOSE_DASK="$SCRIPT_DIR/docker-compose.dask.yml"
PROJECT_DASK="polari-dask"

if [[ "$cmd" == "dask-up" ]]; then
    $DOCKER network inspect polari-link >/dev/null 2>&1 \
        || { echo -e "${R}[error]${NC} polari-link missing — run ./twin-polari-build.sh first"; exit 1; }
    echo -e "${B}[dask]${NC} starting scheduler + a-worker + b-worker on polari-link…"
    $DOCKER compose -p "$PROJECT_DASK" -f "$COMPOSE_DASK" up -d
    echo -e "${G}[dask]${NC} up. Point searches at it with:"
    echo '    {"executionBackend": "dask", "daskScheduler": "tcp://prf-dask-scheduler:8786"}'
    exit 0
fi

if [[ "$cmd" == "dask-down" ]]; then
    $DOCKER compose -p "$PROJECT_DASK" -f "$COMPOSE_DASK" down
    echo -e "${G}[dask]${NC} dask stack down."
    exit 0
fi

if [[ "$cmd" == "dask-status" ]]; then
    $DOCKER ps --filter "name=prf-dask-scheduler" --filter "name=prf-a-dask-worker" \
        --filter "name=prf-b-dask-worker" --format '{{.Names}}\t{{.Status}}'
    exit 0
fi

# ---- 1. peer network -----------------------------------------------------
$DOCKER network inspect polari-link >/dev/null 2>&1 \
    || $DOCKER network create polari-link
echo -e "${G}[twin]${NC} polari-link network ready."

# ---- shared token (stable across re-runs) ---------------------------------
if [[ ! -f "$TOKEN_FILE" ]]; then
    head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$TOKEN_FILE"
fi
TOKEN="$(cat "$TOKEN_FILE")"

# ---- 2. wire instance A ----------------------------------------------------
if $DOCKER ps --format '{{.Names}}' | grep -q '^prf-backend$'; then
    $DOCKER network connect polari-link prf-backend 2>/dev/null \
        && echo -e "${G}[twin]${NC} instance A joined polari-link." \
        || echo -e "${G}[twin]${NC} instance A already on polari-link."
    $DOCKER update --memory 1g --memory-swap 1g prf-backend >/dev/null
    echo -e "${G}[twin]${NC} instance A backend memory raised to 1 GB (dask sizing)."
    $DOCKER exec prf-backend sh -c "printf %s '$TOKEN' > /app/data/peer_token"
    echo -e "${G}[twin]${NC} peer token dropped into instance A."
else
    echo -e "${R}[error]${NC} prf-backend is not running — start instance A first (./rebuild-staging.sh)"; exit 1
fi

# ---- 3. B's frontend runtime config (plain-HTTP ports) --------------------
python3 - "$SCRIPT_DIR" "$LOCAL_IP" <<'EOF'
import json, sys
d, ip = sys.argv[1], sys.argv[2]
src = json.load(open(f'{d}/.generated/prf-runtime-config.json'))
cfg = dict(src)
cfg['_comment'] = 'TWIN INSTANCE B: generated by twin-polari-build.sh (plain HTTP ports)'
cfg['backend'] = {
    'http':  {'protocol': 'http', 'url': ip, 'port': '8081'},
    'https': {'protocol': 'http', 'url': ip, 'port': '8081'},
    'ws':    {'protocol': 'ws',   'url': ip, 'port': '8083'},
}
feats = dict(cfg.get('features') or {})
feats['enableHttps'] = False
cfg['features'] = feats
json.dump(cfg, open(f'{d}/.generated/prf-b-runtime-config.json', 'w'), indent=1)
print('runtime config for B written')
EOF

# ---- 4. instance B ----------------------------------------------------------
echo -e "${B}[twin]${NC} bringing up instance B…"
POLARI_PEER_TOKEN="$TOKEN" $DOCKER compose -p "$PROJECT_B" -f "$COMPOSE_B" up -d

echo -e "${B}[twin]${NC} waiting for instance B to be healthy (first boot seeds everything)…"
for i in $(seq 1 60); do
    state=$($DOCKER inspect -f '{{.State.Health.Status}}' prf-b-backend 2>/dev/null || echo starting)
    [[ "$state" == "healthy" ]] && break
    sleep 5
done
state=$($DOCKER inspect -f '{{.State.Health.Status}}' prf-b-backend 2>/dev/null || echo missing)
if [[ "$state" != "healthy" ]]; then
    echo -e "${R}[error]${NC} instance B not healthy after 5 min (state: $state) — check: $DOCKER logs prf-b-backend"; exit 1
fi
echo -e "${G}[twin]${NC} instance B healthy."

# ---- 5. peer handshake: the PeerAgreement admission flow --------------------
# (Mesh convergence ruling: explicit bilateral agreement. B sends a join
# request to A; this script is the operator flipping A's approve knob; B
# then polls, receives its per-child token, and registers with it. The
# shared token above remains only as the DEPRECATED fallback knob.)
AUTOCONF_BODY='{"myBaseUrl": "http://prf-b-backend:3000", "parentUrl": "http://prf-backend:3000"}'
JOIN1=$(curl -s -X POST "$B_API/api/peers/autoconfig" \
             -H 'Content-Type: application/json' -d "$AUTOCONF_BODY")
AID=$(printf %s "$JOIN1" | python3 -c \
    'import json,sys; d=json.load(sys.stdin).get("data",{}); print(d.get("join",{}).get("agreementId",""))' \
    2>/dev/null || true)
JOINED1=$(printf %s "$JOIN1" | python3 -c \
    'import json,sys; print(json.load(sys.stdin).get("data",{}).get("joined",""))' \
    2>/dev/null || true)
if [[ "$JOINED1" == "True" ]]; then
    echo -e "${G}[twin]${NC} B already joined A (approved agreement reused)."
elif [[ -n "$AID" ]]; then
    echo -e "${B}[twin]${NC} B's join request is agreement $AID — approving on A (operator knob)…"
    curl -k -s -X POST "$A_API/api/peers/agreements/$AID/approve" \
         -H 'Content-Type: application/json' \
         -d '{"approvedBy": "twin-polari-build.sh"}' >/dev/null
    JOIN2=$(curl -s -X POST "$B_API/api/peers/autoconfig" \
                 -H 'Content-Type: application/json' -d "$AUTOCONF_BODY")
    JOINED2=$(printf %s "$JOIN2" | python3 -c \
        'import json,sys; print(json.load(sys.stdin).get("data",{}).get("joined",""))' \
        2>/dev/null || true)
    if [[ "$JOINED2" == "True" ]]; then
        echo -e "${G}[twin]${NC} B joined A via PeerAgreement (per-child token; both peer rows set)."
    else
        echo -e "${R}[twin]${NC} agreement join did not complete — falling back to the deprecated shared token."
        curl -s -X POST "$B_API/api/peers/register" -H 'Content-Type: application/json' \
             -d "{\"name\": \"a\", \"baseUrl\": \"http://prf-backend:3000\", \"token\": \"$TOKEN\"}" >/dev/null
        curl -k -s -X POST "$A_API/api/peers/register" -H 'Content-Type: application/json' \
             -d "{\"name\": \"b\", \"baseUrl\": \"http://prf-b-backend:3000\", \"token\": \"$TOKEN\"}" >/dev/null
    fi
else
    echo -e "${R}[twin]${NC} autoconfig unavailable (old image?) — using the deprecated shared-token handshake."
    curl -s -X POST "$B_API/api/peers/register" -H 'Content-Type: application/json' \
         -d "{\"name\": \"a\", \"baseUrl\": \"http://prf-backend:3000\", \"token\": \"$TOKEN\"}" >/dev/null \
        && echo -e "${G}[twin]${NC} registered A as a peer of B."
    curl -k -s -X POST "$A_API/api/peers/register" -H 'Content-Type: application/json' \
         -d "{\"name\": \"b\", \"baseUrl\": \"http://prf-b-backend:3000\", \"token\": \"$TOKEN\"}" >/dev/null \
        && echo -e "${G}[twin]${NC} registered B as a peer of A."
fi

echo ""
echo -e "${G}[twin done]${NC} two Polari instances are linked."
echo "  - Instance A: https://prf.${LOCAL_IP}.nip.io   (api: $A_API)"
echo "  - Instance B: http://${LOCAL_IP}:8082          (api: $B_API)"
echo "  - Peers:      ./twin-polari-build.sh status"
echo "  - Stop B:     ./twin-polari-build.sh down"
