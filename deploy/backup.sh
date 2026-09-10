#!/bin/bash
set -euo pipefail
base=/opt/travel-planner
stamp=$(date -u +%Y%m%dT%H%M%S)-$$
docker compose --env-file "$base/current.env" -f "$base/deployment/compose.yaml" \
    exec -T app sh -c '
        if [ -f backup_all.py ]; then
            exec python backup_all.py --output "/backups/trip-$1"
        else
            exec python server.py --backup "/backups/trip-$1.db"
        fi
    ' sh "$stamp"
