# Permit Orchestrator — Rough Design Document

Nov 13, 2026 · @Lewis

## Table of contents

1. Introduction and overview: problem, proposed solution, high-level goals
2. System architecture: system diagram, execution model
3. Data storage design
4. Work plan for two people: ownership, dependencies, critical path
5. Open questions

## Introduction and overview

Permit Orchestrator models a city permit office as a distributed system: four independent department services review one application in parallel, and a consensus step refuses approval unless every department answers. It is a hackathon build for the Antigravity CLI track, made with `/swarm` and due at 10:30 AM PT on Nov 14.

### Problem

- A building permit needs sign-off from several isolated departments: Zoning, Fire, Water and Structural.
- Applicants usually see only a status such as "Pending" and cannot tell which department is holding things up.
- When one department is slow or offline, the whole application stalls with no clear failure signal.

### Proposed solution

- **Orchestrator (FastAPI).** Takes a permit payload and fans it out to four department services at once, then fans the results back in.
- **Department services (x4).** Each applies its own rule set with a Gemini-based check and returns approve, reject or needs-info with reasons.
- **Consensus engine.** Enforces timeouts, retries with backoff and one rule: no "Approved" unless all four departments responded.
- **Trace UI (React + Tailwind).** Draws each permit as a distributed trace, with one span per department, so the bottleneck is visible.
- **Intake (stretch).** Parses an uploaded plan or application into the permit JSON; the MVP accepts JSON directly.

### High-level goals

1. **Fail safe.** A permit is never approved if any department timed out or errored after retries; it fails cleanly and does not hang.
2. **Observable.** Every permit shows per-department latency, retries and outcome in a Jaeger-style trace view.
3. **Demoable in one minute.** Submit a permit, kill a department container, and show the timeout, retry and clean rejection.
4. **Shippable by deadline.** Hosted prototype, public repo, both one-pagers and both videos ready by 10:30 AM PT.

**Non-goals:** legally accurate rulings, authentication, integration with a real city system, and multi-node production deployment.

## System architecture

One orchestrator fans a permit out to four department services and merges their answers; every step is recorded as a trace event the UI reads.

### System diagram

&#91;embedded content: architecture · 1 orchestrator, 4 departments, 1 store\]

The orchestrator calls all four departments in parallel and is the only component that writes to SQLite.

### Execution model

Each permit runs as a fan-out/fan-in saga with a single orchestrator as coordinator:

1. The client posts the permit JSON to `/submit`. The orchestrator assigns a `permit_id` and `trace_id` and records state `RECEIVED`.
2. It fires one async request per department (Zoning, Fire, Water, Structural) at the same time, each as its own span.
3. Each request has a 2-second timeout. A timeout or error triggers a retry with exponential backoff.
4. A department service checks the permit against its rules and returns a decision plus reasons.
5. At fan-in, the orchestrator merges the four results into one state map: status, attempts and latency per department.
6. The consensus step turns the state map into a final decision (table below) and records state `DECIDED`.
7. Every state change is appended as a trace event, and the UI renders the events as a waterfall.

| Department results | Final decision |
| --- | --- |
| All four approve | APPROVED |
| All four responded, at least one rejects | REJECTED, with the violations listed |
| Any department still failing after retries | REJECTED as incomplete; never APPROVED |

**Verification.** A verifier agent writes a pytest integration suite that takes the Fire service offline and asserts the permit ends REJECTED, without hanging. The same suite checks that a permit is never APPROVED while any department result is missing.

## Data storage design

The orchestrator is the only writer, so one SQLite file holds all state and the department services stay stateless and safe to kill.

| Entity | Store | Key fields | Purpose |
| --- | --- | --- | --- |
| `permits` | SQLite | `permit_id`, `payload` (JSON), `state`, `created_at`, `decided_at` | The submitted application and its saga state |
| `department_results` | SQLite | `permit_id`, `department`, `status`, `attempts`, `latency_ms`, `reasons` (JSON) | One row per department per permit; this is the state map |
| `trace_events` | SQLite, append-only | `event_id`, `trace_id`, `permit_id`, `span`, `type`, `ts`, `detail` | Source of truth for the waterfall view; replayable |
| `rules` | YAML files in the repo, one per department | `rule_id`, `description`, `threshold`, `source` | Rule set each department loads at startup |
| `uploads` | Local disk | `permit_id`, `filename` | Original plans for the intake stretch goal |

**Design choices**

- **Events first.** `trace_events` is append-only; `permits.state` and `department_results` can be rebuilt from it.
- **Safe retries.** Each department call carries `permit_id` and an attempt number, so a repeated request returns the same answer.
- **Live UI.** The orchestrator streams new events to the browser over server-sent events; the UI does not poll.
- **Stateless departments.** They only read their rule file, so killing one loses no data.
- **Hosting.** Cloud Run disk is ephemeral, so the hosted demo's database resets on restart unless it is mounted or moved to a managed database.

## Work plan for two people

Split by system plane, not by layer. Person A owns the control plane (orchestrator, events, tests, deployment) and Person B owns the data plane (department services and fault modes) plus the trace UI. Each person gets five engineering tasks. Both first freeze one shared contract, then build against stubs and fixtures so neither waits on the other. Give A to whoever is stronger on infrastructure.

### Dependency diagram

&#91;embedded content: work plan · 2 people, 5 phases, 3 cross-person handoffs\]

Both people depend on the contract first. After that, three handoffs cross lanes: A's SSE stream unblocks B's live UI, B's departments and fault modes unblock A's verifier tests, and A's deploy unblocks B's demo recording.

### Tasks and blockers

