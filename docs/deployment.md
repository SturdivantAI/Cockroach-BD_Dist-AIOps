# Deploying the v2 demo

The deployment target is a Streamlit process with saved evidence replay and an optional live proposal workflow. Operational remediation is not implemented. Building an image does not provision cloud infrastructure or publish a service.

## Build and inspect locally

From the v2 repository root, build the image:

```bash
docker build -t sre-agent-v2:0.2.0 .
```

Start a local container preview:

```bash
docker run --rm --name sre-agent-v2-preview -p 127.0.0.1:8501:8501 sre-agent-v2:0.2.0
```

If the existing local Streamlit process occupies port 8501, stop it first or choose a different host port. Replay works without credentials. `DEPLOYMENT_MODE=hosted` is the image default; hosted live proposals remain disabled until an access code is configured.

The runtime dependencies are pinned in `requirements.lock`, and the Dockerfile pins the Python base image by digest. Record the resulting application image digest when publishing so the deployed artifact can be identified.

## Enable a hosted live workflow

Copy the deployment template:

```bash
cp -n .env.deployment.example .env.deployment
```

Populate the database endpoint, managed-secret ARN, and Nebius key. Set a random `DEMO_ACCESS_CODE`; blank disables the hosted live form. Do not add the completed file to Git or the image. Supply the access code separately in the private judging instructions. An access code limits demo access; it is not a replacement for production identity management or request quotas.

Use an AWS workload role rather than embedding AWS credentials. The live path needs:

- `secretsmanager:GetSecretValue` on the exact configured database secret.
- `bedrock:InvokeModel` for `amazon.titan-embed-text-v2:0` in the selected region.
- `kms:Decrypt` on the relevant key only if the secret uses a customer-managed KMS key.

The application host needs network access to PostgreSQL on TCP 5432 and HTTPS access to Nebius and AWS APIs. Add a database security-group rule from the application security group. The developer's single-IP rule does not authorize a deployed host. Preserve certificate verification with the included public RDS CA bundle.

Run a local preview with runtime configuration:

```bash
docker run --rm --name sre-agent-v2-preview --env-file .env.deployment -p 127.0.0.1:8501:8501 sre-agent-v2:0.2.0
```

A local container also needs a working AWS credential provider to use live mode. The deployment template deliberately contains no AWS access keys. An image running on AWS should receive its credentials from the assigned workload role.

## Release checks

Use the saved stale-memory replay first. Ordinary memory should show the obsolete v0 recommendation; validated memory should show `configuration_mismatch` and v1. Check all repeat selectors and the report download.

For live testing, enter the private demo code and generate one proposal. This incurs inference usage and writes records. Confirm completion reports `proposal_recorded`, `not_executed`, and no errors. A successful proposal write does not establish a correct diagnosis or service recovery.

The AWS hosting configuration uses a separate `sre_agent_v2_app` database login and managed secret. It can read the six application tables and insert incidents, agent memory, and decisions. It cannot insert execution or verification records. The provisioning check tests allowed inserts in a rolled-back transaction and confirms both evidence-table inserts are denied. Keep the trusted verification writer outside model-controlled input paths.

## AWS hosting stack

`infra/v2/service.json` defines one Fargate task behind an Application Load Balancer and CloudFront. Viewer traffic uses HTTPS. CloudFront connects to the load balancer over HTTP; this is not end-to-end TLS. The load balancer accepts only the CloudFront origin-facing prefix list and requires a secret origin header. The application security group accepts port 8501 only from the load balancer, and PostgreSQL accepts port 5432 from the application group.

The task role can retrieve only the application database secret and invoke the configured Titan embedding model. The execution role injects the Nebius key and demo access code from the runtime secret. Neither secret is embedded in the image. Public replay does not require the access code; live proposal generation does.

`infra/v2/prepare_runtime.py` provisions the repository, secrets, and database permissions. `infra/v2/push_image.py` checks the local release identity and uploads it to immutable ECR storage. `infra/v2/create_stack.py` creates the stack using that image digest. These scripts create billable AWS resources. Account-specific deployment state is ignored by Git.

A public deployment needs HTTPS and a configured live access code. Preserve the previous image digest and runtime configuration before replacement. If the new build fails its health check or replay, restore the previous image. Do not roll back the database by dropping tables; this release preparation does not change the applied schema.

The hackathon rules require a working demo or test build and availability through the judging period. Plan hosting through December 15, 2026, and verify the official rules before final submission: https://nebiusglobalaihackathon.devpost.com/rules .

## Files excluded from the image

The Docker build excludes `.env` files, `.aws`, Streamlit secrets, private keys, local virtual environments, and Git metadata. The one included PEM file is the public AWS RDS trust bundle. `tools/check_secrets.py` checks publishable Git files for known local values and credential patterns; it is not an exhaustive security scanner.
