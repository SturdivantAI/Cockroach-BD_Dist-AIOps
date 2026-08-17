-- Migration 003: Create agent_memory table
-- Stores step-by-step reasoning chains for each agent session

CREATE TABLE IF NOT EXISTS agent_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    incident_id UUID REFERENCES incidents(id) ON DELETE SET NULL,
    agent_name STRING NOT NULL CHECK (
        agent_name IN ('retrieval', 'analysis', 'decision', 'ops', 'judge')
    ),
    step_type STRING NOT NULL CHECK (
        step_type IN ('observation', 'hypothesis', 'action', 'outcome', 'retrieval', 'evaluation')
    ),
    content JSONB NOT NULL,
    parent_step_id UUID REFERENCES agent_memory(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    INDEX idx_memory_session (session_id),
    INDEX idx_memory_incident (incident_id),
    INDEX idx_memory_agent (agent_name),
    INDEX idx_memory_created (created_at DESC)
);
