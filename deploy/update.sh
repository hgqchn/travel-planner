#!/bin/bash
# Run as root on the deployed server. Optional argument: a Git branch/tag/commit.
set -euo pipefail
umask 077
base=/opt/travel-planner
exec 9>"$base/deploy.lock"
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
cd "$base/repo"
if [ -n "${TRIP_GIT_BUNDLE:-}" ]; then
    git bundle verify "$TRIP_GIT_BUNDLE"
    git fetch "$TRIP_GIT_BUNDLE" main:refs/remotes/origin/main
else
    git -c http.connectTimeout=15 -c http.lowSpeedLimit=1024 -c http.lowSpeedTime=30 fetch --prune origin
fi
ref=${1:-origin/HEAD}
commit=$(git rev-parse --verify "${ref}^{commit}")
tag="$(date -u +%Y%m%dT%H%M%SZ)-${commit:0:12}"
release="$base/releases/$tag"
install -d -m 755 "$release"
git archive "$commit" | tar -x -C "$release"
chmod -R a+rX "$release"
docker build -f "$base/deployment/Dockerfile" -t "travel-planner:$tag" "$release"
# Verify packaged imports and seed/catalog files before touching the live app.
docker run --rm --network none --read-only --tmpfs /tmp \
    --entrypoint python "travel-planner:$tag" -c \
    'import server, ai_service, project_store, place_cache, backup_all; import json; from pathlib import Path; [json.loads(p.read_text()) for p in Path("/app").rglob("*.json")]; print("Image preflight passed")'

previous=''
if [ -f "$base/current.env" ]; then
    previous=$(cat "$base/current.env")
fi
if [ -f /var/lib/travel-planner/trip.db ]; then
    "$base/deployment/backup.sh"
fi
printf 'TRIP_IMAGE_TAG=%s\n' "$tag" > "$base/candidate.env"
dc=(docker compose --env-file "$base/candidate.env" -f "$base/deployment/compose.yaml")
if "${dc[@]}" up -d --no-build --wait --wait-timeout 90; then
    printf '%s\n' "$previous" > "$base/previous.env"
    mv "$base/candidate.env" "$base/current.env"
    printf '%s\n' "$commit" > "$base/current.commit"
    curl -fsS http://127.0.0.1:18081/api/health
    printf '\nDeployed %s\n' "$tag"
else
    echo 'Deployment failed. Inspect docker compose logs. The database may have migrated; do not automatically roll back code without checking it.' >&2
    exit 1
fi
