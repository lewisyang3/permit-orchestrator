# Permit Orchestrator — local Docker stack

Compose runs five containers: a development orchestrator and four copies of the same department image. Department name, rule file, and port differ for each copy. Only the orchestrator mounts the SQLite volume. No database server or SQLite container is needed.

## Start

Install and start Docker Desktop, then run these commands from the directory containing `compose.yaml`:

```bash
docker compose config --quiet
docker compose up --build -d --wait
docker compose ps
```

No Gemini key is needed for this scaffold. `.env.example` reserves the variable for the future T2 implementation; no model requests or API charges occur yet. Runtime and development dependencies are declared in `pyproject.toml`; `uv.lock` pins their exact versions. Docker builds install only runtime dependencies using `uv sync --locked --no-dev`. `.dockerignore` excludes local environments, credentials, and database files from image builds.

| Service | Local address | Container-to-container address |
| --- | --- | --- |
| Development orchestrator | http://localhost:8000 | http://orchestrator:8000 |
| Zoning | http://localhost:8001 | http://zoning:8001 |
| Fire | http://localhost:8002 | http://fire:8002 |
| Water | http://localhost:8003 | http://water:8003 |
| Structural | http://localhost:8004 | http://structural:8004 |

All services expose `/health` and interactive API documentation at `/docs`. Each department also exposes `/rules` to confirm its loaded configuration. Host ports bind to loopback for local use. Compose connects services on its default network and waits for healthy departments before starting the orchestrator.

```bash
curl http://localhost:8002/health
curl http://localhost:8002/rules
docker compose logs -f fire
```

## Submit a connectivity demo

```bash
curl --fail-with-body -X POST http://localhost:8000/demo/submit \
  -H 'Content-Type: application/json' \
  --data-binary @contract/fixtures/permit_approved.json
```

The harness calls all four department `/evaluate` endpoints in parallel with a two-second request timeout, collects responses, and saves the payload and final result in SQLite. It generates a new permit ID on each submission. Read a saved result using the returned ID:

```bash
curl http://localhost:8000/demo/permits/REPLACE_WITH_RETURNED_PERMIT_ID
```

**Expected result today: `REJECTED` with `INCOMPLETE_REVIEWS`.** The department scaffolds load real rule files but return HTTP 501 `EVALUATOR_NOT_IMPLEMENTED`. Their health checks confirm the running service and loaded rules, not completed review capability. The fixture filename does not determine its result.

## SQLite persistence

The database path inside the orchestrator is `/data/permits.db`. The named volume `permit-data` is mounted at `/data`, preserving the database and its journal files when containers are replaced. Department containers cannot access it.

To verify persistence, submit a demo, record its ID, stop/recreate the stack, then fetch the same ID:

```bash
docker compose down
docker compose up -d --wait
curl http://localhost:8000/demo/permits/REPLACE_WITH_RETURNED_PERMIT_ID
```

Ordinary `down` preserves the volume. `docker compose down --volumes` deletes the database volume; use that only for an intentional reset.

## What is implemented

- Shared department scaffold, startup rule loading, health/configuration endpoints, and explicit unimplemented evaluation responses.
- Dockerfiles, five-service Compose wiring, non-root processes, health checks, and persistent SQLite storage.
- A separate development harness for fan-out, response identity validation, fail-safe aggregation, and saving/retrieving demo results.

The harness in `dev/` is not the production T1/T5 orchestrator. It uses `/demo/*`, makes one attempt per department, and does not implement retries, SSE, trace events, fault-control endpoints, or durable in-flight jobs. It persists completed demo requests only. Replace its build entry with Person A's implementation when available; production should use the frozen `/submit` and status/event contract.

Next for T2: implement Gemini evaluation behind `/evaluate`, ground it in the YAML rules and project evidence, and resolve how missing evidence and partial rule coverage map to the shared contract. The current rules and permit fixtures have known scope/input differences documented in [rules/README.md](rules/README.md). Do not manufacture approvals to make the demo green.

## Python tests without Docker

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv sync --locked
uv run --locked pytest -q
```

Run tests from this directory. They use mocked department HTTP calls and temporary SQLite files; they do not call Gemini. `uv sync` creates the local `.venv` and includes the development group by default. This is an application project, not an installable Python package.

Use `uv add PACKAGE` for runtime dependencies and `uv add --dev PACKAGE` for development dependencies. Both update `pyproject.toml` and `uv.lock`; commit both files. If editing the TOML manually, run `uv lock` afterward. Rebuild images after dependency or source changes with `docker compose up --build -d --wait`.

## How the script gets into each container

The department Dockerfile copies `departments/` (the Python script), `contract/` (the shared request models), and `rules/` (the department YAML files) into `/app`. Compose starts `departments.app:app` with Uvicorn and sets `DEPARTMENT`, `RULE_FILE`, and `PORT` for each container. The orchestrator sends the application data over HTTP to `POST /evaluate`; application data is not baked into an image.

Startup loads the selected rule file, and `/evaluate` receives the orchestrator request. The function currently returns HTTP 501; Gemini evaluation is the next implementation step. When that code is added under `departments/`, the existing Dockerfile includes it automatically on rebuild. Runtime credentials come from environment configuration, not copied credential files. Image builds contain a snapshot of the source; editing a local Python or YAML file requires a rebuild to update the running containers.
