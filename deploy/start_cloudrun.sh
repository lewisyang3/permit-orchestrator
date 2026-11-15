#!/usr/bin/env bash
# ==============================================================================
# Permit Orchestrator - Cloud Run Multi-Service Startup Runner
# ==============================================================================
# Starts all four municipal department microservices on loopback ports:
#   - Zoning:     http://127.0.0.1:8001
#   - Fire:       http://127.0.0.1:8002
#   - Water:      http://127.0.0.1:8003
#   - Structural: http://127.0.0.1:8004
# alongside the core orchestrator binding to Cloud Run's $PORT (default: 8080).
# ==============================================================================

set -euo pipefail

# Path and environment resolution
if [ -d "/app/.venv/bin" ]; then
    export PATH="/app/.venv/bin:$PATH"
elif [ -d ".venv/bin" ]; then
    export PATH="$(pwd)/.venv/bin:$PATH"
fi

PORT="${PORT:-8080}"
RULES_DIR="rules"
if [ -d "/app/rules" ]; then
    RULES_DIR="/app/rules"
fi

# Ensure database directory and path
if [ -d "/data" ] && [ -w "/data" ]; then
    export DATABASE_PATH="${DATABASE_PATH:-/data/permits.db}"
else
    export DATABASE_PATH="${DATABASE_PATH:-permits.db}"
fi

# Loopback endpoints for internal department fan-out
export ZONING_URL="${ZONING_URL:-http://127.0.0.1:8001}"
export FIRE_URL="${FIRE_URL:-http://127.0.0.1:8002}"
export WATER_URL="${WATER_URL:-http://127.0.0.1:8003}"
export STRUCTURAL_URL="${STRUCTURAL_URL:-http://127.0.0.1:8004}"

PIDS=()

cleanup() {
    echo "Terminating all background services..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null || true
    echo "All services stopped."
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT

echo "=========================================================="
echo " Starting Permit Orchestrator Full Demo Stack"
echo "=========================================================="
echo " Orchestrator Port : ${PORT}"
echo " Database Path     : ${DATABASE_PATH}"
echo " Zoning Service    : ${ZONING_URL} (port 8001)"
echo " Fire Service      : ${FIRE_URL} (port 8002)"
echo " Water Service     : ${WATER_URL} (port 8003)"
echo " Structural Service: ${STRUCTURAL_URL} (port 8004)"
echo "=========================================================="

# 1. Start Zoning Department Service (Port 8001)
DEPARTMENT=zoning \
PORT="8001" \
RULE_FILE="${RULES_DIR}/zoning.yaml" \
python -m uvicorn departments.app:app --host 0.0.0.0 --port 8001 &
ZONING_PID=$!
PIDS+=("$ZONING_PID")

# 2. Start Fire Department Service (Port 8002)
DEPARTMENT=fire \
PORT="8002" \
RULE_FILE="${RULES_DIR}/fire.yaml" \
python -m uvicorn departments.app:app --host 0.0.0.0 --port 8002 &
FIRE_PID=$!
PIDS+=("$FIRE_PID")

# 3. Start Water Department Service (Port 8003)
DEPARTMENT=water \
PORT="8003" \
RULE_FILE="${RULES_DIR}/water.yaml" \
python -m uvicorn departments.app:app --host 0.0.0.0 --port 8003 &
WATER_PID=$!
PIDS+=("$WATER_PID")

# 4. Start Structural Department Service (Port 8004)
DEPARTMENT=structural \
PORT="8004" \
RULE_FILE="${RULES_DIR}/structural.yaml" \
python -m uvicorn departments.app:app --host 0.0.0.0 --port 8004 &
STRUCTURAL_PID=$!
PIDS+=("$STRUCTURAL_PID")

echo "Background department microservices launched with PIDs: ${PIDS[*]}"

# Give department microservices a moment to initialize before orchestrator serves traffic
sleep 1

# 5. Start Core Orchestrator (binding to Cloud Run $PORT / 8080)
python -m uvicorn orchestrator.app:app --host 0.0.0.0 --port "${PORT}" &
ORCH_PID=$!
PIDS+=("$ORCH_PID")

echo "Permit Orchestrator started on port ${PORT} (PID ${ORCH_PID})"

# Wait on orchestrator process; if it exits, cleanup all child processes
wait "$ORCH_PID"
cleanup
