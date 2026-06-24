#!/usr/bin/env bash
# ==============================================================================
# Polari Suite — install all CA dependencies in one shot (CENTRALIZED_CA_PLAN.md §11)
# ==============================================================================
# Detects what's missing (step CLI + step-ca, certbot + DigitalOcean DNS plugin,
# openssl, curl, dig) and installs it via the box's package manager. Detects
# whether it's running as root; if an install is needed and you're not root, it
# dies with the EXACT `sudo -E` re-run so nothing half-installs.
#
# The individual ca/ scripts ALSO self-install just their own deps, so this is a
# convenience to provision everything up front before running the full flow.
#
# Usage:
#   ./install-deps.sh [--dry-run] [--non-interactive]
#   sudo -E bash ./install-deps.sh          # when something needs installing
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="$SCRIPT_DIR"
# shellcheck source=lib-ca-common.sh
source "$SCRIPT_DIR/lib-ca-common.sh"
ca_capture_argv "$@"

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

log_header "Install CA dependencies"
[[ "$DRY_RUN" == "true" ]] && log_warn "DRY-RUN: reporting what would be installed; installing nothing."

log_step "Environment"
log_ok "Package manager: $(detect_pkg_manager)"
if is_root; then
    log_ok "Running as root — installs will proceed."
elif [[ "$DRY_RUN" != "true" ]]; then
    log_warn "Not running as root. If anything below is missing, you'll be told the exact"
    log_warn "  'sudo -E' command to re-run. (Already-present deps need no root.)"
fi

log_step "Ensuring dependencies"
# Only what THIS environment needs: step-ca + openssl everywhere; the Let's
# Encrypt toolchain (certbot + DO DNS plugin + dig/curl) ONLY when this env has
# a public edge (prod). So a dev/staging run needs no certbot — and no sudo at
# all once step is present.
deps=(openssl step)
if [[ "$PUBLIC_EDGE" == "letsencrypt" ]]; then
    deps+=(curl dig certbot-do)
fi
log "env=$CA_ENV  edge=$PUBLIC_EDGE  → ensuring: ${deps[*]}"
ensure_dependencies "${deps[@]}"

log_step "Done"
log_ok "All CA dependencies present."
log "Next: ca/setup-step-ca.sh  →  ca/issue-internal-certs.sh  →  (prod) ca/setup-letsencrypt.sh"
