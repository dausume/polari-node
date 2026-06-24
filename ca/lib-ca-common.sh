#!/usr/bin/env bash
# ==============================================================================
# Polari Suite — CA common library (sourced, not executed)
# ==============================================================================
# Shared helpers for the ca/ sub-shells (CENTRALIZED_CA_PLAN.md §11):
#   - logging / color helpers (mirrors setup-polari-security.sh's plain style)
#   - prompt / confirm (interactive vs --non-interactive aware)
#   - idempotency + cert-expiry checks (openssl x509 -checkend)
#   - cert-manifest parser (skip comments/blanks, split on '|', trim)
#   - SAN -> args builders for step (--san a --san b ...) and certbot (-d a -d b ...)
#
# Sourced by every ca/*.sh script. It defines functions and a few defaults but
# performs no side effects on its own.
# ==============================================================================

# Guard against double-sourcing.
if [[ -n "${_POLARI_CA_COMMON_SOURCED:-}" ]]; then
    return 0 2>/dev/null || exit 0
fi
_POLARI_CA_COMMON_SOURCED=1

# ------------------------------------------------------------------------------
# Resolved paths (CA_DIR is this lib's directory unless caller overrides it).
# ------------------------------------------------------------------------------
CA_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${CA_DIR:=$CA_LIB_DIR}"
: "${MANIFEST_FILE:=$CA_DIR/cert-manifest.conf}"

# ------------------------------------------------------------------------------
# Shared run-mode flags. Each script sets these from its own arg parsing, but we
# default them here so sourcing is enough to make the helpers safe to call.
# ------------------------------------------------------------------------------
: "${DRY_RUN:=false}"
: "${NON_INTERACTIVE:=false}"

# Renew a cert when it expires within this many seconds (30 days).
: "${CERT_RENEW_WINDOW_SECONDS:=2592000}"

# ------------------------------------------------------------------------------
# Environment — REUSE the app's existing per-environment convention; do not
# invent a new one. setup-polari-security.sh takes a positional ENV (dev|prod|
# cleanup) and writes DEPLOY_ENV into .generated/.env.*; nip-staging-setup.sh
# uses DEPLOY_ENV=staging; prod-setup.sh writes DEPLOY_ENV=production. We read
# that same signal so the CA flow tracks how the app already builds per env.
#
# `CA_ENV` is the normalized result (dev|staging|prod). Each derived behavior is
# still individually overridable — `:=` only fills what the env didn't set:
#   dev      single host, self-trust    -> offline issuance, no public edge
#   staging  single host, remote-tested -> offline issuance, edge OFF
#                                          (flip PUBLIC_EDGE=letsencrypt for
#                                           browser-trusted remote access)
#   prod     multi-node, public         -> ONLINE step-ca daemon (ACME) + LE edge
# ------------------------------------------------------------------------------
CA_ENV="${CA_ENV:-${DEPLOY_ENV:-${ENV:-dev}}}"
case "$CA_ENV" in
    production) CA_ENV="prod" ;;          # normalize prod-setup.sh's value
esac
case "$CA_ENV" in
    prod)
        : "${CA_ISSUANCE_MODE:=online}"    # talk to a running step-ca daemon (multi-node + ACME)
        : "${PUBLIC_EDGE:=letsencrypt}"    # public browser-trusted edge cert
        : "${INTERNAL_TLS:=terminate}"
        ;;
    staging)
        : "${CA_ISSUANCE_MODE:=offline}"   # single host: sign locally from ca.json, no daemon
        : "${PUBLIC_EDGE:=none}"           # flip to letsencrypt for remote browser-trust
        : "${INTERNAL_TLS:=terminate}"
        ;;
    *)
        CA_ENV="dev"
        : "${CA_ISSUANCE_MODE:=offline}"
        : "${PUBLIC_EDGE:=none}"
        : "${INTERNAL_TLS:=terminate}"
        ;;
esac