| ID | Task | Owner | Blocked by | Done when |
| --- | --- | --- | --- | --- |
| T0 | Freeze the contract: permit, event and department JSON schemas, endpoints, state names, repo, one fixture per outcome. A drafts the event schema, B drafts the department schema | Both | none | `contract/` is committed with fixtures for approved, rejected and timed-out permits |
| T1 | Orchestrator core: `/submit`, parallel fan-out, 2 s timeout, retry with backoff, state map, consensus, plus a 20-line stub department to test against | A | T0 | A curl of a sample permit returns a final decision from four stub departments |
| T2 | Department services: one app configured per department, loads a rule file, Gemini writes the reasons, Dockerfile | B | T0 | Each department returns a decision and reasons for a sample permit |
| T3 | Rule files (one YAML per department) and five sample permits covering approve, reject and mixed | B | T0 | Files committed; departments load them |
| T4 | Fault modes in each department (down, slow past 2 s, error) behind a control endpoint | B | T2 | One call switches a department into a fault mode and back |
| T5 | SQLite, `trace_events`, the SSE stream, and an admin endpoint that forwards fault commands to a department | A | T1 | A submitted permit streams events to a browser live |
| T6 | Docker compose for the orchestrator and four departments; hello-world Cloud Run deploy | A | T0 for hello-world; T2 for compose | `docker compose up` runs the stack; a public hello-world URL responds |
| T7 | Trace UI on fixtures: submit form, waterfall, per-department spans, latency, retry markers | B | T0 | Waterfall renders every fixture outcome with no backend running |
| T8 | Verifier tests: Fire offline gives REJECTED, never APPROVED, no hang | A | T1, T2, T3, T4 | The suite passes against real departments |
| T9 | Wire the UI to the live backend and add the fault button | B | T5, T7; T4 for the button | Submitting in the browser draws a live trace, and the button produces a visible timeout |
| T10 | Final Cloud Run deploy of all services and the UI build | A | T2, T5, T6, T9 | A public URL runs the full demo |
| T11 | Record the 1-minute demo (B) and the 2-minute team pitch video (both) | Both | T9, T10 | Videos exist and the demo is public on YouTube |
| T12 | Written items: A writes the idea one-pager and README; B writes the team one-pager, fills the submission form and posts the video | Both | T0 to draft; T10, T11 to finish | Every required item is submitted |
| T13 | Intake stretch: Gemini parses an uploaded plan into permit JSON | Whoever is free first | T0 | Only if T9 is done early |

### Critical path

T0, T1, T5, T9, T10, T11, then submit. A's T1 and T5 gate B's live wiring, and B's T9 then gates A's final deploy, so each person is on the critical path once. Neither blocks the other early: A tests against a stub department, and B builds the UI against fixtures. B's T2 to T4 gate A's T8, so B should finish the fault modes before A needs them for tests.

### Working agreements

- **Contract changes.** A owns the event schema and B owns the department request, response and control schemas. Tell the other person before changing either, and update the fixtures in the same commit.
- **Ownership of folders.** A owns `orchestrator/`, `tests/` and `deploy/`; B owns `departments/`, `rules/` and `frontend/`; both edit `contract/`. Commit small and often to main to avoid merge conflicts.
- **First live run.** When T2 and T5 have both landed, both stop and run the full submit-to-trace flow once together.
- **`/swarm` account.** Confirm which person has the personal Google AI Pro or Ultra account. That person runs `/swarm` for shared scaffolds and pushes; the other pulls. Both need a Gemini API key.
- **Deploy early.** A late first deploy is the biggest schedule risk, so A ships a hello-world service right after T0 to surface permissions and quota issues.
- **Spare capacity.** Whoever finishes first takes T13 or reviews the other's work, starting with A's retry logic or B's fault modes.
- **Freeze.** Stop new features at least 3 hours before the 10:30 AM PT deadline, then record, upload and submit, aiming to finish 30 minutes early.
- **Post early.** Make the demo video public on YouTube as soon as it is recorded, since engagement counts toward the score.

### Cut list

If the plan slips, drop in this order:

1. Intake (T12).
2. Gemini-written reasons: use fixed reason strings from the rule files.
3. Cloud Run with four separate department services: run them as processes in one service and keep docker-compose for the recorded demo.
4. Visual polish on the trace view.

Do not cut T4 and T8: the fault modes and the verifier tests are the core of the demo.

## Open questions

- [ ] **LLM latency vs the 2-second timeout.** A Gemini call may take longer than 2 seconds, which would time out healthy departments. Should departments use deterministic rules and call Gemini only for the written explanation, or should the timeout be raised?
- [ ] **How to kill a service in the demo.** Docker Compose containers can be killed live, but a Cloud Run demo cannot. Do we add a fault-injection toggle in the UI that takes a department offline?
- [ ] **Final state for an unreachable department.** The brief says reject. Should the UI show a distinct "incomplete" outcome so it reads differently from a rule violation?
- [ ] **Retry budget.** How many retries and what backoff keep the worst case short enough that the demo never appears to hang?
- [ ] **Rule corpus.** Use hand-written mock rules or real municipal code excerpts, and how do we label the output as non-binding?
- [ ] **Intake scope.** Is plan or PDF parsing worth building with under a day left, or do we ship JSON and form input only?
- [ ] **Language and names.** The intro mentions Go or Python but the prompts specify FastAPI, so settle on Python? Also choose Permit Orchestrator or PermitSwarm, and Water or Environmental for the fourth department.
- [ ] **Pitch claim.** "Cities lose billions in delayed property taxes" needs a source before the VC judges hear it.
- [ ] **Hackathon logistics.** Confirm we have a personal Google AI Pro or Ultra account for `/swarm`, and decide whether to opt out of paid-ad use of the demo video before submitting.
- [ ] **Deadline.** The event page says 10:30 AM PT in one place and end of day for the Online Track in another. Ask the organizers which applies.
