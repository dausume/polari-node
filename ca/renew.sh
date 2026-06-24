#!/usr/bin/env bash
# ==============================================================================
# Polari Suite — renew all certs (CENTRALIZED_CA_PLAN.md §11)
# ==============================================================================
# Safe to run from cron / a renewer sidecar. Renews the step-ca leaves and the
# Let's Encrypt edge cert (both no-op when not near expiry), then reloads nginx
# so new material takes effect without a rebuild.
#
# Idempotent + preflight-gated. Missing tools are warned (not fatal) so a box
# that only runs one issuer still renews the other.
#
# Usage:
#   ./renew.sh [--dry-run] [--non-interactive]
# Env:
#   STEPPATH           step-ca home (default: $CA_DIR/.step)
#   INTERNAL_CERT_DIR  issued step-ca certs (default: $CA_DIR/issued)
#   CERTBOT_CONFIG_DIR certbot config dir   (default: $CA_DIR/.generated/letsencrypt)
#   NGINX_RELOAD_CMD   reload command (default: nginx -s reload)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="$SCRIPT_DIR"
# shellcheck source=lib-ca-common.sh
source "$SCRIPT_DIR/lib-ca-common.sh"

DRY_RUN=false
NON_INTERACTIVE=true   # cron-safe default: never prompt
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)         DRY_RUN=true; shift ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        -h|--help)         grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) shift ;;
    esac
done

STEPPATH="${STEPPATH:-$CA_DIR/.step}"
INTERNAL_CERT_DIR="${INTERNAL_CERT_DIR:-$CA_DIR/issued}"
CERTBOT_CONFIG_DIR="${CERTBOT_CONFIG_DIR:-$CA_DIR/.generated/letsencrypt}"
NGINX_RELOAD_CMD="${NGINX_RELOAD_CMD:-nginx -s reload}"
export STEPPATH

log_header "Renew certs (step-ca + Let's Encrypt) and reload nginx"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY-RUN: showing renew commands; renewing nothing."

did_something=false

# ---- step-ca leaves ----
log_step "step-ca leaves"
if command -v step >/dev/null 2>&1; then
    if [[ -d "$INTERNAL_CERT_DIR" ]]; then
        # Renew each issued leaf in place; `step ca renew` no-ops if not due
        # unless --force. We let step decide based on the expiry window.
        shopt -s nullglob
        for crt in "$INTERNAL_CERT_DIR"/*.crt; do
            key="${crt%.crt}.key"
            [[ -f "$key" ]] || { log_warn "No key for $crt — skipping."; continue; }
            log "renew $(basename "$crt")"
            run step ca renew --force "$crt" "$key"
            did_something=true
        done
        shopt -u nullglob
    else
        log_warn "No issued-cert dir ($INTERNAL_CERT_DIR) — nothing to renew."
    fi
else
    log_warn "step CLI not found — skipping internal renew."
fi

# ---- Let's Encrypt edge ----
log_step "Let's Encrypt edge"
if command -v certbot >/dev/null 2>&1; then
    run certbot renew \
        --config-dir "$CERTBOT_CONFIG_DIR" \
        --work-dir "$CERTBOT_CONFIG_DIR/work" \
        --logs-dir "$CERTBOT_CONFIG_DIR/logs"
    did_something=true
else
    log_warn "certbot not found — skipping edge renew."
fi

# ---- reload nginx (only if something could have changed) ----
log_step "Reload nginx"
if [[ "$did_something" == "true" ]]; then
    if command -v nginx >/dev/null 2>&1 || [[ "$DRY_RUN" == "true" ]]; then
        # shellcheck disable=SC2086  # NGINX_RELOAD_CMD is intentionally split
        run $NGINX_RELOAD_CMD
    else
        log_warn "nginx not found — skipping reload (renew in a container/sidecar?)."
    fi
else
    log "Nothing renewed — skipping nginx reload."
fi

log_step "Done"
log_ok "Renew pass complete."
