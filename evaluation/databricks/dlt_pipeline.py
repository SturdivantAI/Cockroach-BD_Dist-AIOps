"""Delta Live Tables pipeline for SRE Memory Agent analytics.

Syncs data from CockroachDB into Delta tables for dashboarding and analysis.

Source: CockroachDB incidents + agent_decisions tables (JDBC connector)
Transformations: Join incidents with decisions, compute resolution time, flag memory-assisted runs
Output: Gold table `incident_analytics` for dashboarding

Deploy this as a Databricks DLT pipeline notebook.
"""

# NOTE: This file is designed to run inside a Databricks notebook environment.
# It uses the `dlt` module which is only available in Databricks DLT pipelines.
# For local development, use the mlflow_tracker.py module instead.

# ============================================================================
# To use this pipeline in Databricks:
# 1. Create a new DLT pipeline in Databricks
# 2. Copy this file content into a notebook
# 3. Configure JDBC connection to CockroachDB
# 4. Schedule the pipeline
# ============================================================================

COCKROACHDB_JDBC_URL = (
    "jdbc:postgresql://{host}:{port}/{database}"
    "?sslmode=verify-full&user={user}&password={password}"
)

# Pipeline definition (Databricks DLT syntax)
PIPELINE_SQL = """
-- ============================================================================
-- BRONZE LAYER: Raw data from CockroachDB
-- ============================================================================

CREATE OR REFRESH STREAMING LIVE TABLE bronze_incidents
COMMENT "Raw incidents from CockroachDB"
AS SELECT *
FROM read_jdbc(
  url => '{jdbc_url}',
  table => 'incidents'
);

CREATE OR REFRESH STREAMING LIVE TABLE bronze_agent_decisions
COMMENT "Raw agent decisions from CockroachDB"
AS SELECT *
FROM read_jdbc(
  url => '{jdbc_url}',
  table => 'agent_decisions'
);

CREATE OR REFRESH STREAMING LIVE TABLE bronze_agent_memory
COMMENT "Raw agent memory entries from CockroachDB"
AS SELECT *
FROM read_jdbc(
  url => '{jdbc_url}',
  table => 'agent_memory'
);

-- ============================================================================
-- SILVER LAYER: Cleaned and joined data
-- ============================================================================

CREATE OR REFRESH LIVE TABLE silver_incident_decisions
COMMENT "Incidents joined with agent decisions"
AS SELECT
  i.id AS incident_id,
  i.alert_id,
  i.service,
  i.severity,
  i.incident_class,
  i.description,
  i.root_cause,
  i.resolution,
  i.created_at AS incident_created_at,
  i.resolved_at,
  d.id AS decision_id,
  d.session_id,
  d.hypothesis,
  d.confidence,
  d.reasoning_steps,
  d.resolution_applied,
  d.memory_enabled,
  d.judge_score_accuracy,
  d.judge_score_resolution,
  d.judge_score_reasoning,
  d.judge_score_evidence,
  d.judge_score_total,
  d.time_to_diagnosis_ms,
  d.time_to_resolution_ms,
  d.created_at AS decision_created_at,
  -- Computed: resolution time in minutes
  TIMESTAMPDIFF(MINUTE, i.created_at, i.resolved_at) AS resolution_time_minutes
FROM LIVE.bronze_incidents i
LEFT JOIN LIVE.bronze_agent_decisions d ON d.incident_id = i.id;

-- ============================================================================
-- GOLD LAYER: Analytics-ready aggregations
-- ============================================================================

CREATE OR REFRESH LIVE TABLE gold_incident_analytics
COMMENT "Analytics table for dashboarding: resolution time, scores, memory impact"
AS SELECT
  incident_class,
  service,
  severity,
  memory_enabled,
  COUNT(*) AS incident_count,
  AVG(confidence) AS avg_confidence,
  AVG(reasoning_steps) AS avg_reasoning_steps,
  AVG(resolution_time_minutes) AS avg_resolution_time_minutes,
  AVG(judge_score_total) AS avg_judge_score,
  AVG(judge_score_accuracy) AS avg_accuracy_score,
  AVG(judge_score_resolution) AS avg_resolution_score,
  AVG(time_to_diagnosis_ms) AS avg_diagnosis_time_ms,
  AVG(time_to_resolution_ms) AS avg_resolution_time_ms
FROM LIVE.silver_incident_decisions
WHERE decision_id IS NOT NULL
GROUP BY incident_class, service, severity, memory_enabled;

CREATE OR REFRESH LIVE TABLE gold_memory_improvement_delta
COMMENT "Memory improvement delta: with vs without memory comparison"
AS SELECT
  incident_class,
  -- Without memory metrics
  AVG(CASE WHEN NOT memory_enabled THEN confidence END) AS avg_confidence_no_memory,
  AVG(CASE WHEN memory_enabled THEN confidence END) AS avg_confidence_with_memory,
  AVG(CASE WHEN NOT memory_enabled THEN judge_score_total END) AS avg_score_no_memory,
  AVG(CASE WHEN memory_enabled THEN judge_score_total END) AS avg_score_with_memory,
  AVG(CASE WHEN NOT memory_enabled THEN reasoning_steps END) AS avg_steps_no_memory,
  AVG(CASE WHEN memory_enabled THEN reasoning_steps END) AS avg_steps_with_memory,
  -- Deltas
  AVG(CASE WHEN memory_enabled THEN confidence END) -
    AVG(CASE WHEN NOT memory_enabled THEN confidence END) AS confidence_delta,
  AVG(CASE WHEN memory_enabled THEN judge_score_total END) -
    AVG(CASE WHEN NOT memory_enabled THEN judge_score_total END) AS score_delta,
  COUNT(*) AS total_decisions
FROM LIVE.silver_incident_decisions
WHERE decision_id IS NOT NULL
GROUP BY incident_class;
"""
