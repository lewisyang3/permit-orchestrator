# Permit Orchestrator — Trace Waterfall UI (Tasks T7, T9 & T13)

A distributed trace waterfall visualization (React + TypeScript + Tailwind CSS) modeled after Jaeger / OpenTelemetry, built for observing the parallel permit review saga across 4 departments (Zoning, Fire, Water, Structural) with fail-safe consensus guarantees and multimodal Gemini plan intake.

## Key Capabilities

### 1. Architectural Plan & PDF Intake (Task T13)
- **Multimodal Gemini Extraction**: Supports uploading architectural blueprint plans (PDF, text, markdown) to `POST /intake/parse`.
- **Gemini 2.5 Flash**: Employs Google Gemini (`gemini-2.5-flash`) multimodal REST API with structured JSON output schema to extract all Zoning, Fire, Water, and Structural review parameters.
- **Deterministic Offline Fallback**: Features a rule-based deterministic extractor for offline testing and local development when `GEMINI_API_KEY` is not present.
- **Sample Blueprint Catalog**: Quick selector for built-in blueprints (`Residential Rear Addition`, `Commercial Restaurant Patio`, `Storage Warehouse Modernization`).
- **Disk Archiving**: Automatically archives uploaded plans on disk under `uploads/<permit_id>_<filename>` for compliance auditing.

### 2. Live Backend Integration (Task T9)
- **Live Saga Submission**: Submits permit applications to `POST /submit`, receiving HTTP `202 Accepted` with a stream URL.
- **Server-Sent Events (SSE) Stream**: Real-time event consumption from `GET /permits/{id}/stream`.
- **Live Trace Rendering**: Spans, latency metrics, and consensus banners update progressively in real time as events stream in.
- **Backend Connectivity Pill**: Header automatically polls `GET /health` on `:8000` to indicate backend connectivity.

### 3. Fault Injection & Chaos Control Panel (Task T9)
- **Live Fault Status**: Queries `GET /admin/fault` to show active modes for all 4 departments (`zoning`, `fire`, `water`, `structural`).
- **Interactive Controls**: Toggle any department microservice between `none` (healthy), `offline` (HTTP 503), `slow` (delay > 2.0s), and `error` (HTTP 500).
- **1-Minute Chaos Demo ("Kill Fire Department")**:
  - Sets the Fire department to `offline`.
  - Automatically submits a permit in live mode.
  - Draws a live waterfall with 3 retry attempts, each with 2.0s timeouts and exponential backoffs (200ms, 500ms).
  - Produces a visible timeout and ends with an `INCOMPLETE_REVIEWS` fail-safe rejection banner.
- **One-Click Restore**: "Restore All" resets all 4 departments back to healthy operational state.

### 4. Golden Fixture Mode (Task T7)
- Works 100% offline with zero backend running, covering all 3 contract golden outcomes:
  1. **Clean Approval**: All 4 departments approve on attempt 1 (`ALL_DEPARTMENTS_APPROVED`).
  2. **Department Rejection**: Fire department returns violations (`DEPARTMENT_REJECTIONS`).
  3. **Timeout / Retry**: Fire department fails after 3 attempts with 2.0s timeouts (`INCOMPLETE_REVIEWS`).
- **Animated Saga Replay**: Step-by-step simulation mode animating event streams.

### 5. Distributed Trace Waterfall & Inspection
- Root orchestrator span and child spans for Zoning, Fire, Water, Structural, and Consensus.
- Visual horizontal waterfall bars with time scale ruler.
- **Retry Markers**: Visual attempt partitions (`Att #1`, `+200ms`, `Att #2`, `+500ms`, `Att #3`) directly on retry spans.
- **Span Detail Drawer**: Click any span to inspect its latency, retry flow, rule evaluations, and raw JSON event chronology.
- **Permit Payload Inspector**: View and edit the submitted permit application JSON or structured visual cards.

## Development & Testing

```bash
# Install dependencies
npm install

# Start development server on port 3000 (proxies /health, /submit, /permits, /admin, /intake to :8000)
npm run dev

# Run Vitest unit & component test suite (29 tests across 6 suites)
npm test

# Build production bundle (tsc && vite build)
npm run build
```
