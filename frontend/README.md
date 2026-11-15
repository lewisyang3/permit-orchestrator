# Permit Orchestrator — Trace Waterfall UI (Task T7)

A distributed trace waterfall visualization (React + TypeScript + Tailwind CSS) modeled after Jaeger / OpenTelemetry, built for observing the parallel permit review saga across 4 departments (Zoning, Fire, Water, Structural).

## Key Capabilities (T7)

- **Zero-Backend Fixture Mode**: Fully renders and demonstrates all 3 contract golden outcomes without any running backend:
  1. **Clean Approval**: All 4 departments approve on attempt 1 (`ALL_DEPARTMENTS_APPROVED`).
  2. **Department Rejection**: Fire department returns violations (`DEPARTMENT_REJECTIONS`).
  3. **Timeout / Retry**: Fire department fails after 3 attempts with 2.0s timeouts and exponential backoffs (200ms, 500ms), demonstrating the fail-safe consensus rule (`INCOMPLETE_REVIEWS`).
- **Distributed Trace Waterfall**:
  - Horizontal timeline with time scale ruler.
  - Root orchestrator span and child spans for Zoning, Fire, Water, Structural, and Consensus.
  - Color-coded status badges and latency indicators.
  - **Retry Markers**: Visual attempt partitions and backoff indicators directly on retry spans.
- **Span Inspection Drawer**:
  - Click any span to inspect its latency, retry chronology, rule evaluations, and raw event log.
- **Permit Payload Inspector**:
  - View and edit the submitted permit application JSON or structured visual cards.
- **Saga Simulation & Replay**:
  - Replay events progressively to watch the trace waterfall assemble in real time.
- **Live Orchestrator Mode (T9 Ready)**:
  - Toggle to connect to `POST /submit` and stream live events over Server-Sent Events (`/permits/{id}/stream`).

## Development

```bash
# Install dependencies
npm install

# Start development server on port 3000
npm run dev

# Run Vitest unit & component test suite
npm test

# Build production bundle
npm run build
```
