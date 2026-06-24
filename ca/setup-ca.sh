#!/usr/bin/env bash
# ==============================================================================
# Polari — one-command CA setup  (deps -> bootstrap -> issue -> [edge] -> verify)
# ==============================================================================
# THE single entry point. Provisions step-ca and issues THIS directory's cert
# set (cert-manifest.conf) in one go, then verifies it. Idempotent — safe to
# re-run to restart / refresh (each phase skips what's already done).
#
# Standalone by design: it uses this dir's manifest + .step + issued/
# automatically, so dropping the toolkit into a node (e.g. polari-rf-node/ca/)
# makes that node self-contained — one command sets up its own certs.
#
# Behaviour is per-environment (reads the app's DEPLOY_ENV -> ENV signal):
#   dev / staging  single host -> offline issuance, no public edge
#   prod           multi-node  -> online CA daemon + Let's Encrypt edge
#
# Usage:
#   bash setup-ca.sh                    # full real run for this node/env
#   DEPLOY_ENV=staging bash setup-ca.sh # staging (offline)  [rf-node default below]
#   DEPLOY_ENV=prod    bash setup-ca.sh # prod (online + LE)
#   bash setup-ca.sh --dry-run          # show everything it WOULD do
#   bash setup-ca.sh --non-interactive  # no prompts (fail fast on missing input)
# ==============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Optional leading positional environment, matching the app's `ENV` convention
# (setup-polari-security.sh dev|prod). Set BEFORE sourcing the lib so the env
# profile resolves to it.  e.g.  bash setup-ca.sh staging
case "${1:-}" in
    dev|staging|prod|production) export DEPLOY_ENV="$1"; shift ;;
esac
# shellcheck source=lib-ca-common.sh
source "$DIR/lib-ca-common.sh"
ca_capture_argv "$@"

# Detect dry-run for our own control flow (sub-shells parse it themselves).
DRY=false
for _a in ${CA_ARGV[@]+"${CA_ARGV[@]}"}; do [[ "$_a" == "--dry-run" ]] && DRY=true; done

log_header "Polari CA setup"
log "env=$CA_ENV   issuance=$CA_ISSUANCE_MODE   edge=$PUBLIC_EDGE"
log "manifest=$MANIFEST_FILE"

phase() {  # <label> <script>
    echo ""
    log_step "$1"
    bash "$DIR/$2" ${CA_ARGV[@]+"${CA_ARGV[@]}"}
}

phase "1/4  Dependencies (step-ca, certbot, …)" install-deps.sh
phase "2/4  step-ca bootstrap (root + intermediate + provisioners)" setup-step-ca.sh
phase "3/4  Issue internal certs (offline/online per env)" issue-internal-certs.sh

# Public edge cert ONLY when this environment has one (prod). Needs LE_DOMAIN /
# LE_EMAIL / DO_API_TOKEN — the script walks you through any that are missing.
if [[ "$PUBLIC_EDGE" == "letsencrypt" ]]; then
    phase "3b   Public edge cert (Let's Encrypt, DNS-01)" setup-letsencrypt.sh
fi

# Verify only on a real run — there's nothing to verify in dry-run.
if [[ "$DRY" == "true" ]]; then
    echo ""; log_warn "DRY-RUN: skipping verification (nothing was created)."
    echo ""; log_ok "Dry run complete — reviewed every step above."
    exit 0
fi

echo ""
log_step "4/4  Verify"
if bash "$DIR/verify-step-ca.sh"; then
    echo ""
    log_ok "CA setup complete + verified for env=$CA_ENV."
    log "Import this root on machines/browsers that need internal trust: $CA_DIR/root_ca.crt"
    exit 0
else
    echo ""
    log_err "Setup ran but verification found issues — see the [FAIL] lines above."
    exit 1
fi
