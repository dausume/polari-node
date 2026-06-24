#!/usr/bin/env bash
# ==============================================================================
# Polari Suite — interactive walkthrough helpers (CENTRALIZED_CA_PLAN.md §11)
# ==============================================================================
# Guidance functions for the HUMAN-ONLY steps of edge-cert setup. Each function
# is gated by a verifying preflight that fails LOUD with the exact fix rather
# than letting a half-broken bring-up proceed.
#
# This file is sourced by setup-letsencrypt.sh (and can be run standalone for
# the import steps). It depends on lib-ca-common.sh being sourced first.
# ==============================================================================
set -euo pipefail

_WT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-ca-common.sh
[[ -n "${_POLARI_CA_COMMON_SOURCED:-}" ]] || source "$_WT_DIR/lib-ca-common.sh"

# ------------------------------------------------------------------------------
# walkthrough_do_token — ensure a DigitalOcean API token is present and valid.
# Prompts if absent (interactive), validates with a single read-only API call,
# and persists into the given env file. Preflight-gated.
# Inputs:  DO_API_TOKEN (env), arg1 = env file to persist into (optional)
# ------------------------------------------------------------------------------
walkthrough_do_token() {
    local env_file="${1:-}"
    log_step "DigitalOcean API token"

    prompt_secret "Enter your DigitalOcean API token (read+write DNS scope)" DO_API_TOKEN

    if [[ -z "${DO_API_TOKEN:-}" ]]; then
        die "No DigitalOcean API token provided." \
            "Create one at: https://cloud.digitalocean.com/account/api/tokens" \
            "Grant it WRITE scope (certbot-dns-digitalocean edits TXT records)." \
            "Then re-run with DO_API_TOKEN=... or paste it when prompted."
    fi

    # Validate (read-only) unless dry-run. /v2/account is the cheapest probe.
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  ${C_YELLOW}DRY-RUN${C_RESET} would validate token via: curl -s https://api.digitalocean.com/v2/account"
    else
        require_command curl "Install curl to validate the DO token."
        local code
        code="$(curl -s -o /dev/null -w '%{http_code}' \
            -H "Authorization: Bearer ${DO_API_TOKEN}" \
            https://api.digitalocean.com/v2/account || echo "000")"
        if [[ "$code" != "200" ]]; then
            die "DigitalOcean token rejected (HTTP $code)." \
                "Confirm the token is correct and not expired." \
                "Confirm it has account/DNS scope." \
                "Regenerate at https://cloud.digitalocean.com/account/api/tokens"
        fi
        log_ok "DigitalOcean token validated."
    fi

    if [[ -n "$env_file" ]]; then
        _persist_env "$env_file" DO_API_TOKEN "$DO_API_TOKEN"
        log "Saved DO token -> $env_file"
    fi
}

# ------------------------------------------------------------------------------
# walkthrough_dns_delegation <domain> — confirm the domain's NS records point at
# DigitalOcean. If not, print the EXACT Namecheap steps and stop.
# ------------------------------------------------------------------------------
walkthrough_dns_delegation() {
    local domain="$1"
    log_step "DNS delegation check for $domain"

    local ns_cmd="dig +short NS $domain"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  ${C_YELLOW}DRY-RUN${C_RESET} would check delegation via: $ns_cmd"
        echo "  ${C_YELLOW}DRY-RUN${C_RESET} expecting nameservers matching *.digitalocean.com"
        return 0
    fi

    require_command dig "Install dnsutils/bind-tools to get 'dig' (DNS delegation check)."

    local ns
    ns="$($ns_cmd 2>/dev/null || true)"
    if echo "$ns" | grep -qi 'digitalocean.com'; then
        log_ok "$domain is delegated to DigitalOcean nameservers:"
        echo "$ns" | sed 's/^/      /'
        return 0
    fi

    die "$domain is NOT delegated to DigitalOcean DNS (found: ${ns:-none})." \
        "On Namecheap: Domain List -> Manage '$domain' -> NAMESERVERS." \
        "Choose 'Custom DNS' and set the three DigitalOcean nameservers:" \
        "    ns1.digitalocean.com" \
        "    ns2.digitalocean.com" \
        "    ns3.digitalocean.com" \
        "In DigitalOcean: Networking -> Domains -> add '$domain'." \
        "Wait for propagation (up to ~30 min), then re-run this step."
}

