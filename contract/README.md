# Permit Orchestrator — Shared Interface Contract (Task T0)

> **Status: FROZEN.** Both Person A (Control Plane) and Person B (Data Plane / Trace UI) build against these specifications in parallel without breaking each other.

---

## 1. Directory Structure

```text
contract/
├── README.md                              # This document
├── models.py                              # Pydantic v2 models (for Python services)
├── types.ts                               # TypeScript types (for React frontend)
├── schemas/
│   ├── permit.schema.json                 # Permit application JSON schema
│   ├── department_request.schema.json     # Orchestrator -> Dept POST /evaluate
│   ├── department_response.schema.json    # Dept -> Orchestrator POST /evaluate response
│   ├── fault_control.schema.json          # Fault injection schema (POST /fault)
│   ├── trace_event.schema.json            # SSE & SQLite Trace Event schema
│   ├── permit_status_response.schema.json # Orchestrator GET /permits/{id} response
│   ├── submit_permit_response.schema.json # Orchestrator POST /submit response
│   ├── sse_done_event.schema.json         # SSE stream done event payload
│   └── admin_fault_response.schema.json   # Orchestrator GET /admin/fault response
└── fixtures/
    ├── permit_approved.json               # Test fixture: clean residential addition
    ├── permit_rejected_fire.json          # Test fixture: commercial unit failing fire
    ├── permit_timeout_offline.json        # Test fixture: warehouse for offline/retry demo
    ├── trace_events_approved.json         # Mock SSE stream for approved outcome
    ├── trace_events_rejected.json         # Mock SSE stream for rejected outcome
    └── trace_events_timeout.json          # Mock SSE stream for timeout/retry outcome
```

---

## 2. Ports and Networking Map

| Service | Owner | Port | Base URL (Local) | Base URL (Docker Compose) |
|---|---|---|---|---|
| **Orchestrator** | Person A | `8000` | `http://localhost:8000` | `http://orchestrator:8000` |
| **Zoning Service** | Person B | `8001` | `http://localhost:8001` | `http://zoning:8001` |
| **Fire Service** | Person B | `8002` | `http://localhost:8002` | `http://fire:8002` |
| **Water Service** | Person B | `8003` | `http://localhost:8003` | `http://water:8003` |
| **Structural Service** | Person B | `8004` | `http://localhost:8004` | `http://structural:8004` |
| **Trace UI** | Person B | `3000` | `http://localhost:3000` | `http://localhost:3000` |

---

## 3. Endpoints & Protocol Specifications

### 3.1 Department Services (`departments/`)
Each department microservice exposes:
- **`POST /evaluate`**:
  - Request: `DepartmentEvaluationRequest` (`contract/schemas/department_request.schema.json`)
  - Response: `DepartmentEvaluationResponse` (`contract/schemas/department_response.schema.json`)
  - Status: `200 OK` (whether `status: "APPROVED"` or `"REJECTED"`).
- **`POST /fault`**:
  - Request: `FaultControl` (`contract/schemas/fault_control.schema.json`)
  - Modes:
    - `"none"`: Standard execution.
    - `"offline"`: Returns `503 Service Unavailable`.
    - `"slow"`: Sleeps for `delay_ms` (default 3000ms > 2000ms timeout) before answering.
    - `"error"`: Returns `500 Internal Server Error`.
- **`GET /fault`**: Returns current fault mode.
- **`GET /health`**: Returns `{"status": "healthy", "department": "<name>"}`.

### 3.2 Orchestrator (`orchestrator/`)
- **`POST /submit`**:
  - Body: `Permit` JSON.
  - Response: `{"permit_id": "...", "trace_id": "...", "state": "RECEIVED", "stream_url": "/permits/{id}/stream"}` (`202 Accepted`).
- **`GET /permits/{permit_id}/stream`**:
  - Server-Sent Events (`text/event-stream`).
  - Wire format:
    - `event: trace_event\ndata: <TraceEvent JSON>\n\n`
    - `event: done\ndata: {"permit_id": "...", "final_decision": "..."}\n\n`
  - Replay guarantee: Streams all past recorded events first, then live events.
- **`GET /permits/{permit_id}`**:
  - Returns `PermitStatusResponse` (`contract/schemas/permit_status_response.schema.json`).
- **`POST /admin/fault`**:
  - Request: `{"department": "fire", "mode": "offline", "delay_ms": 3000}`.
  - Forwards call directly to target department's `POST /fault`.
- **`GET /admin/fault`**:
  - Returns current fault mode for all 4 departments.
- **`GET /health`**:
  - Liveness and readiness probe responding with HTTP 200 `{"status": "healthy"}`.
- **`GET /hello`**:
  - Public hello-world endpoint responding with HTTP 200 `{"status": "healthy", "message": "...", "service": "permit-orchestrator", "timestamp": "..."}`.

---

## 4. Timeout and Retry Budget

- **Per-request Department Timeout**: `2.0` seconds.
- **Max Attempts**: `3` (Initial attempt + 2 retries).
- **Backoff Strategy**:
  - Retry 1: 200ms delay.
  - Retry 2: 500ms delay.
- **Max Span Failure Latency**: `2.0s + 0.2s + 2.0s + 0.5s + 2.0s = 6.7s`.
- **Overall Orchestrator Guard Timeout**: `10.0s`.

---

## 5. Consensus Rules

1. **Incomplete reviews take precedence**: If ANY department status is `TIMEOUT` or `ERROR` after 3 attempts:
   - `final_decision`: `REJECTED`
   - `decision_code`: `INCOMPLETE_REVIEWS`
   - *Fulfills Goal #1: Never approved if any department timed out or errored.*
2. **Rule rejections**: If all 4 responded, but 1 or more returned `status: "REJECTED"`:
   - `final_decision`: `REJECTED`
   - `decision_code`: `DEPARTMENT_REJECTIONS`
   - Violations from all rejecting departments are aggregated.
3. **Full consensus**: If all 4 responded and all 4 returned `status: "APPROVED"`:
   - `final_decision`: `APPROVED`
   - `decision_code`: `ALL_DEPARTMENTS_APPROVED`
