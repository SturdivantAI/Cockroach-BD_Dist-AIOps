# Multi-Region Resilience: Zero Data Loss Memory

## The Hackathon Constraint

> "Focus on building systems where memory persists across regions and failures with zero data loss."

This document explains how our architecture meets this constraint.

## How CockroachDB Guarantees Zero Data Loss

### Raft Consensus Protocol

Every write to agent memory follows this path:

```
Agent writes memory  →  CockroachDB Leader (us-east-1)
                         │
                         ├──→ Replicate to us-east-1 (local)
                         ├──→ Replicate to us-east-2 (cross-AZ)
                         ├──→ Replicate to us-west-2 (cross-continent)
                         │
                         ▼
                     Raft quorum achieved (2 of 3 regions acknowledge)
                         │
                         ▼
                     Write acknowledged to agent
```

The write is **not acknowledged** until replicated to a quorum (2 of 3 regions). This means:

- If us-east-1 goes down entirely: **data is safe in us-east-2 + us-west-2**
- If us-west-2 goes down entirely: **data is safe in us-east-1 + us-east-2**
- If ANY single region fails: **the other two form quorum and continue operating**
- If the agent process crashes after receiving acknowledgment: **data is committed across 3 regions**

### Our Multi-Region Configuration

```sql
-- Database spans THREE AWS regions
ALTER DATABASE defaultdb SET PRIMARY REGION "us-east-1";
ALTER DATABASE defaultdb ADD REGION "us-east-2";
ALTER DATABASE defaultdb ADD REGION "us-west-2";

-- SURVIVE REGION FAILURE: strongest guarantee possible
-- The database continues operating even if an entire region goes offline
ALTER DATABASE defaultdb SURVIVE REGION FAILURE;

-- Memory tables replicated globally (reads from any region, writes consistent)
ALTER TABLE agent_memory SET LOCALITY GLOBAL;
ALTER TABLE incident_embeddings SET LOCALITY GLOBAL;
```

### What "SURVIVE REGION FAILURE" Means

With `SURVIVE REGION FAILURE` + `LOCALITY GLOBAL`:
- Writes are synchronously committed to **all 3 regions** before acknowledgment
- If any 1 region goes completely offline, the remaining 2 regions have full data
- Failover is automatic — no human intervention, no data loss, no inconsistency
- This is the **strongest durability guarantee** available in any distributed database

## Failure Scenarios We Survive

### 1. Single Node Failure
- CockroachDB has multiple nodes per region
- Raft automatically elects new leader
- Agent doesn't even notice (connection pool retries)

### 2. Availability Zone Failure
- Multiple AZs per region in the cluster
- Cluster continues operating with remaining AZs
- Zero data loss, zero manual intervention

### 3. Entire Region Failure (SURVIVE REGION FAILURE)
- `SURVIVE REGION FAILURE` configured with 3 regions
- If us-east-1 goes offline entirely: us-east-2 + us-west-2 form quorum
- Agent memory, embeddings, decisions — all intact
- Automatic failover, reads and writes continue from surviving regions

### 4. Agent Process Crash Mid-Workflow
- Every agent step writes to `agent_memory` synchronously
- `_save_error_state()` preserves partial knowledge
- On retry, previous steps' results are retrievable from memory
- The next workflow run can pick up where the previous one left off

### 5. Network Partition Between Regions
- CockroachDB's Raft consensus handles split-brain
- Writes require quorum (2 of 3 regions must agree)
- Minority partition cannot accept writes (prevents divergence)
- Majority partition continues serving reads and writes

## How This Manifests in Our Agent

```python
# Every memory write is synchronous — not fire-and-forget
execute_command(
    "INSERT INTO agent_memory (...) VALUES (...)",
    (memory_id, session_id, ...)
)
# If this line executes without exception:
# → Data is replicated across regions
# → Data survives any single-region failure
# → Data is immediately readable from any region
```

## Verification

Run the resilience test suite:

```bash
python evaluation/test_resilience.py --test all
```

This proves:
1. **Persistence** — Data survives process crashes
2. **Consistency** — Immediate read-after-write (strong consistency)
3. **Durability** — Concurrent writes have zero data loss
4. **Partial failure** — Workflow state preserved across crashes
