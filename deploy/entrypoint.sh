#!/bin/sh
set -e

# Start municipal department evaluator microservices in background on localhost
PORT=8001 DEPARTMENT=zoning python -m uvicorn departments.app:app --host 127.0.0.1 --port 8001 &
PORT=8002 DEPARTMENT=fire python -m uvicorn departments.app:app --host 127.0.0.1 --port 8002 &
PORT=8003 DEPARTMENT=water python -m uvicorn departments.app:app --host 127.0.0.1 --port 8003 &
PORT=8004 DEPARTMENT=structural python -m uvicorn departments.app:app --host 127.0.0.1 --port 8004 &

# Export default endpoint URLs to connect orchestrator to internal department processes
export ZONING_URL="${ZONING_URL:-http://127.0.0.1:8001}"
export FIRE_URL="${FIRE_URL:-http://127.0.0.1:8002}"
export WATER_URL="${WATER_URL:-http://127.0.0.1:8003}"
export STRUCTURAL_URL="${STRUCTURAL_URL:-http://127.0.0.1:8004}"
export DATABASE_PATH="${DATABASE_PATH:-/tmp/permits.db}"
export UPLOADS_DIR="${UPLOADS_DIR:-/tmp/uploads}"

# Start orchestrator in foreground binding to Cloud Run's injected $PORT (default: 8080)
exec python -m uvicorn orchestrator.app:app --host 0.0.0.0 --port "${PORT:-8080}"
