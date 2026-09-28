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

# The engine key must be substituted HERE. compose.secure.yml passes API_KEY
# into the container as an environment variable, but the nginx image only runs
# envsubst on /etc/nginx/templates/ - and this file is mounted straight into
# /etc/nginx/conf.d/. Left unsubstituted, nginx reads ${API_KEY} as a variable
# reference, fails with `unknown "api_key" variable`, and restart: unless-stopped
# hides it as a crash loop. Every caller then gets a connection error.
set -a; . ./.env; set +a
: "${API_KEY:?API_KEY is not set in .env - the gateway cannot reach the engine}"

python3 - "$block" "$API_KEY" <<'PY'
import sys, pathlib
block, api_key = sys.argv[1], sys.argv[2]
tpl = pathlib.Path("templates/llm.conf.template").read_text()
out = tpl.replace("__CALLER_KEYS__", block).replace("${API_KEY}", api_key)
if "${" in out:
    sys.exit("Unsubstituted placeholder left in the rendered config: " +
             out[out.index("${"):out.index("${") + 40])
pathlib.Path("conf.d/llm.conf").write_text(out)
PY

echo "conf.d/llm.conf written with $(wc -l < "$KEYS") caller(s):"
awk '{ printf "  - %s\n", $2 }' "$KEYS"
echo "Now: docker compose -f compose.model.yml -f compose.secure.yml up -d"
