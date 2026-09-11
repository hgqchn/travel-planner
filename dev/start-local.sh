#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
python_bin=${TRIP_PYTHON:-$(command -v python3)}
"$python_bin" -c 'import sys; print("Python:", sys.executable); assert sys.version_info >= (3,9), "Python 3.9+ required"'
if [ ! -f data/preview.env ]; then
    echo '请先按 LOCAL_PREVIEW.md 创建 data/preview.env。' >&2
    exit 1
fi
set -a
. ./data/preview.env
if [ -f data/deepseek.env ]; then . ./data/deepseek.env; fi
if [ -f data/amap.env ]; then . ./data/amap.env; fi
set +a
export TRIP_HOST=0.0.0.0
export TRIP_PORT=${TRIP_PORT:-8000}
export TRIP_DATA_DIR="$project_dir/data/preview"
export TRIP_PUBLIC_ORIGIN=""
export TRIP_COOKIE_SECURE=0
exec "$python_bin" -B server.py
