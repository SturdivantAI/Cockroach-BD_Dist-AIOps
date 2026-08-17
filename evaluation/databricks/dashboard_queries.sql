-- ============================================================================
-- Databricks SQL Dashboard Queries for SRE Memory Agent
-- Create these as widgets in a Databricks SQL Dashboard
-- ============================================================================

-- QUERY 1: Resolution Time Trend (with vs without memory)
-- Widget type: Line chart
SELECT
  incident_class,
  memory_enabled,
  avg_resolution_time_minutes,
  avg_confidence,
  avg_judge_score
FROM gold_incident_analytics
ORDER BY incident_class, memory_enabled;

-- QUERY 2: Score Distribution by Incident Class
-- Widget type: Grouped bar chart
SELECT
  incident_class,
  CASE WHEN memory_enabled THEN 'With Memory' ELSE 'Without Memory' END AS memory_status,
  avg_judge_score AS avg_total_score,
  avg_accuracy_score,
  avg_resolution_score
FROM gold_incident_analytics
WHERE incident_class IS NOT NULL
ORDER BY incident_class;

-- QUERY 3: Memory Improvement Delta by Class
-- Widget type: Bar chart showing delta
SELECT
  incident_class,
  confidence_delta,
  score_delta,
  avg_confidence_no_memory,
  avg_confidence_with_memory,
  avg_score_no_memory,
  avg_score_with_memory,
  total_decisions
FROM gold_memory_improvement_delta
ORDER BY score_delta DESC;

-- QUERY 4: Memory Utilization Rate
-- Widget type: Stat/counter
SELECT
  COUNT(CASE WHEN memory_enabled THEN 1 END) AS memory_assisted_runs,
  COUNT(CASE WHEN NOT memory_enabled THEN 1 END) AS no_memory_runs,
  COUNT(*) AS total_runs,
  ROUND(COUNT(CASE WHEN memory_enabled THEN 1 END) * 100.0 / COUNT(*), 1) AS memory_utilization_pct
FROM silver_incident_decisions
WHERE decision_id IS NOT NULL;

-- QUERY 5: Top Services by Incident Frequency
-- Widget type: Bar chart
SELECT
  service,
  COUNT(*) AS incident_count,
  AVG(confidence) AS avg_confidence,
  AVG(judge_score_total) AS avg_judge_score
FROM silver_incident_decisions
WHERE decision_id IS NOT NULL
GROUP BY service
ORDER BY incident_count DESC
LIMIT 10;

-- QUERY 6: Confidence Distribution (memory vs no memory)
-- Widget type: Histogram
SELECT
  CASE WHEN memory_enabled THEN 'With Memory' ELSE 'Without Memory' END AS memory_status,
  ROUND(confidence, 1) AS confidence_bucket,
  COUNT(*) AS count
FROM silver_incident_decisions
WHERE decision_id IS NOT NULL
GROUP BY memory_status, confidence_bucket
ORDER BY memory_status, confidence_bucket;

-- QUERY 7: Resolution Time Improvement Over Time
-- Widget type: Line chart
SELECT
  DATE(decision_created_at) AS decision_date,
  memory_enabled,
  AVG(resolution_time_minutes) AS avg_resolution_time,
  AVG(confidence) AS avg_confidence
FROM silver_incident_decisions
WHERE decision_id IS NOT NULL
GROUP BY decision_date, memory_enabled
ORDER BY decision_date;