# ------------------------------------------------------------------------------
# Hostnames — pull the per-env BASE_DOMAIN from the app's OWN generated env
# (the single source of truth), so cert SANs use the SAME domain the app serves
# on instead of a retyped copy. The manifest templates SANs as `prf.${BASE_DOMAIN}`.
#   staging -> NIP_DOMAIN  (e.g. 10.0.0.101.nip.io)  from .generated/.env.staging
#   prod    -> PROD_DOMAIN (e.g. polari-systems.org) from .generated/.env.prod
# Override by exporting BASE_DOMAIN (or NIP_DOMAIN / PROD_DOMAIN) directly.
# ------------------------------------------------------------------------------
: "${CA_GENERATED_DIR:=$CA_DIR/../.generated}"
_ca_envvar() {  # <KEY> <env-file>  -> value (empty if file/key absent)
    [[ -f "$2" ]] && sed -n "s/^$1=//p" "$2" | head -1
}
case "$CA_ENV" in
    staging) : "${NIP_DOMAIN:=$(_ca_envvar NIP_DOMAIN  "$CA_GENERATED_DIR/.env.staging")}"
             : "${BASE_DOMAIN:=$NIP_DOMAIN}" ;;
    prod)    : "${PROD_DOMAIN:=$(_ca_envvar PROD_DOMAIN "$CA_GENERATED_DIR/.env.prod")}"
             : "${BASE_DOMAIN:=$PROD_DOMAIN}" ;;
    *)       : "${BASE_DOMAIN:=}" ;;   # dev: pass one in (e.g. localhost / a .local) if needed
esac
export BASE_DOMAIN

# ------------------------------------------------------------------------------
# Color / logging — keep it plain like setup-polari-security.sh; only colorize
# when stdout is a TTY so logs/CI stay clean.
# ------------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'
    C_YELLOW=$'\033[0;33m'; C_BLUE=$'\033[0;34m'; C_BOLD=$'\033[1m'
else
    C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi

log()       { echo "${C_BLUE}[ca]${C_RESET} $*"; }
log_step()  { echo ""; echo "${C_BOLD}==> $*${C_RESET}"; }
log_ok()    { echo "${C_GREEN}[ok]${C_RESET}   $*"; }
log_warn()  { echo "${C_YELLOW}[warn]${C_RESET} $*" >&2; }
log_err()   { echo "${C_RED}[err]${C_RESET}  $*" >&2; }

# Print a banner header (matches the existing scripts' === framing).
log_header() {
    echo "============================================"
    echo "$*"
    echo "============================================"
}

