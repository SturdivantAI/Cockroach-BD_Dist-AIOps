# Architecture

## System Overview

```mermaid
graph TB
    subgraph "Alert Source"
        A[Synthetic CloudWatch Alert] -->|triggers| B[AWS Lambda - Ingestion]
    end

    subgraph "Orchestration Layer"
        B --> C[AWS Step Functions - Agent Workflow]
        C -->|step 1| D[Retrieval Agent]
        C -->|step 2| E[Analysis Agent]
        C -->|step 3| F[Decision Agent]
        C -->|step 4| G[Human Approval Gate]
        G -->|task token callback| C
    end

    subgraph "Memory Layer - CockroachDB"
        D -->|vector search| H[(Distributed Vector Index<br/>incident embeddings)]
        D -->|structured query| I[(Agent Memory<br/>past resolutions + state)]
        F -->|write resolution| I
        J[MCP Server] -->|read access| H
        J -->|read access| I
    end

    subgraph "AI Layer - AWS Bedrock"
        D -->|embed query| K[Titan Embeddings]
        E -->|reason| L[Claude 3.5 Sonnet]
        F -->|decide| L
        M[LLM-as-Judge] -->|score| L
    end

    subgraph "Data Intelligence - Databricks"
        I -->|JDBC sync| N[Delta Live Tables]
        N --> O[MLflow Experiment Tracking]
        O -->|metrics| P[Evaluation Dashboard]
    end

    subgraph "Frontend - Streamlit"
        Q[Panel 1: Incoming Signals]
        R[Panel 2: Agent Reasoning Stream]
        S[Panel 3: Memory Recall]
        T[Step Functions Timeline]
        U[Judge Score Delta Chart]
        V[Approval Button]
    end
```

## CockroachDB Tools Used

| Tool | Purpose |
|------|---------|
| Distributed Vector Indexing | Stores incident embeddings for semantic retrieval |
| MCP Server | Exposes memory to external agents with audit logging |
| ccloud CLI | Cluster provisioning, backup, audit log retrieval |

## Data Flow

1. Alert fires (synthetic or real CloudWatch)
2. Lambda ingests alert payload into CockroachDB `incidents` table
3. Step Functions triggers Retrieval Agent
4. Retrieval Agent embeds alert via Titan, queries vector index for similar incidents
5. Analysis Agent reasons over context using Claude 3.5 Sonnet
6. Decision Agent proposes remediation with confidence scoring
7. Human approval gate (if required)
8. Resolution written back to CockroachDB memory
9. On next similar incident, agent retrieves prior resolution -> faster diagnosis
