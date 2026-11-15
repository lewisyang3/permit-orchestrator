#!/usr/bin/env bash
# Deploy the permit orchestrator stack to Google Cloud Run.
#
#   PROJECT_ID=my-project ./scripts/deploy_cloud_run.sh
#
# Optional: REGION (default us-central1). If a Secret Manager secret named
# gemini-api-key exists, it is mounted as GEMINI_API_KEY on all services.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-us-central1}"
REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/permit-orchestrator"
DEPARTMENTS=(zoning fire water structural)

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com

gcloud artifacts repositories describe permit-orchestrator --location "$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create permit-orchestrator \
       --repository-format docker --location "$REGION"

gcloud builds submit --config cloudbuild.yaml --substitutions "_REPO=${REPO}" .

SECRET_FLAGS=()
if gcloud secrets describe gemini-api-key >/dev/null 2>&1; then
  SECRET_FLAGS=(--set-secrets "GEMINI_API_KEY=gemini-api-key:latest")
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
  gcloud secrets add-iam-policy-binding gemini-api-key \
    --member "serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role roles/secretmanager.secretAccessor >/dev/null
fi

# Near-$0 demo mode: scale to zero and bill only while requests are in flight.
# max-instances 1 keeps in-memory state (permits, faults, SSE) on one instance.
# The UI holds an SSE stream open during a review, which keeps the orchestrator's
# CPU allocated for the background saga. --cpu-boost shortens cold starts so the
# orchestrator's 2s department timeout + retries can absorb them.
COMMON_FLAGS=(
  --region "$REGION" --allow-unauthenticated
  --min-instances 0 --max-instances 1 --cpu-throttling --cpu-boost
  --port 8000 --memory 512Mi
)

DEPT_URL_VARS=""
for dept in "${DEPARTMENTS[@]}"; do
  gcloud run deploy "permit-${dept}" --image "${REPO}/department:latest" \
    "${COMMON_FLAGS[@]}" ${SECRET_FLAGS[@]+"${SECRET_FLAGS[@]}"} \
    --set-env-vars "DEPARTMENT=${dept},RULE_FILE=/app/rules/${dept}.yaml,GEMINI_MODEL=gemini-2.5-flash"
  url="$(gcloud run services describe "permit-${dept}" --region "$REGION" --format 'value(status.url)')"
  DEPT_URL_VARS="${DEPT_URL_VARS},$(echo "$dept" | tr a-z A-Z)_URL=${url}"
done

gcloud run deploy permit-orchestrator --image "${REPO}/orchestrator:latest" \
  "${COMMON_FLAGS[@]}" ${SECRET_FLAGS[@]+"${SECRET_FLAGS[@]}"} --timeout 3600 \
  --set-env-vars "DATABASE_PATH=/tmp/permits.db,GEMINI_MODEL=gemini-2.5-flash${DEPT_URL_VARS}"

URL="$(gcloud run services describe permit-orchestrator --region "$REGION" --format 'value(status.url)')"
curl -fsS "${URL}/health" && echo
echo "Public demo: ${URL}"