# Fail loudly: print the message + the exact fix, then exit non-zero.
# Usage: die "what went wrong" "how to fix it (one line per arg)" ...
die() {
    local msg="$1"; shift
    log_err "$msg"
    if [[ $# -gt 0 ]]; then
        echo "" >&2
        echo "  ${C_BOLD}Fix:${C_RESET}" >&2
        local line
        for line in "$@"; do
            echo "    $line" >&2
        done
    fi
    exit 1
}

# In dry-run, print the command we WOULD run (and don't run it).
# Usage: run cmd arg arg ...   -> echoes "+ cmd arg arg" then executes unless DRY_RUN.
run() {
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  ${C_YELLOW}DRY-RUN${C_RESET} would run: $*"
        return 0
    fi
    echo "  ${C_BLUE}+${C_RESET} $*"
    "$@"
}

# ------------------------------------------------------------------------------
# Prompt / confirm — interactive by default, fail-fast in --non-interactive.
# ------------------------------------------------------------------------------

# prompt "Question" VARNAME ["default"]
# In non-interactive mode: use existing $VARNAME or the default; if neither is
# set, die (a required value is missing).
prompt() {
    local question="$1" varname="$2" default="${3:-}"
    local current="${!varname:-}"

    if [[ -n "$current" ]]; then
        return 0
    fi

    if [[ "$NON_INTERACTIVE" == "true" ]]; then
        if [[ -n "$default" ]]; then
            printf -v "$varname" '%s' "$default"
            return 0
        fi
        die "Required value '$varname' is not set (non-interactive mode)." \
            "Set $varname in the environment / .env before running, or run interactively."
    fi

    local reply
    if [[ -n "$default" ]]; then
        read -rp "  $question [$default]: " reply
        reply="${reply:-$default}"
    else
        read -rp "  $question: " reply
    fi
    printf -v "$varname" '%s' "$reply"
}

# prompt_secret "Question" VARNAME  — like prompt but no echo, no default.
prompt_secret() {
    local question="$1" varname="$2"
    local current="${!varname:-}"
    [[ -n "$current" ]] && return 0

    if [[ "$NON_INTERACTIVE" == "true" ]]; then
        die "Required secret '$varname' is not set (non-interactive mode)." \
            "Set $varname in the environment / .env before running."
    fi

    local reply
    read -rsp "  $question: " reply
    echo ""
    printf -v "$varname" '%s' "$reply"
}

# confirm "Question" — returns 0 on yes. In non-interactive mode returns 0
# (assume yes for unattended runs); callers that need a hard gate use a preflight.
confirm() {
    local question="$1"
    if [[ "$NON_INTERACTIVE" == "true" ]]; then
        return 0
    fi
    local reply
    read -rp "  $question (yes/no): " reply
    [[ "$reply" == "yes" || "$reply" == "y" ]]
}

# ------------------------------------------------------------------------------
# Preflight: assert an external command exists, else die with the fix.
# Usage: require_command step "Install step CLI" "  https://smallstep.com/docs/step-cli/installation"
# ------------------------------------------------------------------------------
require_command() {
    local cmd="$1"; shift
    if command -v "$cmd" >/dev/null 2>&1; then
        return 0
    fi
    die "Required command '$cmd' not found on PATH." "$@"
}

# ------------------------------------------------------------------------------
# Idempotency / expiry.
# ------------------------------------------------------------------------------

# cert_is_valid <cert_path> — true if file exists and is NOT expiring within the
# renew window. Uses openssl x509 -checkend (seconds).
cert_is_valid() {
    local crt="$1"
    [[ -f "$crt" ]] || return 1
    openssl x509 -in "$crt" -checkend "$CERT_RENEW_WINDOW_SECONDS" >/dev/null 2>&1
}

# cert_status <cert_path> — echoes one of: missing | expiring | valid.
cert_status() {
    local crt="$1"
    if [[ ! -f "$crt" ]]; then
        echo "missing"; return
    fi
    if openssl x509 -in "$crt" -checkend "$CERT_RENEW_WINDOW_SECONDS" >/dev/null 2>&1; then
        echo "valid"
    else
        echo "expiring"
    fi
}

# ------------------------------------------------------------------------------
# Manifest parsing.
# ------------------------------------------------------------------------------

# _trim <string> — strip leading/trailing whitespace.
_trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# _resolve_issuer <issuer> — map a manifest's logical issuer to a CONCRETE one
# for THIS environment, so a single manifest serves dev/staging/prod:
#   edge      this node's PUBLIC edge -> 'letsencrypt' when the env has a public
#             edge (PUBLIC_EDGE=letsencrypt, i.e. prod), else 'step-ca' (dev/
#             staging single host: sign the public SANs locally so the node is
#             reachable; browser-trust then needs the root imported).
#   step-ca / letsencrypt  pass through unchanged.
_resolve_issuer() {
    case "$1" in
        edge) [[ "${PUBLIC_EDGE:-none}" == "letsencrypt" ]] && echo "letsencrypt" || echo "step-ca" ;;
        *)    echo "$1" ;;
    esac
}

# _expand_sans <string> — substitute ${VAR} hostname placeholders in a manifest
# SAN list from a fixed allowlist (no eval), so SANs are defined ONCE in the app's
# env and reused here. `prf.${BASE_DOMAIN}` -> `prf.10.0.0.101.nip.io` (staging)
# or `prf.polari-systems.org` (prod).
_expand_sans() {
    local s="$1" v
    for v in BASE_DOMAIN NIP_DOMAIN PROD_DOMAIN SERVER_IP LOCAL_IP; do
        s="${s//\$\{$v\}/${!v:-}}"
    done
    printf '%s' "$s"
}

# manifest_rows [issuer_filter]
# Emits one line per cert row as:  name<TAB>resolved-issuer<TAB>sans
# (sans kept comma-separated; downstream uses the SAN builders to split).
# Skips comments/blanks; trims each field; resolves the per-env issuer ('edge')
# BEFORE filtering, so `manifest_rows step-ca` includes edge rows in dev/staging
# and `manifest_rows letsencrypt` includes them in prod.
manifest_rows() {
    local issuer_filter="${1:-}"
    [[ -f "$MANIFEST_FILE" ]] || die "Manifest not found: $MANIFEST_FILE" \
        "Expected the cert manifest at ca/cert-manifest.conf (or set MANIFEST_FILE)."

    local line name issuer sans
    while IFS= read -r line || [[ -n "$line" ]]; do
        # strip comments / blanks
        case "$(_trim "$line")" in
            ''|'#'*) continue ;;
        esac
        # split on '|' into 3 fields
        IFS='|' read -r name issuer sans <<< "$line"
        name="$(_trim "$name")"
        issuer="$(_resolve_issuer "$(_trim "$issuer")")"
        sans="$(_expand_sans "$(_trim "$sans")")"

        [[ -z "$name" || -z "$issuer" || -z "$sans" ]] && {
            log_warn "Skipping malformed manifest line: $line"
            continue
        }
        if [[ -n "$issuer_filter" && "$issuer" != "$issuer_filter" ]]; then
            continue
        fi
        printf '%s\t%s\t%s\n' "$name" "$issuer" "$sans"
    done < "$MANIFEST_FILE"
}

