#!/usr/bin/env bash
# ==============================================================================
# Polari Suite — Let's Encrypt edge cert (CENTRALIZED_CA_PLAN.md §9, §10, §11)
# ==============================================================================
# The single public-edge cert. Runs the interactive human-step walkthrough (DO
# token, DNS delegation, domain+email, port-forward), then AUTO-BUILDS a
# `certbot certonly --dns-digitalocean -d ... -d ...` command from the manifest's
# letsencrypt row (explicit SANs, no wildcards).
#
# Idempotent: a valid existing cert is skipped (certbot itself also no-ops).
# Preflight-gated: domain set? DO token present+valid? DNS delegated (dig NS)?
#
# Usage:
#   ./setup-letsencrypt.sh [--dry-run] [--non-interactive]
# Env:
#   LE_DOMAIN          public domain (e.g. polari-systems.org)  [required]
#   LE_EMAIL           LE account email                          [required]
#   DO_API_TOKEN       DigitalOcean API token (DNS-01)           [required]
#   LE_CERT_NAME       manifest row to issue (default: pol-proxy-public)
#   LE_ENV_FILE        env file to persist collected values (default: $CA_DIR/.generated/.env.le)
#   CERTBOT_CONFIG_DIR certbot --config-dir (default: $CA_DIR/.generated/letsencrypt)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="$SCRIPT_DIR"
# shellcheck source=lib-ca-common.sh
source "$SCRIPT_DIR/lib-ca-common.sh"
ca_capture_argv "$@"   # for an exact sudo re-run hint if a dep install needs root

DRY_RUN=false
NON_INTERACTIVE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)         DRY_RUN=true; shift ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        -h|--help)         grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) shift ;;
    esac
done

# shellcheck source=walkthrough.sh
source "$SCRIPT_DIR/walkthrough.sh"

LE_CERT_NAME="${LE_CERT_NAME:-pol-proxy-public}"
LE_ENV_FILE="${LE_ENV_FILE:-$CA_DIR/.generated/.env.le}"
CERTBOT_CONFIG_DIR="${CERTBOT_CONFIG_DIR:-$CA_DIR/.generated/letsencrypt}"
DO_CREDS_FILE="$CERTBOT_CONFIG_DIR/digitalocean.ini"

log_header "Let's Encrypt edge cert ($LE_CERT_NAME)"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY-RUN: walkthrough + manifest->certbot build; issuing nothing."

# ---- resolve the manifest row first (so we know the domain set early) ----
log_step "Resolve manifest row '$LE_CERT_NAME'"
SANS="$(manifest_sans "$LE_CERT_NAME" || true)"
[[ -n "$SANS" ]] || die "No letsencrypt row named '$LE_CERT_NAME' in $MANIFEST_FILE." \
    "Add a row:  $LE_CERT_NAME | letsencrypt | host1, host2, ..." \
    "Or set LE_CERT_NAME to an existing letsencrypt row."

# Verify it is actually a letsencrypt-issuer row.
ROW_ISSUER="$(manifest_rows letsencrypt | awk -F'\t' -v n="$LE_CERT_NAME" '$1==n{print $2}')"
[[ "$ROW_ISSUER" == "letsencrypt" ]] || die \
    "Manifest row '$LE_CERT_NAME' is not issuer=letsencrypt." \
    "The edge cert must be a letsencrypt row in $MANIFEST_FILE."

CERTBOT_D_ARGS="$(certbot_d_args "$SANS")"
log_ok "SANs: $(_split_sans "$SANS" | paste -sd', ')"
echo "  certbot -d args: $CERTBOT_D_ARGS"

# Derive a default domain from the first SAN if LE_DOMAIN unset.
: "${LE_DOMAIN:=$(sans_cn "$SANS")}"

# ---- dependencies (auto-install: certbot + DO plugin, dig, curl, openssl) ----
# Done BEFORE the walkthrough so the DNS-delegation check (dig) is available.
log_step "Dependencies"
ensure_dependencies certbot-do dig curl openssl

# ---- interactive walkthrough for the human-only steps (each preflight-gated) ----
walkthrough_domain_email "$LE_ENV_FILE"
walkthrough_do_token     "$LE_ENV_FILE"
walkthrough_dns_delegation "$LE_DOMAIN"
walkthrough_port_forward

# ---- final preflight before any issuance ----
log_step "Preflight (issuance)"
[[ -n "${LE_DOMAIN:-}" ]]    || die "LE_DOMAIN unset."  "Set LE_DOMAIN and re-run."
[[ -n "${LE_EMAIL:-}" ]]     || die "LE_EMAIL unset."   "Set LE_EMAIL and re-run."
[[ -n "${DO_API_TOKEN:-}" ]] || die "DO_API_TOKEN unset." "Provide the DigitalOcean token and re-run."
log_ok "All required values present."

