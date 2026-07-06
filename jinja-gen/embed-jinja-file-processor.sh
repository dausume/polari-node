#!/bin/bash
# /embed-jinja/ansible-orchestration/embed-jinja-file-processor.sh
# Usage: ./embed-jinja-file-processor.sh <project_dir> <input_file_path>
set -euo pipefail

PROJECT_DIR="${1:-}"
INPUT_FILE="${2:-}"

[[ -z "$PROJECT_DIR" || -z "$INPUT_FILE" ]] && { echo "Usage: $0 <project_dir> <input_file>"; exit 2; }
[[ ! -d "$PROJECT_DIR" ]] && { echo "Error: project dir '$PROJECT_DIR' not found" >&2; exit 1; }
[[ ! -f "$INPUT_FILE" ]] && { echo "Error: input file '$INPUT_FILE' not found" >&2; exit 1; }

# Normalize to absolute
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
case "$INPUT_FILE" in
  /*) : ;;
  *)  INPUT_FILE="$(cd "$(dirname "$INPUT_FILE")" && pwd)/$(basename "$INPUT_FILE")" ;;
esac

# Relative path from the project root
case "$INPUT_FILE" in
  "$PROJECT_DIR"/*) REL="${INPUT_FILE#"$PROJECT_DIR/"}" ;;
  *) REL="$(basename "$INPUT_FILE")" ;;
esac

OUT_FILE="$PROJECT_DIR/jinja-templates/$REL.j2"
OUT_DIR="$(dirname "$OUT_FILE")"
mkdir -p "$OUT_DIR"

# ----- file-type → comment schema -----
HASH_FILE_TYPES="*.py,*.sh,Dockerfile,Dockerfile.*,docker-compose,docker-compose.*,*.yml,*.yaml,*.env,*.ini,*.conf,*.txt"
SLASH_FILE_TYPES="*.js,*.ts,*.java,*.go,*.c,*.cpp,*.h,*.hpp,*.proto"
TAG_FILE_TYPES="*.html,*.htm,*.xml,*.vue,*.svelte"

fname="$(basename "$INPUT_FILE")"

match_glob() {
  local name="$1" globs="$2"
  IFS=',' read -r -a arr <<<"$globs"
  for g in "${arr[@]}"; do
    g="${g#"${g%%[![:space:]]*}"}"; g="${g%"${g##*[![:space:]]}"}"
    [[ -z "$g" ]] && continue
    if [[ "$g" == *'*'* || "$g" == *'?'* || "$g" == *'['* ]]; then
      [[ "$name" == $g ]] && return 0
    else
      [[ "$name" == "$g" || "${name##*.}" == "$g" ]] && return 0
    fi
  done
  return 1
}

comment_schema="hash"
if   match_glob "$fname" "$SLASH_FILE_TYPES"; then comment_schema="slash"
elif match_glob "$fname" "$TAG_FILE_TYPES";   then comment_schema="tag"
fi

# ----- transform -----
inside_block=false
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT

while IFS= read -r line || [[ -n "$line" ]]; do
  norm="$(echo "$line" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"

  case "$comment_schema" in
    hash)
      if [[ "$norm" == "#start-jinja" || "$norm" == "#jinja-start" ]]; then inside_block=true;  continue; fi
      if [[ "$norm" == "#end-jinja"   || "$norm" == "#jinja-end"   ]]; then inside_block=false; continue; fi

      if $inside_block; then
        # drop embedded comments starting with '##'
        [[ "$line" =~ ^[[:space:]]*## ]] && continue
        # strip one leading "# " (preserve indent) to surface Jinja/code
        if [[ "$line" =~ ^([[:space:]]*)#\ (.*)$ ]]; then
          printf "%s%s\n" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" >> "$tmp"
        else
          printf "%s\n" "$line" >> "$tmp"
        fi
      else
        printf "%s\n" "$line" >> "$tmp"
      fi
      ;;
    slash)
      if [[ "$norm" == "//start-jinja" || "$norm" == "//jinja-start" ]]; then inside_block=true;  continue; fi
      if [[ "$norm" == "//end-jinja"   || "$norm" == "//jinja-end"   ]]; then inside_block=false; continue; fi

      if $inside_block; then
        [[ "$line" =~ ^[[:space:]]*/// ]] && continue
        if [[ "$line" =~ ^([[:space:]]*)//\ (.*)$ ]]; then
          printf "%s%s\n" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" >> "$tmp"
        else
          printf "%s\n" "$line" >> "$tmp"
        fi
      else
        printf "%s\n" "$line" >> "$tmp"
      fi
      ;;
    tag)
      if [[ "$line" =~ \<\!\-\-[[:space:]]*(start-jinja|jinja-start)[[:space:]]*\-\-\> ]]; then inside_block=true;  continue; fi
      if [[ "$line" =~ \<\!\-\-[[:space:]]*(end-jinja|jinja-end)[[:space:]]*\-\-\>     ]]; then inside_block=false; continue; fi

      if $inside_block; then
        [[ "$line" =~ \<\!\-\-[[:space:]]*## ]] && continue
        if [[ "$line" =~ ^([[:space:]]*)\<\!\-\-[[:space:]]*#[[:space:]]*(.*?)[[:space:]]*\-\-\>[[:space:]]*$ ]]; then
          printf "%s%s\n" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" >> "$tmp"
        else
          printf "%s\n" "$line" >> "$tmp"
        fi
      else
        printf "%s\n" "$line" >> "$tmp"
      fi
      ;;
  esac
done < "$INPUT_FILE"

# enforce a consistent permission template
#chmod 0644 "$OUTPUT_FILE"

# Write only if changed
if [[ ! -f "$OUT_FILE" ]] || ! cmp -s "$tmp" "$OUT_FILE"; then
  mv "$tmp" "$OUT_FILE"
  echo "updated template: $OUT_FILE"
else
  : # no change
fi