# manifest_sans <cert-name> — echo the comma-separated SAN list for one cert,
# or exit non-zero if not found.
manifest_sans() {
    local want="$1" name issuer sans
    while IFS=$'\t' read -r name issuer sans; do
        if [[ "$name" == "$want" ]]; then
            printf '%s' "$sans"
            return 0
        fi
    done < <(manifest_rows)
    return 1
}

# _split_sans <comma-separated> — print one trimmed, non-empty SAN per line.
_split_sans() {
    local raw="$1" part
    local IFS=','
    for part in $raw; do
        part="$(_trim "$part")"
        [[ -n "$part" ]] && printf '%s\n' "$part"
    done
}

# sans_cn <comma-separated> — the CN = first SAN. Computed WITHOUT a pipe: a
# `_split_sans | head -n1` would SIGPIPE the producer under `set -o pipefail`
# (head closes the pipe after one line), aborting the caller with status 141.
sans_cn() {
    _trim "${1%%,*}"
}

# step_san_args <comma-separated> — build:  --san a --san b ...
step_san_args() {
    local san out=""
    while IFS= read -r san; do
        out+=" --san $san"
    done < <(_split_sans "$1")
    printf '%s' "${out# }"
}

# certbot_d_args <comma-separated> — build:  -d a -d b ...
certbot_d_args() {
    local san out=""
    while IFS= read -r san; do
        out+=" -d $san"
    done < <(_split_sans "$1")
    printf '%s' "${out# }"
}

# ------------------------------------------------------------------------------
# Privilege detection + dependency auto-install — so a fresh box "just works".
# ------------------------------------------------------------------------------

# Capture the invoking command line so a `sudo` hint can reproduce it exactly.
# Each script calls `ca_capture_argv "$@"` as its first action (before arg
# parsing consumes "$@").
CA_ARGV=()
ca_capture_argv() { CA_ARGV=("$@"); }

# is_root — true when running as root (EUID 0).
is_root() { [[ "${EUID:-$(id -u)}" -eq 0 ]]; }

# require_root_for_install <reason> — proceed if root; else die with the EXACT
# `sudo -E` re-run (so DO_API_TOKEN / LE_DOMAIN / POLARI_CA_* carry through).
# A no-op in dry-run (nothing is actually installed there).
require_root_for_install() {
    local reason="${1:-installing system dependencies}"
    [[ "$DRY_RUN" == "true" ]] && return 0
    is_root && return 0
    die "Root is required for $reason — you are not running as root." \
        "Re-run with sudo (-E keeps your env: DO_API_TOKEN, LE_DOMAIN, POLARI_CA_*):" \
        "  sudo -E bash $0 ${CA_ARGV[*]:-}"
}

# When invoked via sudo, hand newly-created CA artifacts back to the real user
# so they aren't stuck root-owned. No-op when not under sudo / in dry-run.
reclaim_sudo_ownership() {
    [[ "$DRY_RUN" == "true" ]] && return 0
    [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]] || return 0
    local path
    for path in "$@"; do
        [[ -e "$path" ]] && chown -R "$SUDO_USER" "$path" 2>/dev/null || true
    done
}

# detect_pkg_manager — echo the first supported package manager found.
detect_pkg_manager() {
    local pm
    for pm in apt-get dnf yum pacman zypper brew; do
        command -v "$pm" >/dev/null 2>&1 && { echo "$pm"; return 0; }
    done
    echo "unknown"
}

