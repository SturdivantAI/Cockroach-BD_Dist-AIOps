# =============================================================================
# CockroachDB Serverless Cluster Provisioning via ccloud CLI (Windows PowerShell)
# This script counts toward the hackathon requirement: ccloud CLI usage
# =============================================================================

$ErrorActionPreference = "Stop"

$ClusterName = if ($env:CLUSTER_NAME) { $env:CLUSTER_NAME } else { "sre-memory-agent" }
$CloudProvider = "AWS"
$PrimaryRegion = "us-east-1"
$SecondaryRegion = "us-east-2"
$TertiaryRegion = "us-west-2"
$Plan = "STANDARD"  # Standard plan required for multi-region

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "SRE Memory Agent - CockroachDB Provisioning" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Check ccloud CLI is installed
if (-not (Get-Command "ccloud" -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: ccloud CLI not found. Install from: https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started" -ForegroundColor Red
    exit 1
}

# Check authentication
Write-Host "[1/6] Verifying ccloud authentication..." -ForegroundColor Yellow
try {
    $authInfo = ccloud auth whoami --output json | ConvertFrom-Json
    Write-Host "  Authenticated as: $($authInfo.email)" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Not authenticated. Run: ccloud auth login" -ForegroundColor Red
    exit 1
}

# Create multi-region cluster
Write-Host "[2/6] Creating CockroachDB Multi-Region cluster: $ClusterName..." -ForegroundColor Yellow
Write-Host "  Primary: $PrimaryRegion | Secondary: $SecondaryRegion | Tertiary: $TertiaryRegion" -ForegroundColor Gray
$clusterJson = ccloud cluster create `
    --name $ClusterName `
    --cloud $CloudProvider `
    --region "$PrimaryRegion,$SecondaryRegion,$TertiaryRegion" `
    --plan $Plan `
    --output json | ConvertFrom-Json

$ClusterId = $clusterJson.id
Write-Host "  Cluster ID: $ClusterId" -ForegroundColor Green

# Wait for cluster to be ready
Write-Host "[3/6] Waiting for cluster to be ready..." -ForegroundColor Yellow
for ($i = 1; $i -le 30; $i++) {
    $clusters = ccloud cluster list --output json | ConvertFrom-Json
    $thisCluster = $clusters | Where-Object { $_.id -eq $ClusterId }
    $status = $thisCluster.state

    if ($status -eq "CREATED" -or $status -eq "READY") {
        Write-Host "  Cluster is ready!" -ForegroundColor Green
        break
    }
    Write-Host "  Status: $status (attempt $i/30)" -ForegroundColor Gray
    Start-Sleep -Seconds 10
}

# Create service account
Write-Host "[4/6] Creating service account for agent..." -ForegroundColor Yellow
$saJson = ccloud service-account create `
    --name "sre-agent-sa" `
    --description "Service account for SRE Memory Agent" `
    --output json | ConvertFrom-Json

$SaId = $saJson.id
Write-Host "  Service Account ID: $SaId" -ForegroundColor Green

# Create API key
Write-Host "[5/6] Creating API key for service account..." -ForegroundColor Yellow
$apiKeyJson = ccloud cluster api-key create `
    --cluster $ClusterId `
    --service-account $SaId `
    --output json | ConvertFrom-Json

$ApiKey = $apiKeyJson.id
$ApiSecret = $apiKeyJson.secret

# Verify cluster
Write-Host "[6/6] Verifying cluster..." -ForegroundColor Yellow
$clusters = ccloud cluster list --output json | ConvertFrom-Json
$thisCluster = $clusters | Where-Object { $_.id -eq $ClusterId }
Write-Host "  Name: $($thisCluster.name)" -ForegroundColor Green
Write-Host "  State: $($thisCluster.state)" -ForegroundColor Green

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "PROVISIONING COMPLETE" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Cluster ID:      $ClusterId"
Write-Host "Cluster Name:    $ClusterName"
Write-Host "Regions:         $PrimaryRegion, $SecondaryRegion, $TertiaryRegion"
Write-Host "Service Account: $SaId"
Write-Host ""
Write-Host "Connection details (add to .env):" -ForegroundColor Yellow
Write-Host "  COCKROACHDB_CLUSTER_ID=$ClusterId"
Write-Host "  COCKROACHDB_USER=$ApiKey"
Write-Host "  COCKROACHDB_PASSWORD=$ApiSecret"
Write-Host "  COCKROACHDB_HOST=$ClusterId.aws-$PrimaryRegion.cockroachlabs.cloud"
Write-Host ""
Write-Host "IMPORTANT: Save the API secret above - it cannot be retrieved later." -ForegroundColor Red
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Update .env with connection details"
Write-Host "  2. Run schema migration: python data/migrations/apply.py"
Write-Host "  3. Seed data: python data/seed_incidents.py"
