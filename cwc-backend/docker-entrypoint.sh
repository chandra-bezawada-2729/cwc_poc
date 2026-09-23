#!/bin/sh
# The fax folders live on the shared /data volume. The Inbound folder must exist
# before Spring starts (cwc.ingestion.inbound.require-path-at-startup=true).
set -e
mkdir -p /data/Inbound /data/incoming /data/Outbound
exec java $JAVA_OPTS -jar /app/cwc-backend.jar "$@"
