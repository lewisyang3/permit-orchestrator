# Permit Orchestrator — Trace Waterfall UI (Tasks T7 & T9)

A distributed trace waterfall visualization (React + TypeScript + Tailwind CSS) modeled after Jaeger / OpenTelemetry, built for observing the parallel permit review saga across 4 departments (Zoning, Fire, Water, Structural) with fail-safe consensus guarantees.

## Key Capabilities

### 1. Live Backend Integration (Task T9)
- **Live Saga Submission**: Submits permit applications to `POST /submit`, receiving HTTP `202 Accepted` with a stream URL.
- **Server-Sent Events (SSE) Stream**: Real-time event consumption from `GET /permits/{id}/stream`.
- **Live Trace Rendering**: Spans, latency metrics, and consensus banners update progressively in real time as events stream in.
- **Backend Connectivity Pill**: Header automatically polls `GET /health` on `:8000` to indicate backend connectivity.

### 2. Fault Injection & Chaos Control Panel (Task T9)
- **Live Fault Status**: Queries `GET /admin/fault` to show active modes for all 4 departments (`zoning`, `fire`, `water`, `structural`).
- **Interactive Controls**: Toggle any department microservice between `none` (healthy), `offline` (HTTP 503), `slow` (delay > 2.0s), and `error` (HTTP 500).
- **1-Minute Chaos Demo ("Kill Fire Department")**:
  - Sets the Fire department to `offline`.
  - Automatically submits a permit in live mode.
  - Draws a live waterfall with 3 retry attempts, each with 2.0s timeouts and exponential backoffs (200ms, 500ms).
  - Produces a visible timeout and ends with an `INCOMPLETE_REVIEWS` fail-safe rejection banner.
- **One-Click Restore**: "Restore All" resets all 4 departments back to healthy operational state.

### 3. Golden Fixture Mode (Task T7)
- Works 100% offline with zero backend running, covering all 3 contract golden outcomes:
  1. **Clean Approval**: All 4 departments approve on attempt 1 (`ALL_DEPARTMENTS_APPROVED`).
  2. **Department Rejection**: Fire department returns violations (`DEPARTMENT_REJECTIONS`).
  3. **Timeout / Retry**: Fire department fails after 3 attempts with 2.0s timeouts (`INCOMPLETE_REVIEWS`).
- **Animated Saga Replay**: Step-by-step simulation mode animating event streams.

### 4. Distributed Trace Waterfall & Inspection
- Root orchestrator span and child spans for Zoning, Fire, Water, Structural, and Consensus.
- Visual horizontal waterfall bars with time scale ruler.
- **Retry Markers**: Visual attempt partitions (`Att #1`, `+200ms`, `Att #2`, `+500ms`, `Att #3`) directly on retry spans.
- **Span Detail Drawer**: Click any span to inspect its latency, retry flow, rule evaluations, and raw JSON event chronology.
- **Permit Payload Inspector**: View and edit the submitted permit application JSON or structured visual cards.

## Development & Testing

```bash
# Install dependencies
npm install

# Start development server on port 3000 (proxies /submit, /permits, /admin to :8000)
npm run dev

# Run Vitest unit & component test suite (14 tests)
npm test

# Build production bundle (tsc && vite build)
npm run build
```
