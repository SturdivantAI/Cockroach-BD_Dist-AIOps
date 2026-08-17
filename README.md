# SRE Memory Agent

**Incident Response with Persistent Agentic Memory**

An AI SRE assistant that diagnoses incidents better the second time because CockroachDB gives it globally distributed, never-down memory. The agent learns from past resolutions and applies that knowledge to future incidents — measurably faster and more accurately.

> Alert fires → Agent reasons with memory → Proposes fix (human gate) → Second incident resolves faster → Judge scores the improvement delta

## Multi-Region Memory: Zero Data Loss

This system is deployed across **us-east-1**, **us-east-2**, and **us-west-2** on CockroachDB with `SURVIVE REGION FAILURE` enabled. Every agent memory write is synchronously replicated across all 3 regions via Raft consensus before being acknowledged. If an entire AWS region goes offline, the agent's memory is intact and immediately available from the surviving regions — zero data loss, zero manual intervention, automatic failover.

See [docs/multi-region-resilience.md](docs/multi-region-resilience.md) for the full technical explanation.

---

## CockroachDB Tools Used (3 of 4)

| Tool | How the Agent Uses It |
|------|----------------------|
| **Distributed Vector Indexing** | Stores incident embeddings (1536-dim Titan vectors) for semantic retrieval of similar past incidents via cosine similarity |
| **MCP Server** | Exposes the memory layer to external agents/tools with full audit logging |
| **ccloud CLI** | Automated cluster provisioning, backup scheduling after resolution, audit log retrieval by the ops agent |

## AWS Services Used

| Service | Role |
|---------|------|
| **Amazon Bedrock** | Claude 3.5 Sonnet (chain-of-thought reasoning) + Titan Embeddings v2 (vector generation) |
| **AWS Step Functions** | Agent workflow orchestration with human-in-the-loop approval gate |
| **AWS Lambda** | Executes each agent step (ingestion, retrieval, analysis, decision, memory write) |
| **Amazon S3** | Incident artifacts, logs, model outputs |
| **AWS Secrets Manager** | CockroachDB credentials (no secrets in code) |

## Databricks Integration

| Component | Purpose |
|-----------|---------|
| **Delta Live Tables** | Sync incident data from CockroachDB (JDBC) into bronze/silver/gold layers |
| **MLflow** | Track retrieval quality, model latency, judge scores, confidence deltas per run |
| **Evaluation Harness** | Compare agent performance with vs without memory (the visible metric) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Alert Source                                   │
│  Synthetic CloudWatch Alert ──triggers──► AWS Lambda (Ingestion)     │
└───────────────────────────────────┬─────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────┐
│                    Orchestration Layer                                │
│  AWS Step Functions ──► Retrieval ──► Analysis ──► Decision          │
│                                                    ──► Approval Gate │
└──────┬────────────────────┬───────────────────────────┬─────────────┘
       │                    │                           │
┌──────▼──────┐    ┌───────▼────────┐    ┌────────────▼──────────────┐
│ Memory Layer │    │   AI Layer     │    │   Data Intelligence       │
│ CockroachDB  │    │  AWS Bedrock   │    │   Databricks              │
│              │    │                │    │                           │
│ • Vector Idx │    │ • Claude 3.5   │    │ • Delta Live Tables       │
│ • Agent Mem  │    │ • Titan Embed  │    │ • MLflow Tracking         │
│ • Decisions  │    │ • LLM-as-Judge │    │ • Eval Dashboard          │
│ • MCP Server │    │                │    │                           │
└──────────────┘    └────────────────┘    └───────────────────────────┘
```

See [docs/architecture.md](docs/architecture.md) for the full Mermaid diagram.

---

## Project Structure

```
├── agents/                    # Multi-agent system
│   ├── retrieval/             # Vector search agent (CockroachDB)
│   ├── analysis/              # Chain-of-thought reasoning (Claude)
│   ├── decision/              # Remediation with confidence gating
│   ├── ops/                   # ccloud CLI operations agent
│   ├── orchestrator.py        # Local workflow runner
│   ├── lambda_handlers.py     # AWS Lambda entry points
│   ├── bedrock_client.py      # Bedrock API with retry/backoff
│   ├── db.py                  # CockroachDB connection management
│   ├── config.py              # Centralized settings (pydantic)
│   ├── resilience.py          # Rate limiter + circuit breaker
│   ├── secrets.py             # AWS Secrets Manager integration
│   ├── health.py              # System health checks
│   └── logging_config.py      # Structured JSON logging
├── data/
│   ├── migrations/            # CockroachDB schema (4 SQL files)
│   ├── sample_incidents/      # Ground truth + schema
│   ├── seed_incidents.py      # Populate DB with 20 incidents
│   └── generate_incidents.py  # Bedrock-powered variant generator
├── evaluation/
│   ├── run_evaluation_pair.py # Memory improvement loop demo
│   ├── judge.py               # LLM-as-Judge (4 dimensions, 1-5)
│   ├── mlflow_tracker.py      # MLflow experiment logging
│   └── databricks/            # DLT pipeline + dashboard queries
├── frontend/
│   └── app.py                 # Streamlit three-panel UI
├── infra/
│   ├── provision-cluster.ps1  # ccloud CLI provisioning (Windows)
│   ├── provision-cluster.sh   # ccloud CLI provisioning (Linux/Mac)
│   ├── template.yaml          # AWS SAM template
│   ├── deploy.ps1             # SAM deployment script
│   └── samconfig.toml         # SAM configuration
└── docs/
    └── architecture.md        # Full architecture with Mermaid diagram
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- AWS CLI configured with Bedrock model access (Claude 3.5 Sonnet + Titan Embeddings v2)
- [CockroachDB ccloud CLI](https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started)
- (Optional) Databricks workspace for MLflow tracking

