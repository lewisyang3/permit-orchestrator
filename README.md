# Permit Orchestrator

Permit Orchestrator autonomously reviews and approves city building permits. Built for the UC Berkeley x Google DeepMind Hackathon (Antigravity CLI track).

---

## The problem

Getting a building permit is one of the slowest, most opaque bureaucratic bottlenecks in urban development. Homeowners and commercial developers submit architectural blueprints and permit applications that sit in municipal backlogs for months. Within city hall, applications must navigate four independent departmental silos—**Zoning**, **Fire Life Safety**, **Water & Utilities**, and **Structural Engineering**—each operating on separate schedules, manual checklists, and conflicting code cycles.

When reviews stall or fail, applicants receive cryptic rejection letters with zero visibility into which department blocked approval or what specific code standard was violated. Worse, in manual workflows, approvals can accidentally slip through when a department fails to respond in time, creating severe public safety and legal liabilities. Municipalities lack distributed consensus guarantees and end-to-end audit trails to prove that every required authority actively signed off.

---

## How Permit Orchestrator solves it

Permit Orchestrator replaces manual municipal permit routing with an asynchronous distributed saga. It extracts specifications directly from blueprint documents, evaluates them concurrently across isolated department microservices, and enforces a strict consensus gate: **a permit is approved if and only if all four departments explicitly sign off**.

Every application moves through five automated steps, prioritizing fast, deterministic checks before expensive AI extraction and network calls:

1. **Intake.** Applicants upload an architectural plan (PDF, blueprint text, or select a built-in sample). A deterministic parser extracts normalized permit parameters (dimensions, setbacks, occupant loads, utility connections). When blueprints are unstructured or complex, Gemini 2.5 Flash extracts the structured permit payload.
2. **Dispatch.** The orchestrator initiates an asynchronous distributed saga (`POST /submit`). It records the permit into SQLite and dispatches concurrent evaluation requests to four isolated department microservices (Zoning `:8001`, Fire `:8002`, Water `:8003`, Structural `:8004`).
3. **Evaluate.** Each department independently evaluates the application against codified standards (such as San José Title 20 zoning regulations, NFPA 13 sprinkler thresholds, and CBC/IBC egress requirements). If a parameter fails, the evaluator cites the exact rule ID, statutory section, and violation reason.
4. **Consensus Gate.** The orchestrator gathers results and evaluates a fail-safe consensus rule: **unanimous 4/4 approval is strictly required**. If any department reports a violation, the application is marked `REJECTED` with all aggregated citations. If a department times out or encounters network faults after 3 exponential backoff retries, the consensus fail-safe triggers, refusing approval (`INCOMPLETE_REVIEWS`) to prevent unverified permits from slipping through.
5. **Trace & Explain.** The orchestrator maintains an append-only timeline of `TraceEvent` records in SQLite and streams them live to the browser over Server-Sent Events (SSE). An Apple-inspired Jaeger-style trace waterfall visualizes the distributed execution, span durations, retries, and department decisions in real time.

The orchestrator is built for resilience. In the live interface, a built-in **Fault Injection & Chaos Controls** panel lets operators simulate municipal outages (such as knocking the Fire department offline with HTTP 503) to watch the saga engine execute exponential backoffs and enforce consensus fail-safes live.

---

## Stack

| Layer | Choice |
| --- | --- |
| **Backend & Saga Engine** | Python 3.13, FastAPI, Pydantic v2, managed with `uv` |
| **Database & Trace Log** | SQLite in WAL mode with append-only `trace_events` table |
| **Frontend UI** | React 18, Vite 6, TypeScript, Tailwind CSS v4, Lucide Icons |
| **Observability & Streaming** | Server-Sent Events (SSE) with real-time Jaeger-style trace waterfall |
| **Document Intake & Vision** | Google Gemini 2.5 Flash with deterministic fallback parser |
| **Rule Engine** | Codified YAML building standards (San José Title 20, NFPA 13, CBC/IBC) |
| **Container Orchestration** | Docker Compose v2 (local 5-service stack) & Google Cloud Run (serverless) |
| **Tests & Verification** | pytest (350 backend tests) and Vitest (31 frontend tests) |

---

## Quick start

You need Python 3.13 with [uv](https://docs.astral.sh/uv/), Docker with Compose v2 (Docker Desktop running), and Node 20 or later with npm.

### Option 1: Full Distributed Stack (Docker Compose)

From the project root:

```bash
make up        # build and start orchestrator (:8000) and 4 department microservices (:8001-:8004)
```

Then, in a second terminal:

```bash
cd frontend
npm install
npm run dev    # starts the dashboard on http://localhost:3000
```

Open **[http://localhost:3000](http://localhost:3000)** in your browser:
- Select a sample blueprint from the dropdown (e.g. **Commercial Restaurant Patio** or **Residential Rear Addition**).
- Click **Check For Approval** to watch the orchestrator evaluate the specifications and stream the consensus verdict live over SSE.
- Open **Fault Injection & Chaos Controls** to trigger a simulated Fire department outage and watch fail-safe consensus refuse approval in real time.

---

### Option 2: Quick Start — Frontend Only (Golden Fixtures Mode)

*No Docker or backend required.* Explore verified municipal contract baselines immediately:

```bash
cd frontend
npm install
npm run dev
```

Open **[http://localhost:3000](http://localhost:3000)** to interact with the UI, test blueprint sample loading, and replay golden execution traces.

---

### Tests and Verification

Run the comprehensive test suites across backend and frontend:

```bash
uv run pytest tests/           # 350 backend saga, consensus, and fault tests
npm --prefix frontend test     # 31 frontend vitest trace and intake tests
```

---

### Cloud Run Hosting

Deploy the unified container to Google Cloud Run:

```bash
./deploy/deploy_cloudrun.sh
```

For complete cloud deployment commands, custom domains, and probe verification, see [deploy/README.md](deploy/README.md).
