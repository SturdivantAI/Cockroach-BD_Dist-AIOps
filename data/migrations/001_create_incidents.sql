-- Migration 001: Create incidents table
-- The core table for storing incident records

CREATE TABLE IF NOT EXISTS incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id STRING NOT NULL,
    severity STRING NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    service STRING NOT NULL,
    description TEXT NOT NULL,
    raw_logs JSONB,
    root_cause TEXT,
    resolution TEXT,
    incident_class STRING,  -- e.g. 'cpu_spike', 'memory_leak', 'network_partition'
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    INDEX idx_incidents_service (service),
    INDEX idx_incidents_severity (severity),
    INDEX idx_incidents_class (incident_class),
    INDEX idx_incidents_created (created_at DESC)
);
