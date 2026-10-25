-- PostgreSQL-only initial schema. Do not run the CockroachDB migration runner.
-- pgvector must already be enabled. Apply this file as one transaction.
BEGIN;

CREATE TABLE incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    service TEXT NOT NULL,
    description TEXT NOT NULL,
    raw_logs JSONB,
    root_cause TEXT,
    resolution TEXT,
    incident_class TEXT,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_incidents_service ON incidents (service);
CREATE INDEX idx_incidents_severity ON incidents (severity);
CREATE INDEX idx_incidents_class ON incidents (incident_class);
CREATE INDEX idx_incidents_created ON incidents (created_at DESC);

CREATE TABLE incident_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    embedding VECTOR(1024) NOT NULL,
    chunk_text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    embedding_type TEXT NOT NULL DEFAULT 'incident' CHECK (
        embedding_type IN ('incident', 'resolution', 'symptom', 'root_cause')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_embeddings_incident ON incident_embeddings (incident_id);
CREATE INDEX idx_embeddings_type ON incident_embeddings (embedding_type);
-- Use exact cosine-distance search for the small controlled pilot.
-- Defer approximate vector indexes until retrieval accuracy is evaluated.

CREATE TABLE agent_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    agent_name TEXT NOT NULL CHECK (
        agent_name IN ('retrieval', 'validation', 'analysis', 'decision', 'ops', 'judge')
    ),
    step_type TEXT NOT NULL CHECK (
        step_type IN ('observation', 'hypothesis', 'action', 'outcome', 'retrieval', 'evaluation', 'validation')
    ),
    content JSONB NOT NULL,
    parent_step_id UUID REFERENCES agent_memory(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_memory_session ON agent_memory (session_id);
CREATE INDEX idx_memory_incident ON agent_memory (incident_id);
CREATE INDEX idx_memory_agent ON agent_memory (agent_name);
CREATE INDEX idx_memory_created ON agent_memory (created_at DESC);

CREATE TABLE agent_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    session_id UUID NOT NULL,
    hypothesis TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence JSONB NOT NULL DEFAULT '[]'::JSONB,
    reasoning_steps INT NOT NULL DEFAULT 0 CHECK (reasoning_steps >= 0),
    proposed_action TEXT NOT NULL,
    memory_items_used UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    memory_enabled BOOLEAN NOT NULL DEFAULT true,
    judge_score_accuracy DOUBLE PRECISION CHECK (judge_score_accuracy BETWEEN 1 AND 5),
    judge_score_resolution DOUBLE PRECISION CHECK (judge_score_resolution BETWEEN 1 AND 5),
    judge_score_reasoning DOUBLE PRECISION CHECK (judge_score_reasoning BETWEEN 1 AND 5),
    judge_score_evidence DOUBLE PRECISION CHECK (judge_score_evidence BETWEEN 1 AND 5),
    judge_score_total DOUBLE PRECISION CHECK (judge_score_total BETWEEN 1 AND 5),
    time_to_diagnosis_ms BIGINT CHECK (time_to_diagnosis_ms >= 0),
    time_to_resolution_ms BIGINT CHECK (time_to_resolution_ms >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_decisions_incident ON agent_decisions (incident_id);
CREATE INDEX idx_decisions_session ON agent_decisions (session_id);
CREATE INDEX idx_decisions_memory ON agent_decisions (memory_enabled);
CREATE INDEX idx_decisions_created ON agent_decisions (created_at DESC);

-- A decision is only a proposal. Execution records describe individual attempts.
CREATE TABLE action_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id UUID NOT NULL REFERENCES agent_decisions(id),
    execution_mode TEXT NOT NULL CHECK (execution_mode IN ('simulation', 'operational')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'blocked')),
    action_payload JSONB NOT NULL,
    run_identifier TEXT NOT NULL CHECK (length(trim(run_identifier)) > 0),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status NOT IN ('completed', 'failed') OR
        (started_at IS NOT NULL AND finished_at IS NOT NULL)),
    CHECK (finished_at IS NULL OR (started_at IS NOT NULL AND finished_at >= started_at)),
    UNIQUE (id, execution_mode)
);
CREATE INDEX idx_executions_decision ON action_executions (decision_id);

-- Evidence is recorded separately from model confidence and approval.
CREATE TABLE outcome_verifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id UUID NOT NULL,
    execution_mode TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('deterministic_simulation', 'operational_checks', 'human_confirmation')),
    verdict TEXT NOT NULL CHECK (verdict IN ('passed', 'failed', 'inconclusive')),
    verifier TEXT NOT NULL CHECK (length(trim(verifier)) > 0),
    checks JSONB NOT NULL CHECK (jsonb_typeof(checks) = 'array' AND jsonb_array_length(checks) > 0),
    evidence JSONB NOT NULL CHECK (jsonb_typeof(evidence) = 'object' AND evidence <> '{}'::JSONB),
    verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (execution_id, execution_mode) REFERENCES action_executions(id, execution_mode),
    CHECK ((execution_mode = 'simulation' AND method = 'deterministic_simulation') OR
        (execution_mode = 'operational' AND method IN ('operational_checks', 'human_confirmation')))
);
CREATE INDEX idx_verifications_execution ON outcome_verifications (execution_id);

-- These records support verification; application logic must enforce promotion
-- and retrieval rules. Existing v1 writers are not compatible with this schema.
COMMIT;
