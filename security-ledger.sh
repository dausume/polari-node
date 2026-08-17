#!/bin/bash
# security-ledger.sh — deployment-security bookkeeping shared by the setup
# shells (staging-setup.sh / prod-setup.sh) and `pol security`.
#
# All security material (domain, passwords, certs) is put in AT DEPLOY TIME;
# this ledger records WHEN each credential artifact was last (re)generated so
# a deployment can detect existing material, flag anything older than
# SEC_STALE_DAYS (default 30), and an update run can keep-or-rotate
# deliberately instead of silently regenerating. Secret VALUES never enter
# the ledger — only names, timestamps, sources, and content fingerprints.
#
# Ledger: .generated/security-ledger.tsv (gitignored with .generated/).
# One row per artifact: name  epoch  iso8601  source  sha256-prefix

SEC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_LEDGER_FILE="${SEC_LEDGER_FILE:-$SEC_LIB_DIR/.generated/security-ledger.tsv}"
SEC_STALE_DAYS="${SEC_STALE_DAYS:-30}"

sec_fingerprint() { sha256sum "$1" 2>/dev/null | cut -c1-12; }

# ledger_stamp NAME PATH SOURCE — record that PATH was (re)written now.
ledger_stamp() {
    local name=$1 path=$2 src=${3:-manual} tmp
    mkdir -p "$(dirname "$SEC_LEDGER_FILE")"
    tmp="${SEC_LEDGER_FILE}.tmp"
    {
        [ -f "$SEC_LEDGER_FILE" ] && grep -v "^${name}$(printf '\t')" "$SEC_LEDGER_FILE" || true
        printf '%s\t%s\t%s\t%s\t%s\n' \
            "$name" "$(date +%s)" "$(date -Iseconds)" "$src" "$(sec_fingerprint "$path")"
    } > "$tmp"
    mv "$tmp" "$SEC_LEDGER_FILE"
}

ledger_row() { [ -f "$SEC_LEDGER_FILE" ] && grep "^${1}$(printf '\t')" "$SEC_LEDGER_FILE" | tail -1 || true; }

# ledger_age_days NAME PATH — whole days since last stamped write. Prefers
# the ledger stamp when its fingerprint still matches the file; falls back
# to file mtime for unstamped or out-of-band-edited files. Empty output =
# file missing.
ledger_age_days() {
    local name=$1 path=$2 row epoch fp now
    [ -f "$path" ] || return 0
    now=$(date +%s)
    row=$(ledger_row "$name")
    if [ -n "$row" ]; then
        epoch=$(printf '%s' "$row" | cut -f2)
        fp=$(printf '%s' "$row" | cut -f5)
        if [ "$fp" = "$(sec_fingerprint "$path")" ] && [ -n "$epoch" ]; then
            echo $(( (now - epoch) / 86400 )); return 0
        fi
    fi
    epoch=$(stat -c %Y "$path" 2>/dev/null || echo "$now")
    echo $(( (now - epoch) / 86400 ))
}

# sec_placeholders PATH — count secret-bearing keys whose value is a known
# dev default, a REPLACE_ME placeholder, or empty. Zero means the file looks
# like real deploy-time material. Only PASSWORD/SECRET/PASS keys are
# examined so ordinary empty config keys don't count.
sec_placeholders() {
    [ -f "$1" ] || { echo 0; return; }
    grep -E '^[A-Za-z_]*(PASSWORD|SECRET|PASS)[A-Za-z_]*=' "$1" 2>/dev/null \
      | grep -cE '=(admin|rootpassword|kcpassword|pscpassword|polaripassword|polari-file-store-password|REPLACE_ME[A-Za-z0-9_.-]*)$|=$' \
      || true
}

# sec_is_stale NAME PATH — exit 0 when the artifact is older than
# SEC_STALE_DAYS (missing files are not "stale", they are missing).
sec_is_stale() {
    local age; age=$(ledger_age_days "$1" "$2")
    [ -n "$age" ] && [ "$age" -gt "$SEC_STALE_DAYS" ]
}
