# =============================================================================
# Deploy AWS Resources via SAM CLI (Windows PowerShell)
# =============================================================================

$ErrorActionPreference = "Stop"

$StackName = if ($env:STACK_NAME) { $env:STACK_NAME } else { "sre-memory-agent-dev" }
$Region = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$Environment = if ($env:ENVIRONMENT) { $env:ENVIRONMENT } else { "dev" }
$S3BucketName = "sre-memory-agent-artifacts"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "SRE Memory Agent - AWS Deployment" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Stack:       $StackName"
Write-Host "Region:      $Region"
Write-Host "Environment: $Environment"
Write-Host ""

# Check SAM CLI is installed
if (-not (Get-Command "sam" -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: AWS SAM CLI not found. Install from: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html" -ForegroundColor Red
    exit 1
}

# Validate template
Write-Host "[1/3] Validating SAM template..." -ForegroundColor Yellow
sam validate --template-file template.yaml --region $Region
Write-Host "  Template is valid." -ForegroundColor Green

# Build
Write-Host "[2/3] Building SAM application..." -ForegroundColor Yellow
sam build --template-file template.yaml

# Deploy
Write-Host "[3/3] Deploying stack..." -ForegroundColor Yellow
sam deploy `
    --stack-name $StackName `
    --region $Region `
    --resolve-s3 `
    --capabilities CAPABILITY_NAMED_IAM `
    --no-confirm-changeset `
    --parameter-overrides `
        "Environment=$Environment" `
        "S3BucketName=$S3BucketName" `
        "CockroachDBSecretArn=arn:aws:secretsmanager:${Region}:$($env:AWS_ACCOUNT_ID):secret:sre-agent/cockroachdb-${Environment}"

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "DEPLOYMENT COMPLETE" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Verify deployment:" -ForegroundColor Yellow
Write-Host "  aws stepfunctions describe-state-machine --state-machine-arn <ARN_FROM_OUTPUTS>"
Write-Host "  aws s3 ls s3://$S3BucketName-$Environment/"
