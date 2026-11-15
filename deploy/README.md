# Permit Orchestrator - Google Cloud Run Deployment Guide

This directory contains the production Cloud Run deployment configuration and automation script for the Permit Orchestrator full-stack application (Task T6 & Task T10).

---

## Architecture Overview

```
                      +---------------------------------------+
                      |       Google Cloud Run ($PORT)        |
                      |  https://<service-name>.run.app       |
                      +-------------------+-------------------+
                                          |
                      +-------------------v-------------------+
                      |      FastAPI Orchestrator (:8080)     |
                      |  - Serves React Frontend SPA (GET /)  |
                      |  - Distributed Saga Engine (/submit)  |
                      |  - Live SSE Event Stream (/permits)   |
                      |  - Intake Document Parser (/intake)   |
                      |  - Fault Injection Control (/admin)   |
                      |  - Health & Hello Probes (/health)    |
                      +--+--------+--------+---------------+--+
                         |        |        |               |
                         | (8001) | (8002) | (8003)        | (8004)
                         v        v        v               v
                      [Zoning]  [Fire]  [Water]      [Structural]
                         (All 4 microservices running locally)
```

1. **All-in-One Process Isolation**: A single Cloud Run container runs the 4 municipal department evaluators as lightweight localhost processes on ports `8001` (Zoning), `8002` (Fire), `8003` (Water), and `8004` (Structural).
2. **Unified Frontend & Backend**: The orchestrator serves the compiled React Single-Page Application (`frontend/dist`) on the root route (`GET /`), completely eliminating cross-origin CORS barriers.
3. **Fault Injection & Chaos Testing**: The UI's **Fault Injection & Chaos Controls** communicate via `POST /admin/fault` to toggle simulated outages (503), slow delays, or errors without needing root access to restart containers.
4. **Cloud Run Health Probes**: `GET /health` and `GET /hello` verify container liveness and return operational metadata.

---

## Deployment Prerequisites

1. **Google Cloud Account & Project**:
   Create a Google Cloud project (or use an existing one):
   ```bash
   gcloud projects create my-permit-orchestrator --name="Permit Orchestrator"
   gcloud config set project my-permit-orchestrator
   ```
2. **Enable Required APIs**:
   ```bash
   gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com
   ```
3. **Install & Authenticate Google Cloud CLI**:
   ```bash
   gcloud auth login
   gcloud auth configure-docker
   ```

---

## How to Deploy to Cloud Run

### Option A: One-Command Deployment Script
Run the automated deployment script from the project root:
```bash
./deploy/deploy_cloudrun.sh
```

You can customize deployment parameters using environment variables:
```bash
SERVICE_NAME="permit-orchestrator" \
REGION="us-central1" \
PROJECT_ID="my-permit-orchestrator" \
./deploy/deploy_cloudrun.sh
```

### Option B: Step-by-Step Manual Deployment

1. **Build Container Image with Cloud Build**:
   ```bash
   PROJECT_ID=$(gcloud config get-value project)
   IMAGE_NAME="gcr.io/${PROJECT_ID}/permit-orchestrator:latest"

   gcloud builds submit --tag "${IMAGE_NAME}" -f deploy/cloudrun.Dockerfile .
   ```

2. **Deploy to Cloud Run**:
   ```bash
   gcloud run deploy permit-orchestrator \
       --image "${IMAGE_NAME}" \
       --region us-central1 \
       --platform managed \
       --port 8080 \
       --memory 1Gi \
       --cpu 1 \
       --allow-unauthenticated \
       --set-env-vars "DATABASE_PATH=/tmp/permits.db,UPLOADS_DIR=/tmp/uploads"
   ```

3. **Verify Deployment**:
   Retrieve the service URL:
   ```bash
   URL=$(gcloud run services describe permit-orchestrator --region us-central1 --format='value(status.url)')
   echo "Application URL: ${URL}"

   # Check health probe
   curl -s "${URL}/health"

   # Check hello probe
   curl -s "${URL}/hello"
   ```

---

## Verification Endpoints

- **Web UI**: Open `https://<service-name>.run.app` in any browser.
- **Health Check**: `GET /health` → `{"status": "healthy"}`
- **Verification Probe**: `GET /hello` → `{"status": "operational", "message": "Permit Orchestrator Cloud Run verification probe"}`
- **Permit Submission**: `POST /submit`
- **Intake Parser**: `POST /intake/parse` & `GET /intake/sample-plans`
