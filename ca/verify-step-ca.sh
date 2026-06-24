#!/usr/bin/env bash
# ==============================================================================
# Polari Suite — verify the step-ca implementation actually worked (READ-ONLY)
# ==============================================================================
# Run this AFTER setup-step-ca.sh + issue-internal-certs.sh. It issues nothing
# and changes nothing — it just checks the artifacts a working CA produces and
# validates the issued certs against the root. Safe to run repeatedly.
#
# Checks:
#   1. step / step-ca on PATH
#   2. root + intermediate CA material exists in STEPPATH
#   3. root_ca.crt exported (the file users import)
#   4. ca.json carries the JWK (scripted) + ACME (auto-renew) provisioners
#   5. for every manifest issuer=step-ca row: the cert+key exist, the cert
#      chains to the root (openssl verify), its SANs match the manifest, and
#      it isn't expired
#
# Usage:  bash verify-step-ca.sh
# Env:    STEPPATH (default $CA_DIR/.step)   INTERNAL_CERT_DIR (default $CA_DIR/issued)
# ==============================================================================
set -uo pipefail   # NOTE: no -e — we want to run every check and tally failures

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="$SCRIPT_DIR"
# shellcheck source=lib-ca-common.sh
source "$SCRIPT_DIR/lib-ca-common.sh"

STEPPATH="${STEPPATH:-$CA_DIR/.step}"
INTERNAL_CERT_DIR="${INTERNAL_CERT_DIR:-$CA_DIR/issued}"
ROOT_OUT="$CA_DIR/root_ca.crt"
ROOT_CA_PEM="$STEPPATH/certs/root_ca.crt"
INT_CA_PEM="$STEPPATH/certs/intermediate_ca.crt"
CA_JSON="$STEPPATH/config/ca.json"

pass=0; fail=0
ok()   { echo "  ${C_GREEN}[PASS]${C_RESET} $*"; pass=$((pass+1)); }
bad()  { echo "  ${C_RED}[FAIL]${C_RESET} $*"; fail=$((fail+1)); }

log_header "Verify step-ca implementation"

# 1. tooling
log_step "1. step CLI + step-ca installed"
command -v step    >/dev/null 2>&1 && ok "step on PATH ($(command -v step))"       || bad "step not found — run ca/install-deps.sh"
command -v step-ca >/dev/null 2>&1 && ok "step-ca on PATH ($(command -v step-ca))" || bad "step-ca not found — run ca/install-deps.sh"

# 2. CA material
log_step "2. Root + intermediate CA material"
[[ -f "$ROOT_CA_PEM" ]] && ok "root_ca.crt present ($ROOT_CA_PEM)" || bad "no root at $ROOT_CA_PEM — setup-step-ca.sh didn't init"
[[ -f "$INT_CA_PEM"  ]] && ok "intermediate_ca.crt present"        || bad "no intermediate at $INT_CA_PEM"

# 3. exported root
log_step "3. Exported trust anchor"
[[ -f "$ROOT_OUT" ]] && ok "root_ca.crt exported for import ($ROOT_OUT)" || bad "root not exported to $ROOT_OUT"

# 4. provisioners
log_step "4. Provisioners in ca.json"
if [[ -f "$CA_JSON" ]]; then
    grep -q '"type": *"JWK"'  "$CA_JSON" 2>/dev/null || grep -qi 'JWK'  "$CA_JSON" 2>/dev/null \
        && ok "JWK provisioner present (scripted issuance)" || bad "no JWK provisioner in ca.json"
    grep -qi 'ACME' "$CA_JSON" 2>/dev/null \
        && ok "ACME provisioner present (auto-renew)" || bad "no ACME provisioner in ca.json"
else
    bad "ca.json not found at $CA_JSON"
fi

# 5. issued internal certs vs the manifest
log_step "5. Issued internal certs (chain + SANs + expiry)"
if [[ ! -f "$ROOT_CA_PEM" ]]; then
    bad "skipping cert checks — no root CA to verify against"
else
    # Build a verify bundle (root + intermediate) so openssl can walk the chain.
    BUNDLE="$(mktemp)"; cat "$ROOT_CA_PEM" "$INT_CA_PEM" 2>/dev/null > "$BUNDLE"
    while IFS=$'\t' read -r name issuer sans; do
        crt="$INTERNAL_CERT_DIR/$name.crt"
        key="$INTERNAL_CERT_DIR/$name.key"
        if [[ ! -f "$crt" || ! -f "$key" ]]; then
            bad "$name: cert/key missing ($crt) — issue-internal-certs.sh didn't produce it"
            continue
        fi
        # chain
        if openssl verify -CAfile "$BUNDLE" "$crt" >/dev/null 2>&1; then
            chain_ok=1
        else
            chain_ok=0
        fi
        # not expired (>1 day left)
        openssl x509 -in "$crt" -checkend 86400 >/dev/null 2>&1 && exp_ok=1 || exp_ok=0
        # SANs present
        cert_sans="$(openssl x509 -in "$crt" -noout -ext subjectAltName 2>/dev/null | tr -d ' ')"
        miss=""
        while IFS= read -r s; do
            [[ -z "$s" ]] && continue
            echo "$cert_sans" | grep -q "$s" || miss="$miss $s"
        done < <(_split_sans "$sans")
        if [[ $chain_ok -eq 1 && $exp_ok -eq 1 && -z "$miss" ]]; then
            ok "$name: chains to root, valid, SANs ok ($sans)"
        else
            reason=""
            [[ $chain_ok -eq 0 ]] && reason="$reason chain!"
            [[ $exp_ok   -eq 0 ]] && reason="$reason expired/expiring!"
            [[ -n "$miss" ]]      && reason="$reason missing-SANs:$miss"
            bad "$name:$reason"
        fi
    done < <(manifest_rows step-ca)
    rm -f "$BUNDLE"
fi

echo
log_header "Result: $pass passed, $fail failed"
[[ $fail -eq 0 ]] && { log_ok "step-ca implementation looks healthy."; exit 0; }
log_err "Some checks failed — see [FAIL] lines above. Paste them and I can pinpoint the fix."
exit 1
