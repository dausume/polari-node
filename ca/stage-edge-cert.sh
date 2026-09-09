#!/usr/bin/env bash
# stage-edge-cert.sh <certbot live dir> — copy a Let's Encrypt fullchain/privkey
# into <suite>/.generated/certs/edge (the pair docker-compose.prod.yml mounts
# over the proxy's baked-in self-signed cert) and reload the running proxy.
# Called by setup-letsencrypt.sh after issue and by renew.sh after renew;
# harmless when nothing is running.
set -euo pipefail
LIVE="${1:?certbot live dir (…/live/<cert-name>)}"
SUITE="${POL_SUITE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
EDGE="$SUITE/.generated/certs/edge"
[[ -s "$LIVE/fullchain.pem" && -s "$LIVE/privkey.pem" ]] || { echo "no fullchain/privkey in $LIVE" >&2; exit 1; }
mkdir -p "$EDGE"
cp -L "$LIVE/fullchain.pem" "$EDGE/fullchain.pem"
cp -L "$LIVE/privkey.pem" "$EDGE/privkey.pem"; chmod 600 "$EDGE/privkey.pem"
echo "staged $EDGE/fullchain.pem (expires $(openssl x509 -in "$EDGE/fullchain.pem" -noout -enddate 2>/dev/null | cut -d= -f2))"
if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx pol-proxy; then
    docker exec pol-proxy nginx -s reload && echo "pol-proxy reloaded"
fi
