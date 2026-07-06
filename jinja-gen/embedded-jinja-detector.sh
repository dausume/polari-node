#!/bin/bash

# /embed-jinja/ansible-orchestration/embedded-jinja-detector.sh
# Usage: ./embedded-jinja-detector.sh <project_dir> [exclude_dir ...]
set -euo pipefail
export LC_ALL=C

PROJECT_DIR="${1:-}"; shift || true
[[ -z "$PROJECT_DIR" || ! -d "$PROJECT_DIR" ]] && { echo "Error: valid project_dir required." >&2; exit 1; }

EXCLUDES=( "$@" )  # absolute or relative to PROJECT_DIR


HASH_FILE_TYPES="*.py,*.sh,Dockerfile,Dockerfile.*,docker-compose,docker-compose.*,*.yml,*.yaml,*.env,*.ini,*.conf,*.txt"
SLASH_FILE_TYPES="*.js,*.ts,*.java,*.go,*.c,*.cpp,*.h,*.hpp,*.proto"
TAG_FILE_TYPES="*.html,*.htm,*.xml,*.vue,*.svelte"

pat_hash='^[[:space:]]{0,4}#.*(start-jinja|jinja-start)[[:space:]]*$'
pat_slash='^[[:space:]]{0,4}//.*(start-jinja|jinja-start)[[:space:]]*$'
pat_tag='<!--[[:space:]]*(start-jinja|jinja-start)[[:space:]]*-->'



csv_each() { IFS=',' read -r -a arr <<<"$1"; for p in "${arr[@]}"; do
  p="${p#"${p%%[![:space:]]*}"}"; p="${p%"${p##*[![:space:]]}"}"
  [[ -n "$p" ]] && printf '%s\n' "$p"
done; }

#echo "got past csv_each"

# Build prune expr
PRUNE=()
for d in "${EXCLUDES[@]}"; do
  [[ -z "$d" ]] && continue
  [[ "$d" != /* ]] && d="$PROJECT_DIR/${d#./}"
  d="${d%/}"
  PRUNE+=( -path "$d" -prune -o )
done

#echo "got past prune"

run_find() {  # $1 pattern
  if ((${#PRUNE[@]})); then
    find "$PROJECT_DIR" "${PRUNE[@]}" -type f -name "$1" -print0
  else
    find "$PROJECT_DIR" -type f -name "$1" -print0
  fi
}

#echo "got past run_find"

# HASH
while IFS= read -r pat; do
  run_find "$pat" \
  | while IFS= read -r -d $'\0' f; do
      grep -Eqi "$pat_hash" "$f" && printf '%s\n' "$f"
    done \
  || true
done < <(csv_each "$HASH_FILE_TYPES")

#echo "got past hash"

# SLASH
while IFS= read -r pat; do
  run_find "$pat" \
  | while IFS= read -r -d $'\0' f; do
      grep -Eqi "$pat_slash" "$f" && printf '%s\n' "$f"
    done \
  || true
done < <(csv_each "$SLASH_FILE_TYPES")

#echo "got past slash"

# TAG
while IFS= read -r pat; do
  run_find "$pat" \
  | while IFS= read -r -d $'\0' f; do
      grep -Eqi "$pat_tag" "$f" && printf '%s\n' "$f"
    done \
  || true
done < <(csv_each "$TAG_FILE_TYPES")



#echo "got past tag"