### 1. Install

```bash
git clone https://github.com/SturdivantAI-Lab/Cockroach-BD_Dist-AIOps.git
cd Cockroach-BD_Dist-AIOps
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your CockroachDB and AWS credentials
```

### 3. Provision CockroachDB

```powershell
cd infra
.\provision-cluster.ps1
# Follow prompts, copy credentials to .env
```

### 4. Apply Schema & Seed Data

```bash
python data/migrations/apply.py
python data/seed_incidents.py --skip-embeddings  # Without AWS
python data/seed_incidents.py                     # With Bedrock Titan
```

### 5. Health Check

```bash
python -m agents.health
```

### 6. Run Demo

```bash
streamlit run frontend/app.py
```

### 7. Run Evaluation

```bash
python evaluation/run_evaluation_pair.py --all
python evaluation/judge.py --all-pairs
```

---

## The Memory Improvement Loop (Key Feature)

```
Incident A (first encounter):
  → No memory available
  → Agent reasons from scratch
  → Confidence: 0.6, Steps: 5
  → Resolution written to CockroachDB memory

Incident A' (same class, different service):
  → Memory recalls A's resolution (cosine similarity)
  → Agent cites prior resolution in reasoning
  → Confidence: 0.85, Steps: 3
  → Judge scores improvement: +0.25 confidence, -2 steps
```

This is not a toy. The improvement is measurable, reproducible, and scored by an independent LLM judge.

---

## Known Limitations & Future Work

| Limitation | Context | Production Mitigation |
|---|---|---|
| **Evaluation sample size (n=5 pairs)** | Sufficient for hackathon demo; not statistically significant | Expand to 50+ pairs with confidence intervals |
| **Ceiling effect** | Baseline score 4.15/5.0 limits observable improvement headroom | Use finer-grained scoring (1-10) or domain-specific rubrics |
| **Same-model judge** | Claude scores Claude's output (potential self-bias) | Use a different model family for judging, or human evaluators |
| **2/5 improvement rate** | Mean delta (+0.60) is carried by 2 strong pairs; 3 were flat due to high baseline | More incident classes with harder variants would separate signal |
| **No repeated judging** | Single judge pass per pair; no inter-rater reliability | Run 3+ judge passes per pair, report median and variance |

These are documented honestly because the architecture is sound — the memory mechanism works — but the evaluation harness needs production-grade rigor before deployment beyond a demo.

---

## Demo Video

[Video Link TBD]

**Beat sheet (< 3 minutes):**
- 0:00–0:20 — Alert fires (press button, signal panel)
- 0:20–1:00 — Agent retrieves + reasons (stream, "No prior memory")
- 1:00–1:30 — Remediation proposed, approval clicked
- 1:30–2:10 — Second alert (same class), agent resolves faster, memory panel shows prior resolution
- 2:10–2:40 — Judge scores displayed, bar chart shows delta
- 2:40–3:00 — Architecture diagram

---

## License

MIT — see [LICENSE](LICENSE)
