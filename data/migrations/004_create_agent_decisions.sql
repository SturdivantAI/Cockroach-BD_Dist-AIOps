-- Migration 004: Create agent_decisions table
-- Stores final decisions with judge scores for evaluation

CREATE TABLE IF NOT EXISTS agent_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    session_id UUID NOT NULL,
    hypothesis TEXT NOT NULL,
    confidence FLOAT NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence JSONB DEFAULT '[]'::JSONB,
    reasoning_steps INT DEFAULT 0,
    resolution_applied TEXT,
    memory_items_used UUID[] DEFAULT ARRAY[]::UUID[],
    memory_enabled BOOL NOT NULL DEFAULT true,

    -- LLM-as-Judge scores (1-5 scale)
    judge_score_accuracy FLOAT CHECK (judge_score_accuracy >= 1.0 AND judge_score_accuracy <= 5.0),
    judge_score_resolution FLOAT CHECK (judge_score_resolution >= 1.0 AND judge_score_resolution <= 5.0),
    judge_score_reasoning FLOAT CHECK (judge_score_reasoning >= 1.0 AND judge_score_reasoning <= 5.0),
    judge_score_evidence FLOAT CHECK (judge_score_evidence >= 1.0 AND judge_score_evidence <= 5.0),
    judge_score_total FLOAT,  -- Computed average

    -- Timing metrics
    time_to_diagnosis_ms INT,
    time_to_resolution_ms INT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    INDEX idx_decisions_incident (incident_id),
    INDEX idx_decisions_session (session_id),
    INDEX idx_decisions_memory (memory_enabled),
    INDEX idx_decisions_created (created_at DESC)
);