# ------------------------------------------------------------------------------
# walkthrough_domain_email — collect/persist LE domain + account email.
# Inputs: LE_DOMAIN, LE_EMAIL (env); arg1 = env file (optional)
# ------------------------------------------------------------------------------
walkthrough_domain_email() {
    local env_file="${1:-}"
    log_step "Domain & Let's Encrypt account email"

    prompt "Public domain (e.g. polari-systems.org)" LE_DOMAIN
    prompt "Let's Encrypt account email (for expiry notices)" LE_EMAIL

    [[ -n "${LE_DOMAIN:-}" ]] || die "A public domain is required for the edge cert." \
        "Set LE_DOMAIN=polari-systems.org and re-run."
    [[ -n "${LE_EMAIL:-}" ]] || die "A Let's Encrypt account email is required." \
        "Set LE_EMAIL=you@example.com and re-run."

    if [[ -n "$env_file" ]]; then
        _persist_env "$env_file" LE_DOMAIN "$LE_DOMAIN"
        _persist_env "$env_file" LE_EMAIL "$LE_EMAIL"
    fi
    log_ok "Domain=$LE_DOMAIN  Email=$LE_EMAIL"
}

# ------------------------------------------------------------------------------
# walkthrough_port_forward — reminder for the staging dev box. DNS-01 issuance
# does NOT need inbound :443; only remote ACCESS does. Informational gate.
# ------------------------------------------------------------------------------
walkthrough_port_forward() {
    log_step "Port-forward reminder (remote access only)"
    cat <<'EOF'
  Issuance via DNS-01 does NOT require any inbound port (TXT records only).
  But for a remote tester to REACH this box you must forward inbound :443:
    Router admin -> Port Forwarding -> TCP 443 -> <this box's LAN IP>:443
  Home IP is likely dynamic -> keep the A record fresh with a DDNS cron
  (script the DigitalOcean API A-record update). Issuance is unaffected by
  IP changes; only reachability is.
EOF
    if [[ "$NON_INTERACTIVE" != "true" && "$DRY_RUN" != "true" ]]; then
        confirm "Port-forward acknowledged (or not needed)?" || true
    fi
}

# ------------------------------------------------------------------------------
# walkthrough_root_import <root_ca_path> — after issuance, print OS/Firefox/
# Chrome import steps for the step-ca root (internal plane / staging tester).
# ------------------------------------------------------------------------------
walkthrough_root_import() {
    local root="${1:-$CA_DIR/root_ca.crt}"
    log_step "Import the step-ca root (internal plane / dev machines)"
    cat <<EOF
  The single trust anchor is:
      $root

  Linux (system store):
      sudo cp "$root" /usr/local/share/ca-certificates/polari-root.crt
      sudo update-ca-certificates

  Firefox (has its own store):
      Settings -> Privacy & Security -> Certificates -> View Certificates
      -> Authorities -> Import -> select $root
      -> check "Trust this CA to identify websites".

  Chrome / Chromium (Linux uses the NSS db):
      certutil -d sql:\$HOME/.pki/nssdb -A -t "C,," -n "Polari Root" -i "$root"
      (or Settings -> Privacy and security -> Security -> Manage certificates
       -> Authorities -> Import)

  Remote users on the PUBLIC edge import NOTHING — the edge presents a
  Let's Encrypt cert, trusted by every browser natively.
EOF
}

# ------------------------------------------------------------------------------
# _persist_env <file> <KEY> <VALUE> — idempotently upsert KEY=VALUE into file.
# Honors DRY_RUN (prints, writes nothing). chmod 600 (secrets live here).
# ------------------------------------------------------------------------------
_persist_env() {
    local file="$1" key="$2" value="$3"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  ${C_YELLOW}DRY-RUN${C_RESET} would persist ${key}=*** -> $file"
        return 0
    fi
    mkdir -p "$(dirname "$file")"
    touch "$file"; chmod 600 "$file"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        # replace existing line (value may contain /, use | as sed delim)
        local tmp; tmp="$(mktemp)"
        grep -v "^${key}=" "$file" > "$tmp" || true
        mv "$tmp" "$file"
    fi
    printf '%s=%s\n' "$key" "$value" >> "$file"
    chmod 600 "$file"
}

# Allow running standalone just to print the import steps.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    NON_INTERACTIVE="${NON_INTERACTIVE:-true}"
    walkthrough_root_import "${1:-$CA_DIR/root_ca.crt}"
fi
