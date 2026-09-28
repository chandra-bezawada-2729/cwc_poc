#!/usr/bin/env bash
# Wipe the application back to empty and start fresh. For repeated demo runs.
#
#   ./reset-demo.sh                                      just reset
#   ./reset-demo.sh --from /home/automation/inboundfaxes/212-334-6887_Walker
#   ./reset-demo.sh --from <dir> --yes                   no confirmation prompt
#
# What it does NOT touch: the model server and the gateway. Those are a
# separate Compose project. Restarting the model costs ~9 minutes of torch
# compile for nothing, so this script leaves them alone.
#
# Why the database is dropped rather than emptied: cwc.ingest_ledger records a
# content hash for every fax ever ingested, so the same file is never processed
# twice. Keep the database and re-copied faxes are silently skipped as
# duplicates - which looks exactly like a broken scanner.
set -euo pipefail
cd "$(dirname "$0")"

SRC=""
ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) SRC="${2:?--from needs a directory}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -f .env ] || { echo "ERROR: .env not found in $(pwd)" >&2; exit 1; }
set -a; . ./.env; set +a
DATA="${DATA_DIR:-/srv/cwc-data}"

echo "About to erase:"
echo "  - the application database (all faxes, classifications, feedback)"
echo "  - every file under $DATA/{Inbound,incoming,Outbound}"
[ -n "$SRC" ] && echo "Then copy *.pdf from: $SRC"
echo

if [ "$ASSUME_YES" -ne 1 ]; then
  printf "Type 'reset' to continue: "
  read -r answer
  [ "$answer" = "reset" ] || { echo "Cancelled."; exit 1; }
fi

# The AI service logs to a file inside the container, on a volume that
# 'down -v' destroys. Keep a copy first - this is the only record of why a
# run failed, and it has been lost this way before.
STAMP=$(date +%Y%m%d-%H%M%S)
if docker inspect cwc-ai-1 >/dev/null 2>&1; then
  echo "==> saving AI service log to ~/ai-diag-$STAMP.log"
  ./app.sh exec -T ai sh -c 'cat /app/logs/*.log' > "$HOME/ai-diag-$STAMP.log" 2>/dev/null \
    || echo "    (no log to save)"
fi

echo "==> stopping the application and dropping its database"
./app.sh down -v

echo "==> clearing $DATA"
# Inbound/* also removes the scanner's _processed and _failed subfolders.
sudo rm -rf "${DATA:?}"/Inbound/* "${DATA:?}"/incoming/* "${DATA:?}"/Outbound/*
sudo mkdir -p "$DATA"/Inbound "$DATA"/incoming "$DATA"/Outbound
# Containers run as UID 10001. On a folder they cannot write, the scanner
# fails silently - no error, faxes simply sit in Inbound forever.
sudo chown -R 10001:999 "$DATA"

left=$(find "$DATA" -type f | wc -l)
[ "$left" -eq 0 ] || { echo "ERROR: $left file(s) still under $DATA" >&2; exit 1; }
echo "    empty"

echo "==> starting the application"
./app.sh up -d

echo "==> waiting for the backend"
for i in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' cwc-backend-1 2>/dev/null || echo starting)
  [ "$state" = "healthy" ] && break
  sleep 2
done
[ "$state" = "healthy" ] && echo "    backend healthy" || echo "    WARNING: backend not healthy yet - check ./app.sh ps"

if [ -n "$SRC" ]; then
  n=$(find "$SRC" -maxdepth 1 -iname '*.pdf' | wc -l)
  echo "==> copying $n fax(es) from $SRC"
  sudo cp "$SRC"/*.pdf "$DATA"/Inbound/
  sudo chown -R 10001:999 "$DATA"
  echo "    $(ls "$DATA"/Inbound | wc -l) file(s) in Inbound"
fi

echo
echo "Done. Watch it work with:"
echo "  ./app.sh logs -f backend ai"
echo "  watch -n 5 'ls $DATA/Inbound | wc -l; find $DATA/Outbound -type f | wc -l'"
