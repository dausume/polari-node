#!/usr/bin/env bash
# Stage the cnt-engines build inputs into cnt-engines/vendor/ (gitignored).
# Source of truth = the host tool tree the cntfet module already uses
# (~/tools); a fresh machine downloads the same pinned artifacts.
#   ngspice-46 source : https://sourceforge.net/projects/ngspice/files/ng-spice-rework/46/ngspice-46.tar.gz
#   OpenVAF 23.5.0    : https://openvaf.semimod.de/ (x86_64 linux tarball;
#                       glibc-2.35-safe — NOT openvaf-reloaded)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
V="$HERE/vendor"
mkdir -p "$V"
TOOLS="${POLARI_TOOLS:-$HOME/tools}"

if [ ! -f "$V/ngspice.tar.gz" ]; then
    if [ -f "$TOOLS/ngspice.tar.gz" ]; then
        cp "$TOOLS/ngspice.tar.gz" "$V/ngspice.tar.gz"
    else
        curl -fL -o "$V/ngspice.tar.gz" \
          "https://sourceforge.net/projects/ngspice/files/ng-spice-rework/46/ngspice-46.tar.gz/download"
    fi
fi
if [ ! -f "$V/openvaf" ]; then
    if [ -f "$TOOLS/openvaf/openvaf" ]; then
        cp "$TOOLS/openvaf/openvaf" "$V/openvaf"
    else
        echo "openvaf binary not found at $TOOLS/openvaf/openvaf — download the" >&2
        echo "OpenVAF 23.5.0 linux tarball from https://openvaf.semimod.de/ and" >&2
        echo "place the 'openvaf' binary at $V/openvaf" >&2
        exit 1
    fi
fi
tar tzf "$V/ngspice.tar.gz" | grep -q '^ngspice-46/configure$' \
  || { echo "vendor/ngspice.tar.gz is not the ngspice-46 source tree" >&2; exit 1; }
chmod +x "$V/openvaf"
echo "vendor staged: $(ls -1 "$V" | tr '\n' ' ')"
