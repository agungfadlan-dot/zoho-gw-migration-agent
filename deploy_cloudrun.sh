#!/usr/bin/env bash
# ==============================================================================
# Deploy Zoho to Google Workspace Migration Agent to Google Cloud Run
# ==============================================================================

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-zoho-gw-migration-agent}"
REGION="${REGION:-asia-southeast2}" # Jakarta (or us-central1)
CPU="${CPU:-1}"
MEMORY="${MEMORY:-2Gi}"
TIMEOUT="${TIMEOUT:-3600}"

echo "================================================================================"
echo "  Deploying ${SERVICE_NAME} to Google Cloud Run (${REGION})"
echo "================================================================================"

# Verify gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "Error: gcloud CLI is not installed. Please install the Google Cloud SDK."
    exit 1
fi

PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
if [ -z "${PROJECT_ID}" ]; then
    echo "Error: No active GCP project configured. Run 'gcloud config set project <PROJECT_ID>'."
    exit 1
fi

echo "  • GCP Project: ${PROJECT_ID}"
echo "  • Service Name: ${SERVICE_NAME}"
echo "  • Region: ${REGION}"
echo "  • Specs: ${CPU} vCPU, ${MEMORY} RAM, Timeout ${TIMEOUT}s"
echo "--------------------------------------------------------------------------------"

# Build and deploy from source directly to Cloud Run
gcloud run deploy "${SERVICE_NAME}" \
    --source . \
    --region "${REGION}" \
    --platform managed \
    --cpu "${CPU}" \
    --memory "${MEMORY}" \
    --timeout "${TIMEOUT}" \
    --min-instances 0 \
    --max-instances 1 \
    --no-allow-unauthenticated

echo "================================================================================"
echo "  Deployment Complete! Open your Cloud Run URL to access the Migration Web UI."
echo "================================================================================"
