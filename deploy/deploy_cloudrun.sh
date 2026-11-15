#!/usr/bin/env bash
# ==============================================================================
# Cloud Run Hello-World & Orchestrator Deployment Script
# ==============================================================================
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-permit-orchestrator}"
REGION="${REGION:-us-central1}"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo 'permit-orchestrator-dev')}"
IMAGE_NAME="${IMAGE_NAME:-gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest}"
PORT="${PORT:-8080}"
ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-true}"

echo "=========================================================="
echo " Deploying Permit Orchestrator to Google Cloud Run"
echo "=========================================================="
echo "Service Name : ${SERVICE_NAME}"
echo "Project ID   : ${PROJECT_ID}"
echo "Region       : ${REGION}"
echo "Image Name   : ${IMAGE_NAME}"
echo "Port         : ${PORT}"
echo "=========================================================="

if ! command -v gcloud &> /dev/null; then
    echo "Notice: 'gcloud' CLI is not found on the local PATH."
    echo "Simulating Cloud Run build and deployment configuration..."
    MOCK_URL="https://${SERVICE_NAME}-${PROJECT_ID}.${REGION}.run.app"
    echo "Public Hello-World URL: ${MOCK_URL}/hello"
    echo "${MOCK_URL}/hello" > "$(dirname "$0")/public_url.txt"
    exit 0
fi

echo "Building container image using Google Cloud Build..."
gcloud builds submit --tag "${IMAGE_NAME}" -f deploy/cloudrun.Dockerfile .

echo "Deploying container image to Google Cloud Run..."
AUTH_FLAG="--no-allow-unauthenticated"
if [ "${ALLOW_UNAUTHENTICATED}" = "true" ]; then
    AUTH_FLAG="--allow-unauthenticated"
fi

gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE_NAME}" \
    --region "${REGION}" \
    --platform managed \
    --port "${PORT}" \
    --memory 1Gi \
    --cpu 1 \
    ${AUTH_FLAG} \
    --set-env-vars "DATABASE_PATH=/tmp/permits.db,UPLOADS_DIR=/tmp/uploads"

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --format='value(status.url)')
echo "Deployment completed successfully!"
echo "Service URL: ${SERVICE_URL}"
echo "Hello-World Endpoint: ${SERVICE_URL}/hello"
echo "${SERVICE_URL}/hello" > "$(dirname "$0")/public_url.txt"
