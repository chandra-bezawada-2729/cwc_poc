#!/usr/bin/env bash
# Write the reviewer feedback to a CSV file on this server.
#
# Staff mark each processed fax right or wrong from its detail page, and a
# wrong one carries a written summary of what went wrong. This pulls all of
# that out so it can be reviewed away from the machine and used to correct the
# routing rules.
#
#   ./export-feedback.sh              everything
#   ./export-feedback.sh DOWN         only the problems
#   ./export-feedback.sh DOWN /tmp    somewhere other than ./feedback-exports
#
# Then fetch it:
#   scp -i <key> ubuntu@<server>:/opt/cwc/deploy/cbwchc/feedback-exports/*.csv .
#
# The file contains free text written while looking at patient documents, so
# treat it as patient data: transfer it the same way you would a fax.
set -euo pipefail
cd "$(dirname "$0")"

VERDICT="${1:-}"
OUTDIR="${2:-./feedback-exports}"
mkdir -p "$OUTDIR"

STAMP=$(date +%Y%m%d-%H%M)
SUFFIX=$([ -n "$VERDICT" ] && echo "-${VERDICT,,}" || echo "")
OUT="$OUTDIR/fax-feedback${SUFFIX}-${STAMP}.csv"

# Straight through the web container, so this works whether or not the
# backend port is reachable from the host.
QS=$([ -n "$VERDICT" ] && echo "?verdict=$VERDICT" || echo "")
docker exec cwc-web-1 curl -fsS "http://backend:8080/api/feedback/export.csv${QS}" > "$OUT"

ROWS=$(( $(wc -l < "$OUT") - 1 ))
echo "Wrote $ROWS row(s) to $OUT"

if [ "$ROWS" -gt 0 ]; then
  echo
  echo "Summary:"
  docker exec cwc-web-1 curl -fsS "http://backend:8080/api/feedback" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f"  total {d[\"total\"]}   correct {d[\"upCount\"]}   problems {d[\"downCount\"]}")' \
    2>/dev/null || true
fi