# pkg_install <pkg...> — install OS packages via the detected manager.
pkg_install() {
    local pm; pm="$(detect_pkg_manager)"
    case "$pm" in
        apt-get) run apt-get update -qq
                 run env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
        dnf)     run dnf install -y "$@" ;;
        yum)     run yum install -y "$@" ;;
        pacman)  run pacman -Sy --noconfirm "$@" ;;
        zypper)  run zypper --non-interactive install "$@" ;;
        brew)    run brew install "$@" ;;
        *) die "No supported package manager (apt/dnf/yum/pacman/zypper/brew) found." \
               "Install these manually, then re-run: $*" ;;
    esac
}

# _pkg_name <logical> — map a logical name to the detected manager's package
# name (only where it actually differs across distros).
_pkg_name() {
    local logical="$1" pm; pm="$(detect_pkg_manager)"
    case "$logical:$pm" in
        dig:apt-get|dig:pacman)     echo "dnsutils" ;;
        dig:dnf|dig:yum|dig:zypper) echo "bind-utils" ;;
        dig:brew)                   echo "bind" ;;
        *)                          echo "$logical" ;;
    esac
}

# ensure_basic_tool <command> [logical_pkg] — ensure a simple OS tool exists.
ensure_basic_tool() {
    local cmd="$1" logical="${2:-$1}"
    command -v "$cmd" >/dev/null 2>&1 && return 0
    log "Missing '$cmd' — installing."
    require_root_for_install "installing '$cmd'"
    pkg_install "$(_pkg_name "$logical")"
}

# ensure_step — the Smallstep `step` CLI + `step-ca` server. apt via the
# official Smallstep repo; other distros get the documented install URL.
ensure_step() {
    command -v step >/dev/null 2>&1 && command -v step-ca >/dev/null 2>&1 && return 0
    log "Missing Smallstep 'step'/'step-ca' — installing."
    require_root_for_install "installing the Smallstep step CLI + step-ca"
    local pm; pm="$(detect_pkg_manager)"
    if [[ "$pm" == "apt-get" ]]; then
        run bash -c 'curl -fsSL https://packages.smallstep.com/keys/apt/repo-signing-key.gpg -o /etc/apt/trusted.gpg.d/smallstep.asc'
        run bash -c "echo 'deb [signed-by=/etc/apt/trusted.gpg.d/smallstep.asc] https://packages.smallstep.com/stable/debian debs main' > /etc/apt/sources.list.d/smallstep.list"
        run apt-get update -qq
        run env DEBIAN_FRONTEND=noninteractive apt-get install -y step-cli step-ca
    else
        die "Automatic Smallstep install is wired for apt only (found: $pm)." \
            "Install manually:" \
            "  step CLI: https://smallstep.com/docs/step-cli/installation" \
            "  step-ca:  https://smallstep.com/docs/step-ca/installation"
    fi
}

# ensure_certbot_do — certbot + the DigitalOcean DNS-01 plugin.
ensure_certbot_do() {
    local need=()
    command -v certbot >/dev/null 2>&1 || need+=("certbot")
    if command -v certbot >/dev/null 2>&1; then
        certbot plugins 2>/dev/null | grep -q "dns-digitalocean" || need+=("dns-plugin")
    else
        need+=("dns-plugin")
    fi
    [[ ${#need[@]} -eq 0 ]] && return 0
    log "Missing certbot / DigitalOcean DNS plugin — installing."
    require_root_for_install "installing certbot + the DigitalOcean DNS plugin"
    local pm; pm="$(detect_pkg_manager)"
    case "$pm" in
        apt-get) run apt-get update -qq
                 run env DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-dns-digitalocean ;;
        dnf|yum) pkg_install certbot python3-certbot-dns-digitalocean ;;
        *) die "Automatic certbot+DO-plugin install is wired for apt/dnf (found: $pm)." \
               "Install manually: https://certbot.eff.org + the certbot-dns-digitalocean plugin." ;;
    esac
}

# ensure_dependencies <tool...> — make each listed dependency available, auto-
# installing what's missing. Root is required ONLY when something must be
# installed (in dry-run we report but install nothing). Tokens:
#   step | certbot-do | openssl | curl | dig
ensure_dependencies() {
    local tool
    for tool in "$@"; do
        case "$tool" in
            step)         ensure_step ;;
            certbot-do)   ensure_certbot_do ;;
            openssl|curl) ensure_basic_tool "$tool" ;;
            dig)          ensure_basic_tool dig dig ;;
            *) log_warn "ensure_dependencies: unknown tool '$tool' (skipped)" ;;
        esac
    done
}
