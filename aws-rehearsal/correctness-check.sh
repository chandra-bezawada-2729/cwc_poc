#!/usr/bin/env bash
# Mirrors correctness-check.sh from the guide.
# Fires 8 identical requests at once and checks every answer. Expected: 0 wrong.
# A one-at-a-time test will NOT catch concurrency faults - that is the whole point.
#   usage: ./correctness-check.sh [rounds]     default 5 rounds x 8 parallel
set -euo pipefail
cd "$(dirname "$0")"
source .env
export URL=${URL:-http://localhost:30000} API_KEY SERVED_NAME
ROUNDS=${1:-5}

ask() {
  curl -s "$URL/v1/chat/completions" \
    -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
    -d '{"model":"'"$SERVED_NAME"'","temperature":0,"max_tokens":64,
         "chat_template_kwargs":{"enable_thinking":false},
         "messages":[{"role":"user","content":"Sort ascending, comma-separated, numbers only: 42, 7, 19, 3, 88, 25"}]}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["choices"][0]["message"]["content"].replace(" ","").strip().rstrip("."))'
}
export -f ask

total=0; bad=0
for r in $(seq 1 "$ROUNDS"); do
  while read -r ans; do
    total=$((total+1))
    [ "$ans" = "3,7,19,25,42,88" ] || { bad=$((bad+1)); echo "  wrong: $ans"; }
  done < <(seq 1 8 | xargs -P 8 -I{} bash -c ask)
done
echo "$bad wrong out of $total"
[ "$bad" -eq 0 ]
