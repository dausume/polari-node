#!/usr/bin/env bash
# ==============================================================================
# Polari Suite — issue internal certs from step-ca (CENTRALIZED_CA_PLAN.md §11)
# ==============================================================================
# Iterate every manifest row with issuer=step-ca and issue a per-service cert
# with EXPLICIT --san flags (no wildcards, §10). The first SAN is the CN.
#
# Idempotent: a valid cert is skipped; a cert near expiry is renewed; only a
# missing cert is issued.
# Preflight-gated: fails loud if `step` is missing.
#
# Usage:
#   ./issue-internal-certs.sh [--dry-run] [--non-interactive]
# Env:
#   STEPPATH          step-ca home (default: $CA_DIR/.step)
#   STEP_JWK_NAME     provisioner used for scripted issuance (default: polari-jwk)
#   INTERNAL_CERT_DIR output dir for issued certs (default: $CA_DIR/issued)
#   CERT_NOT_AFTER    leaf lifetime hint passed to step (default: 720h = 30d)
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

STEPPATH="${STEPPATH:-$CA_DIR/.step}"
STEP_JWK_NAME="${STEP_JWK_NAME:-polari-jwk}"
INTERNAL_CERT_DIR="${INTERNAL_CERT_DIR:-$CA_DIR/issued}"
CERT_NOT_AFTER="${CERT_NOT_AFTER:-720h}"
# Optional: a file holding the CA/provisioner password so offline issuance runs
# non-interactively. If unset/missing, step prompts (fine when you're at a TTY).
STEP_PASSWORD_FILE="${STEP_PASSWORD_FILE:-$STEPPATH/secrets/password}"
export STEPPATH

log_header "Issue internal certs (step-ca, explicit SANs)"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY-RUN: parsing manifest + building --san args; issuing nothing."

# ---- dependencies + preflight ----
log_step "Dependencies + preflight"
ensure_dependencies step openssl
if [[ "$DRY_RUN" != "true" ]]; then
    [[ -f "$STEPPATH/certs/root_ca.crt" ]] || die \
        "step-ca is not initialised (no root at $STEPPATH/certs/root_ca.crt)." \
        "Run ca/setup-step-ca.sh first."
else
    log_warn "DRY-RUN: skipping step-ca root preflight."
fi
mkdir -p "$INTERNAL_CERT_DIR"
log_ok "Output dir: $INTERNAL_CERT_DIR"

# ---- iterate manifest issuer=step-ca rows ----
issued=0; renewed=0; skipped=0
while IFS=$'\t' read -r name issuer sans; do
    cn="$(sans_cn "$sans")"
    san_args="$(step_san_args "$sans")"
    crt="$INTERNAL_CERT_DIR/$name.crt"
    key="$INTERNAL_CERT_DIR/$name.key"

    log_step "Cert: $name  (CN=$cn)"
    echo "  SANs:     $(_split_sans "$sans" | paste -sd', ')"
    echo "  step args: $san_args"

    # Refuse malformed SANs — e.g. a templated host whose ${VAR} resolved empty
    # ('prf.'), or a leftover '${...}'. Better to fail loudly than issue a bad cert.
    while IFS= read -r _san; do
        if [[ "$_san" == *'${'* || "$_san" == .* || "$_san" == *. || "$_san" == *..* ]]; then
            die "Cert '$name' has a malformed SAN: '$_san' (an unresolved/empty hostname variable?)." \
                "Set the base hostname the way the app defines it, then re-run:" \
                "  staging:  BASE_DOMAIN=<IP>.nip.io     (or generate .generated/.env.staging with NIP_DOMAIN)" \
                "  prod:     BASE_DOMAIN=<your-domain>   (or .generated/.env.prod with PROD_DOMAIN)"
        fi
    done < <(_split_sans "$sans")

    status="$(cert_status "$crt")"
    case "$status" in
        valid)
            log_ok "Valid and not near expiry — skipping (idempotent)."
            skipped=$((skipped+1))
            continue
            ;;
        expiring) action="renew (near expiry)"; renewed=$((renewed+1)) ;;
        missing)  action="issue (missing)";     issued=$((issued+1)) ;;
    esac
    log "Action: $action  (issuance mode: $CA_ISSUANCE_MODE, env: $CA_ENV)"

    # The same command issues or renews — step writes a fresh leaf either way.
    # Issuance MODE is per-environment (CA_ISSUANCE_MODE, derived from CA_ENV):
    #   offline (dev/staging, single host) — sign locally from ca.json + the
    #     intermediate key; NO running CA daemon needed.
    #   online  (prod, multi-node) — talk to the running step-ca daemon (ACME).
    pw_args=()
    [[ -f "$STEP_PASSWORD_FILE" ]] && pw_args=(--password-file "$STEP_PASSWORD_FILE")
    # shellcheck disable=SC2086  # $san_args must word-split into --san flags
    if [[ "$CA_ISSUANCE_MODE" == "offline" ]]; then
        run step ca certificate "$cn" "$crt" "$key" \
            --offline --provisioner "$STEP_JWK_NAME" \
            ${pw_args[@]+"${pw_args[@]}"} \
            --not-after "$CERT_NOT_AFTER" \
            $san_args
    else
        run step ca certificate "$cn" "$crt" "$key" \
            --provisioner "$STEP_JWK_NAME" \
            --not-after "$CERT_NOT_AFTER" \
            $san_args
    fi

    [[ "$DRY_RUN" != "true" && -f "$key" ]] && chmod 600 "$key"
done < <(manifest_rows step-ca)

# If we ran under sudo, give the issued certs back to the invoking user.
reclaim_sudo_ownership "$INTERNAL_CERT_DIR"

log_step "Summary"
log_ok "issued=$issued  renewed=$renewed  skipped(valid)=$skipped"
