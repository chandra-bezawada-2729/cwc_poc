#!/usr/bin/env bash
# Wrapper for the application stack, so the long compose invocation is typed
# once here rather than every time.
#
#   ./app.sh up -d --build
#   ./app.sh ps
#   ./app.sh logs -f backend ai
#   ./app.sh restart backend
#
# Why the two extra flags: Docker Compose takes the project directory and the
# .env location from the FIRST -f file, which is the repository root. Without
# --env-file and --project-directory it reads no .env at all and every
# variable comes out empty.
set -euo pipefail
cd "$(dirname "$0")"
exec docker compose \
  --env-file .env \
  --project-directory ../.. \
  -f ../../docker-compose.yml \
  -f compose.app.yml \
  "$@"
