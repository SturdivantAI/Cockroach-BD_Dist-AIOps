# SRE-Agent V2: Trust-Aware Agentic Memory

AI incident response with memory it can trust.

SRE-Agent V2 tests whether validating historical incident memory prevents an agent from reusing obsolete or altered recommendations. NVIDIA Nemotron runs through Nebius Token Factory. PostgreSQL with pgvector stores incidents and linked execution evidence; Amazon Bedrock Titan produces 1,024-dimensional embeddings.

The application offers saved experimental replay and a live validated workflow. The live workflow records a proposed action. It does not execute remediation or claim that a service recovered.

## Run the replay

Use the v2 checkout containing this README. The archived v1 main branch may not contain these files. Python 3.12 on Linux is the tested environment. Run each command separately from the repository root.

Create an environment:

```bash
python3.12 -m venv .venv
```

Install the pinned runtime:

```bash
.venv/bin/python -m pip install -r requirements.lock
```

Start the interface:

```bash
.venv/bin/python -m streamlit run frontend/app.py --server.address 127.0.0.1 --server.port 8501
```

Open http://127.0.0.1:8501. Saved replay requires no API keys, database connection, or inference calls. Select **Stale experience** and inspect the ordinary and validated recommendations. The repository includes the complete 21-episode report.

## What the pilot showed

The fixed experiment used one synthetic release-regression incident and three calls per condition/mode. The no-memory control was shared.

| Memory | Ordinary: correct / attempts | Validated: correct / attempts |
|---|---:|---:|
| Clean | 3/3 | 3/3 |
| Stale | 0/3 | 3/3 |
| Poisoned | 3/3 | 3/3 |
| None | 3/3 shared control | Shared control |

Ordinary stale memory recommended release v0, which belonged to an obsolete configuration contract. Validated memory withheld those records; the model recommended v1. No unsafe recommendations occurred in this 21-call run. The run used 11,964 reported tokens; validated episodes added approximately 0.67 to 0.70 seconds, mostly evidence lookup and connection setup.

These are repeated calls on one synthetic incident. They do not establish general reliability, statistical significance, or operational recovery. An earlier prompt version produced an unsafe recommendation under poisoned memory; preserve that result separately rather than pooling prompt versions.

- [Complete fixed-run evidence](evaluation/results/controlled-comparison-20261024T135140Z-3c5e0acb.json)
- [Independent score and trace recomputation](evaluation/results/repeat-audit-20261024T135140Z-3c5e0acb.json)

The replay checks prompt fingerprints and recomputes structured-action scores. This validates internal consistency, not the authenticity of arbitrary uploaded evidence.

## Live configuration

Copy the example without overwriting an existing configuration:

```bash
cp -n .env.example .env
```

Populate `NEBIUS_API_KEY`, `RDS_HOST`, and `RDS_SECRET_ARN`. Keep configuration files local. The application obtains the database username and password from the managed RDS secret. The public AWS RDS certificate bundle is included at `certs/aws-rds-global.pem`.

Use AWS credentials through the normal SDK credential chain. On hosted AWS infrastructure, use a workload role. Live inference needs Titan model invocation and access to the configured database secret. Database authentication, a reachable endpoint, and an inbound rule from the application host are also required.

The existing project database is already initialized. For a different, empty PostgreSQL database with pgvector available, initialize it once:

```bash
.venv/bin/python -m data.bootstrap_postgresql
```

The initializer refuses to overwrite a database containing tables. Do not use the original CockroachDB migration runner for PostgreSQL. The new schema is `data/migrations/postgresql/001_initial.sql`.

Optional synthetic incident seeds make billable embedding requests:

```bash
.venv/bin/python -m data.seed_incidents
```

Seed fixtures are not verified operational history. Default validation withholds unverified memory. The experiment runner opts into simulation evidence explicitly; normal live retrieval does not.

## Architecture and trust boundary

```text
Alert -> ingest -> retrieve distinct incidents + prior agent records
      -> validate against execution/verification ledger
      -> NVIDIA analysis -> NVIDIA decision -> record unexecuted proposal
```

Validation checks the recorded execution outcome, evidence age, service scope, configuration compatibility, and incident fingerprint. It covers retrieved incidents and prior agent records. Model confidence and a memory's own success flag cannot establish a verified outcome. Exact cosine search is used for the pilot; approximate HNSW retrieval is deferred.

The ledger is a trust boundary. A trusted verifier must create its evidence. The current simulator is a small deterministic state machine with declared release-health assumptions. It is not a production service monitor.

## Tests and experiments

Install development tools when needed:

```bash
.venv/bin/python -m pip install -e '.[dev]'
```

Run offline tests:

```bash
.venv/bin/python -m pytest tests -q
```

Check publishable files for known local secrets and common credential patterns:

```bash
.venv/bin/python tools/check_secrets.py
```

Run the database-backed gate smoke test. It writes five synthetic records but makes no inference calls:

```bash
.venv/bin/python -m evaluation.run_memory_validation_smoke
```

Run the controlled comparison with three repeats. It writes two synthetic fixtures and makes 21 billable NVIDIA calls:

```bash
.venv/bin/python -m evaluation.run_controlled_comparison --repeats 3
```

Reports are saved under `~/sre-agent-v2-results/`, with code fingerprints and request timings. Ordinary and validated arms receive the same current alert. The selected memory is the experimental intervention. Filtering can reduce context to the no-memory control; the experiment does not claim that the model itself learns to detect contamination.

## Deployment and inherited code

See [deployment instructions](docs/deployment.md) for container startup, hosted access, and rollback. A hosted live form requires a demo access code; replay remains public. The container includes no local environment file or AWS credentials.

V2 inherits the incident agents, fixtures, and local orchestration from [Cockroach-BD_Dist-AIOps](https://github.com/SturdivantAI/Cockroach-BD_Dist-AIOps). V2 adds the PostgreSQL schema, managed-secret connection path, Nebius provider, evidence gate, separate proposal persistence, controlled simulator, evaluation reports, and comparison interface. The old Step Functions, CockroachDB operations, Databricks, and judge code remain historical integrations; they are not the tested v2 deployment path. Optional dependencies for those integrations are available through `.[legacy]`.

The [v1 README](docs/v1-foundation.md) is preserved for historical context. MIT license: [LICENSE](LICENSE).
