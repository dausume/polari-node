#!/usr/bin/env bash
# build.sh - osc-5: build prf-scope-engines:trixie. Stages vendor/ (the framework's instruments.custom emulator files - ONE source of truth)
# then `docker build` here, or on SCOPE_BUILD_HOST over ssh when set (the bridge-engines idiom). FRAMEWORK_MODULES overrides the module root.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
modules="${FRAMEWORK_MODULES:-$here/../polari-framework/modules}"
tag="${SCOPE_IMAGE:-prf-scope-engines:trixie}"
rm -rf "$here/vendor"
mkdir -p "$here/vendor/instruments/custom"
printf '"""vendored by prf-scope-engines/build.sh from polari-framework/modules/instruments - do not edit here."""\n' > "$here/vendor/instruments/__init__.py"
cp "$here/vendor/instruments/__init__.py" "$here/vendor/instruments/custom/__init__.py"
for f in scpi.py lineage.py sources.py emulator.py device_feed.py; do cp "$modules/instruments/custom/$f" "$here/vendor/instruments/custom/$f"; done
sha256sum "$here"/vendor/instruments/custom/*.py | sed 's#'"$here"'/##' > "$here/vendor/SOURCES.sha256"
if [ -n "${SCOPE_BUILD_HOST:-}" ]; then
    tar -C "$here" --exclude=build.sh --exclude=__pycache__ -cf - . | ssh "$SCOPE_BUILD_HOST" "docker build -t $tag -"
    ssh "$SCOPE_BUILD_HOST" "docker image inspect $tag --format 'image {{.Id}} {{.Size}} bytes'"
else
    docker build -t "$tag" "$here"
    docker image inspect "$tag" --format 'image {{.Id}} {{.Size}} bytes'
fi
