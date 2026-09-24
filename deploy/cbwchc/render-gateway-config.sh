#!/usr/bin/env bash
# Writes the gateway configuration from the template plus the caller keys.
#
# CORRECTION 2 (found during rehearsal): the vendor design keeps live keys
# inside a version-controlled configuration file. Re-deploying that file
# replaces them with placeholders and locks every caller out — and nginx
# starts normally and logs nothing, so the cause is not obvious.
#
# Here the keys live in keys/callers.txt, which is never committed, and are
# inserted at deploy time. Re-running this script is always safe.
#
#   keys/callers.txt format, one per line:   <64-hex-key> <caller-name>
set -euo pipefail
cd "$(dirname "$0")"

KEYS=keys/callers.txt
[ -f "$KEYS" ] || { echo "Missing $KEYS"; exit 1; }

mkdir -p conf.d
block=$(awk 'NF >= 2 { printf "    \"%s\" \"%s\";\n", $1, $2 }' "$KEYS")
[ -n "$block" ] || { echo "$KEYS has no valid entries"; exit 1; }

python3 - "$block" <<'PY'
import sys, pathlib
block = sys.argv[1]
tpl = pathlib.Path("templates/llm.conf.template").read_text()
pathlib.Path("conf.d/llm.conf").write_text(tpl.replace("__CALLER_KEYS__", block))
PY

echo "conf.d/llm.conf written with $(wc -l < "$KEYS") caller(s):"
awk '{ printf "  - %s\n", $2 }' "$KEYS"
echo "Now: docker compose -f compose.model.yml -f compose.secure.yml up -d"
