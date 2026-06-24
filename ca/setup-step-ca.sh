#!/usr/bin/env bash
# ==============================================================================
# Polari Suite — step-ca bootstrap (CENTRALIZED_CA_PLAN.md §9, §11)
# ==============================================================================
# Initialise the universal internal CA: root + intermediate + provisioners
# (ACME for auto-enroll/renew, JWK for scripted issuance), then export the
# single root_ca.crt users import.
#
# Idempotent: if the root already exists, skip init (never clobber CA material).
# Preflight-gated: fails loud with the fix if `step` is not installed.
#
# Usage:
#   ./setup-step-ca.sh [--dry-run] [--non-interactive]
# Env:
#   STEPPATH         step-ca home (default: $CA_DIR/.step)
#   STEP_CA_NAME     CA display name (default: "Polari Root CA")
#   STEP_CA_DNS      CA DNS name / hostname (default: pol-ca)
#   STEP_CA_ADDRESS  listen address (default: :9000)
#   STEP_ACME_NAME   ACME provisioner name (default: acme)
#   STEP_JWK_NAME    JWK provisioner name (default: polari-jwk)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="$SCRIPT_DIR"
# shellcheck source=lib-ca-common.sh
source "$SCRIPT_DIR/lib-ca-common.sh"
ca_capture_argv "$@"   # for an exact sudo re-run hint if a dep install needs root

# ---- arg parsing (mirror generate-prf-certs.sh flag style) ----
DRY_RUN=false
NON_INTERACTIVE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)         DRY_RUN=true; shift ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) shift ;;
    esac
done

STEPPATH="${STEPPATH:-$CA_DIR/.step}"
STEP_CA_NAME="${STEP_CA_NAME:-Polari Root CA}"
STEP_CA_DNS="${STEP_CA_DNS:-pol-ca}"
STEP_CA_ADDRESS="${STEP_CA_ADDRESS:-:9000}"
STEP_ACME_NAME="${STEP_ACME_NAME:-acme}"
STEP_JWK_NAME="${STEP_JWK_NAME:-polari-jwk}"
ROOT_OUT="$CA_DIR/root_ca.crt"
# A persisted CA password makes init AND later offline issuance non-interactive,
# so the one-command flow runs (and restarts) unattended. Generated once if absent.
STEP_PASSWORD_FILE="${STEP_PASSWORD_FILE:-$STEPPATH/secrets/password}"
# step-ca defaults a provisioner to a 24h max cert duration; our internal leaves
# live longer (renewed periodically). Raise the JWK provisioner's claims to fit.
CERT_NOT_AFTER="${CERT_NOT_AFTER:-720h}"          # default leaf lifetime (matches issuer)
STEP_X509_MAX_DUR="${STEP_X509_MAX_DUR:-8760h}"   # allow up to 1y so CERT_NOT_AFTER fits

export STEPPATH

log_header "step-ca bootstrap (universal internal CA)"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY-RUN: no CA material will be created."

# ---- dependencies (auto-install: step CLI + step-ca) ----
log_step "Dependencies"
ensure_dependencies step
if [[ "$DRY_RUN" == "true" ]]; then
    command -v step >/dev/null 2>&1 \
        && log_ok "step CLI present." \
        || log_warn "step CLI not found (dry-run: showing intended commands; a real run installs it)."
fi
log_ok "STEPPATH=$STEPPATH"

# ---- idempotency: skip init if root already exists ----
ROOT_CA_PEM="$STEPPATH/certs/root_ca.crt"
if [[ -f "$ROOT_CA_PEM" ]]; then
    log_step "Existing CA detected"
    log_ok "Root CA already initialised at $ROOT_CA_PEM — skipping init (idempotent)."
else
    log_step "Initialising root + intermediate"
    # Persist a random CA password first so `step ca init` (and offline issuance
    # later) need no TTY. Idempotent: reuse an existing password file.
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  ${C_YELLOW}DRY-RUN${C_RESET} would generate CA password file: $STEP_PASSWORD_FILE"
    elif [[ ! -f "$STEP_PASSWORD_FILE" ]]; then
        mkdir -p "$(dirname "$STEP_PASSWORD_FILE")"
        ( umask 077; openssl rand -base64 24 > "$STEP_PASSWORD_FILE" )
        chmod 600 "$STEP_PASSWORD_FILE"
        log_ok "Generated CA password file: $STEP_PASSWORD_FILE"
    fi
    # `step ca init` builds root_ca + intermediate_ca + ca.json in $STEPPATH.
    run step ca init \
        --name "$STEP_CA_NAME" \
        --dns "$STEP_CA_DNS" \
        --address "$STEP_CA_ADDRESS" \
        --provisioner "$STEP_JWK_NAME" \
        --password-file "$STEP_PASSWORD_FILE" \
        --deployment-type standalone

    log_step "Adding ACME provisioner ($STEP_ACME_NAME)"
    run step ca provisioner add "$STEP_ACME_NAME" --type ACME
fi

# ---- ensure the JWK provisioner allows our leaf lifetime (idempotent) ----
# Runs on EVERY invocation, including an already-initialised CA, so an existing
# CA created with the default 24h max gets fixed on the next run.
log_step "Provisioner cert-duration claims (max=$STEP_X509_MAX_DUR, default=$CERT_NOT_AFTER)"
run step ca provisioner update "$STEP_JWK_NAME" \
    --x509-default-dur "$CERT_NOT_AFTER" \
    --x509-max-dur "$STEP_X509_MAX_DUR"

# Hand the password file back to the invoking user if we ran under sudo.
reclaim_sudo_ownership "$STEP_PASSWORD_FILE" 2>/dev/null || true

# ---- export the single trust anchor ----
log_step "Exporting root_ca.crt (the one file users import)"
if [[ "$DRY_RUN" == "true" ]]; then
    echo "  ${C_YELLOW}DRY-RUN${C_RESET} would copy $ROOT_CA_PEM -> $ROOT_OUT"
else
    if [[ -f "$ROOT_CA_PEM" ]]; then
        cp "$ROOT_CA_PEM" "$ROOT_OUT"
        log_ok "Exported root -> $ROOT_OUT"
    else
        log_warn "Root not present yet ($ROOT_CA_PEM); run without --dry-run to create it."
    fi
fi

# If we ran under sudo, give the CA material back to the invoking user.
reclaim_sudo_ownership "$STEPPATH" "$ROOT_OUT"

log_step "Done"
log_ok "step-ca ready. Provisioners: JWK=$STEP_JWK_NAME (scripted), ACME=$STEP_ACME_NAME (auto-renew)."
log "Next: ca/issue-internal-certs.sh issues per-service certs from this CA."
