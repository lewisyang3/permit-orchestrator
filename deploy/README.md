# Permit Orchestrator - Cloud Run Deployment (Task T6)

This directory contains the Cloud Run deployment configuration and automation for the Permit Orchestrator service.

## Components

- **`cloudrun.Dockerfile`**: Lightweight container image tailored for Cloud Run serverless execution. Automatically binds to the injected `$PORT` environment variable (default: `8080`) and runs the orchestrator with non-root privileges.
- **`deploy_cloudrun.sh`**: Deployment automation script utilizing Google Cloud Build and Cloud Run CLI (`gcloud run deploy`), with graceful simulation mode when running outside of GCP authenticated environments.
- **`public_url.txt`**: Records the active or provisioned public Hello-World service URL.

## Endpoints

- `GET /health`: Liveness and readiness probe responding with HTTP 200 `{"status": "healthy"}`.
- `GET /hello`: Public hello-world endpoint responding with service metadata, operational status, and UTC timestamp.
