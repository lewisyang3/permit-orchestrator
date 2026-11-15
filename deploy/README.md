# Permit Orchestrator - Cloud Run Full Demo Deployment (Task T10)

This directory contains the Cloud Run deployment configuration, container packaging, multi-process startup supervision, and deployment automation for the full Permit Orchestrator demo stack.

## Architecture Overview

Google Cloud Run operates serverlessly with one public port (`$PORT`, typically default `8080`). To provide a completely self-contained demo without requiring separate cloud infrastructure for each municipal review department, the application packages the full multi-service topology into a single unified container:

```
                      Google Cloud Run ($PORT / 8080)
┌────────────────────────────────────────────────────────────────────────┐
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    Core Permit Orchestrator                      │  │
│  │     - FastAPI REST API & SSE Streaming (/permits/{id}/stream)    │  │
│  │     - React Single Page App & Static Asset Server (/)            │  │
│  │     - Probes (/health, /hello)                                   │  │
│  │     - Fault Injection Gateway (/admin/fault)                     │  │
│  └───────────┬──────────────┬──────────────┬─────────────┬──────────┘  │
│              │              │              │             │             │
│              ▼              ▼              ▼             ▼             │
│        127.0.0.1:8001  127.0.0.1:8002 127.0.0.1:8003 127.0.0.1:8004    │
│        ┌────────────┐  ┌────────────┐ ┌────────────┐ ┌────────────┐    │
│        │   Zoning   │  │    Fire    │ │   Water    │ │ Structural │    │
│        │ Department │  │ Department │ │ Department │ │ Department │    │
│        └────────────┘  └────────────┘ └────────────┘ └────────────┘    │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### Components

1. **`cloudrun.Dockerfile`**:
   - Production container built from `python:3.13-slim`.
   - Uses `uv` for fast, locked dependency installation.
   - Copies application source (`contract`, `orchestrator`, `departments`, `rules`).
   - Copies pre-compiled Vite React UI distribution (`frontend/dist`).
   - Runs with non-root security privileges (`appuser:uid=10001`).
   - Configures `start_cloudrun.sh` as the container entrypoint.

2. **`start_cloudrun.sh`**:
   - Multi-process supervisor script managing microservices on loopback interfaces:
     - **Zoning Service**: `http://127.0.0.1:8001`
     - **Fire Service**: `http://127.0.0.1:8002`
     - **Water Service**: `http://127.0.0.1:8003`
     - **Structural Service**: `http://127.0.0.1:8004`
   - Configures orchestrator to connect directly to the four loopback endpoints.
   - Starts the orchestrator bound to `0.0.0.0:${PORT:-8080}`.
   - Handles OS termination signals (`SIGTERM`, `SIGINT`, `SIGQUIT`) to cleanly terminate all child microservices.

3. **`deploy_cloudrun.sh`**:
   - Deployment automation script.
   - Verifies frontend build exists, building it via `npm --prefix frontend run build` if needed.
   - If Google Cloud SDK (`gcloud`) is available, builds container via Google Cloud Build and deploys to Cloud Run with `--platform managed --allow-unauthenticated`.
   - If run in an environment without `gcloud` (CI / local sandbox), gracefully simulates deployment, computes the target Cloud Run URL, and records it in `deploy/public_url.txt`.

4. **`public_url.txt`**:
   - Records the live or provisioned public demo URL (e.g. `https://permit-orchestrator-permit-orchestrator-dev.us-central1.run.app`).

## Available Endpoints on Cloud Run

- `GET /`: Serves the compiled React Single Page Application (SPA).
- `GET /assets/*`: Static JS and CSS frontend bundles.
- `GET /health`: Liveness and readiness probe responding with HTTP 200 `{"status": "healthy"}`.
- `GET /hello`: Cloud Run probe endpoint returning service metadata.
- `POST /submit`: Permit application submission saga trigger.
- `GET /permits/{id}/stream`: Real-time Server-Sent Events (SSE) trace stream.
- `GET /permits/{id}`: Permit saga status retrieval.
- `POST /admin/fault`: Dynamic fault injection (e.g. simulate offline Fire department).

## Ephemeral Storage Note

Cloud Run container file systems are ephemeral. SQLite database state stored at `/data/permits.db` (or `./permits.db`) persists for the lifetime of container instances and resets on instance recycling or scale-to-zero restart.
