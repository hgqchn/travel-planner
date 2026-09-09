#!/bin/bash
set -euo pipefail
base=/opt/travel-planner
stamp=$(date -u +%Y%m%dT%H%M%S)-$$
docker compose --env-file "$base/current.env" -f "$base/deployment/compose.yaml" \
    exec -T app python server.py --backup "/backups/trip-$stamp.db"
