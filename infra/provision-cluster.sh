#!/usr/bin/env bash
# =============================================================================
# CockroachDB Serverless Cluster Provisioning via ccloud CLI
# This script counts toward the hackathon requirement: ccloud CLI usage
# =============================================================================

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-sre-memory-agent}"
CLOUD_PROVIDER="AWS"
REGION="us-east-1"
PLAN="SERVERLESS"

echo "=========================================="
echo "SRE Memory Agent - CockroachDB Provisioning"
echo "=========================================="

# Check ccloud CLI is installed
if ! command -v ccloud &> /dev/null; then
    echo "ERROR: ccloud CLI not found. Install from: https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started"
    exit 1
fi

# Check authentication
echo "[1/6] Verifying ccloud authentication..."
ccloud auth whoami --output json || {
    echo "ERROR: Not authenticated. Run: ccloud auth login"
    exit 1
}

# Create cluster
echo "[2/6] Creating CockroachDB Serverless cluster: ${CLUSTER_NAME}..."
CLUSTER_OUTPUT=$(ccloud cluster create \
    --name "${CLUSTER_NAME}" \
    --cloud "${CLOUD_PROVIDER}" \
    --region "${REGION}" \
    --plan "${PLAN}" \
    --output json)

CLUSTER_ID=$(echo "${CLUSTER_OUTPUT}" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Cluster ID: ${CLUSTER_ID}"

# Wait for cluster to be ready
echo "[3/6] Waiting for cluster to be ready..."
for i in $(seq 1 30); do
    STATUS=$(ccloud cluster list --output json | python3 -c "
import sys, json
clusters = json.load(sys.stdin)
for c in clusters:
    if c['id'] == '${CLUSTER_ID}':
        print(c['state'])
        break
")
    if [ "${STATUS}" = "CREATED" ] || [ "${STATUS}" = "READY" ]; then
        echo "Cluster is ready!"
        break
    fi
    echo "  Status: ${STATUS} (attempt ${i}/30)"
    sleep 10
done

# Create service account for the agent (least-privilege)
echo "[4/6] Creating service account for agent..."
SA_OUTPUT=$(ccloud service-account create \
    --name "sre-agent-sa" \
    --description "Service account for SRE Memory Agent (read vectors, write agent_memory)" \
    --output json)

SA_ID=$(echo "${SA_OUTPUT}" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "Service Account ID: ${SA_ID}"

# Create API key for the service account
echo "[5/6] Creating API key for service account..."
API_KEY_OUTPUT=$(ccloud cluster api-key create \
    --cluster "${CLUSTER_ID}" \
    --service-account "${SA_ID}" \
    --output json)

API_KEY=$(echo "${API_KEY_OUTPUT}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['id'])")
API_SECRET=$(echo "${API_KEY_OUTPUT}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['secret'])")

# Verify cluster
echo "[6/6] Verifying cluster..."
ccloud cluster list --output json | python3 -c "
import sys, json
clusters = json.load(sys.stdin)
for c in clusters:
    if c['id'] == '${CLUSTER_ID}':
        print(f\"  Name: {c['name']}\")
        print(f\"  State: {c['state']}\")
        print(f\"  Region: {c.get('regions', ['unknown'])}\")
        break
"

echo ""
echo "=========================================="
echo "PROVISIONING COMPLETE"
echo "=========================================="
echo ""
echo "Cluster ID:      ${CLUSTER_ID}"
echo "Cluster Name:    ${CLUSTER_NAME}"
echo "Region:          ${REGION}"
echo "Service Account: ${SA_ID}"
echo ""
echo "Connection details (add to .env):"
echo "  COCKROACHDB_CLUSTER_ID=${CLUSTER_ID}"
echo "  COCKROACHDB_USER=${API_KEY}"
echo "  COCKROACHDB_PASSWORD=${API_SECRET}"
echo "  COCKROACHDB_HOST=${CLUSTER_ID}.aws-${REGION}.cockroachlabs.cloud"
echo ""
echo "IMPORTANT: Save the API secret above - it cannot be retrieved later."
echo ""
echo "Next steps:"
echo "  1. Update .env with connection details"
echo "  2. Run schema migration: python data/migrations/apply.py"
echo "  3. Seed data: python data/seed_incidents.py"