# ---- idempotency check against existing issued cert ----
LIVE_CERT="$CERTBOT_CONFIG_DIR/live/$LE_CERT_NAME/fullchain.pem"
if cert_is_valid "$LIVE_CERT"; then
    log_ok "Existing edge cert valid and not near expiry — skipping (idempotent): $LIVE_CERT"
    log "Use ca/renew.sh to renew near expiry."
    exit 0
fi

# ---- write the DO credentials ini (certbot-dns-digitalocean expects this) ----
# ---- challenge: dns (DigitalOcean DNS-01, default) or http (HTTP-01 webroot) ----
# LE_CHALLENGE=http needs no DNS-provider API: the prod proxy serves
# /.well-known/acme-challenge/ from .generated/certbot-www on :80, so any
# registrar works. DNS-01 stays the default (it also allows internal-only names).
LE_CHALLENGE="${LE_CHALLENGE:-dns}"
LE_WEBROOT="${LE_WEBROOT:-$CA_DIR/../../.generated/certbot-www}"
if [[ "$LE_CHALLENGE" == "http" ]]; then
    log_step "HTTP-01 webroot ($LE_WEBROOT) — the proxy must be up on :80"
    mkdir -p "$LE_WEBROOT"
    run certbot certonly \
        --webroot -w "$LE_WEBROOT" \
        --cert-name "$LE_CERT_NAME" \
        --config-dir "$CERTBOT_CONFIG_DIR" \
        --work-dir "$CERTBOT_CONFIG_DIR/work" \
        --logs-dir "$CERTBOT_CONFIG_DIR/logs" \
        -m "$LE_EMAIL" \
        --non-interactive --agree-tos \
        $CERTBOT_D_ARGS
    reclaim_sudo_ownership "$CERTBOT_CONFIG_DIR" "$LE_ENV_FILE"
    bash "$CA_DIR/stage-edge-cert.sh" "$CERTBOT_CONFIG_DIR/live/$LE_CERT_NAME" || true
    log_ok "Edge cert issued (HTTP-01) -> $CERTBOT_CONFIG_DIR/live/$LE_CERT_NAME/fullchain.pem"
    exit 0
fi

log_step "DigitalOcean credentials file"
if [[ "$DRY_RUN" == "true" ]]; then
    echo "  ${C_YELLOW}DRY-RUN${C_RESET} would write $DO_CREDS_FILE (chmod 600) with dns_digitalocean_token=***"
else
    mkdir -p "$CERTBOT_CONFIG_DIR"
    umask 077
    printf 'dns_digitalocean_token = %s\n' "$DO_API_TOKEN" > "$DO_CREDS_FILE"
    chmod 600 "$DO_CREDS_FILE"
    log_ok "Wrote $DO_CREDS_FILE"
fi

# ---- AUTO-BUILD + run certbot from the manifest SANs ----
log_step "Issue edge cert via DNS-01 (DigitalOcean)"
NONINT_FLAG="--non-interactive --agree-tos"
# shellcheck disable=SC2086  # $CERTBOT_D_ARGS must word-split into -d flags
run certbot certonly \
    --dns-digitalocean \
    --dns-digitalocean-credentials "$DO_CREDS_FILE" \
    --dns-digitalocean-propagation-seconds 60 \
    --cert-name "$LE_CERT_NAME" \
    --config-dir "$CERTBOT_CONFIG_DIR" \
    --work-dir "$CERTBOT_CONFIG_DIR/work" \
    --logs-dir "$CERTBOT_CONFIG_DIR/logs" \
    -m "$LE_EMAIL" \
    $NONINT_FLAG \
    $CERTBOT_D_ARGS

# If we ran under sudo, give the issued cert + env back to the invoking user.
reclaim_sudo_ownership "$CERTBOT_CONFIG_DIR" "$LE_ENV_FILE"

log_step "Done"
if [[ "$DRY_RUN" == "true" ]]; then
    log_ok "DRY-RUN complete — reviewed the exact certbot command above."
else
    log_ok "Edge cert issued -> $LIVE_CERT"
    # stage into .generated/certs/edge (what docker-compose.prod.yml mounts) and reload the proxy if it runs
    bash "$CA_DIR/stage-edge-cert.sh" "$CERTBOT_CONFIG_DIR/live/$LE_CERT_NAME" || true
fi
