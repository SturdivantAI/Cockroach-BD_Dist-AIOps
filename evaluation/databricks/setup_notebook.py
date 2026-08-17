"""Databricks setup notebook for the SRE Memory Agent evaluation pipeline.

Run this notebook in Databricks to:
1. Configure the JDBC connection to CockroachDB
2. Set up MLflow experiment
3. Create the DLT pipeline
4. Verify connectivity

Prerequisites:
- Databricks workspace with 14-day trial
- $400 AWS credits applied
- CockroachDB cluster provisioned (Phase 1)
"""

# %% [markdown]
# # SRE Memory Agent - Databricks Setup
# 
# This notebook configures the Databricks integration for evaluation and analytics.

# %% Configuration
# Set these values from your .env or Databricks secrets

COCKROACHDB_HOST = dbutils.secrets.get(scope="sre-agent", key="cockroachdb-host")  # noqa: F821
COCKROACHDB_PORT = 26257
COCKROACHDB_DATABASE = "defaultdb"
COCKROACHDB_USER = dbutils.secrets.get(scope="sre-agent", key="cockroachdb-user")  # noqa: F821
COCKROACHDB_PASSWORD = dbutils.secrets.get(scope="sre-agent", key="cockroachdb-password")  # noqa: F821

JDBC_URL = (
    f"jdbc:postgresql://{COCKROACHDB_HOST}:{COCKROACHDB_PORT}/{COCKROACHDB_DATABASE}"
    f"?sslmode=verify-full"
)

MLFLOW_EXPERIMENT = "/sre-memory-agent/evaluation"

# %% MLflow Experiment Setup
import mlflow

mlflow.set_experiment(MLFLOW_EXPERIMENT)
print(f"MLflow experiment: {MLFLOW_EXPERIMENT}")

# %% Test JDBC Connection
# Read a sample from CockroachDB to verify connectivity
df_incidents = (
    spark.read  # noqa: F821
    .format("jdbc")
    .option("url", JDBC_URL)
    .option("dbtable", "incidents")
    .option("user", COCKROACHDB_USER)
    .option("password", COCKROACHDB_PASSWORD)
    .option("driver", "org.postgresql.Driver")
    .load()
)

print(f"Connected to CockroachDB. Incidents count: {df_incidents.count()}")
df_incidents.select("service", "incident_class", "severity", "created_at").show(5)

# %% Read Agent Decisions
df_decisions = (
    spark.read  # noqa: F821
    .format("jdbc")
    .option("url", JDBC_URL)
    .option("dbtable", "agent_decisions")
    .option("user", COCKROACHDB_USER)
    .option("password", COCKROACHDB_PASSWORD)
    .option("driver", "org.postgresql.Driver")
    .load()
)

print(f"Agent decisions count: {df_decisions.count()}")
df_decisions.select("confidence", "memory_enabled", "judge_score_total", "created_at").show(5)

# %% Create Delta Tables for Analytics
# Write to Delta for fast dashboard queries
df_incidents.write.mode("overwrite").saveAsTable("sre_agent.bronze_incidents")
df_decisions.write.mode("overwrite").saveAsTable("sre_agent.bronze_agent_decisions")

# %% Create Analytics View
spark.sql("""  # noqa: F821
CREATE OR REPLACE VIEW sre_agent.incident_analytics AS
SELECT
  i.id AS incident_id,
  i.service,
  i.severity,
  i.incident_class,
  i.created_at AS incident_time,
  i.resolved_at,
  d.confidence,
  d.reasoning_steps,
  d.memory_enabled,
  d.judge_score_total,
  d.judge_score_accuracy,
  d.judge_score_resolution,
  TIMESTAMPDIFF(MINUTE, i.created_at, i.resolved_at) AS resolution_minutes
FROM sre_agent.bronze_incidents i
LEFT JOIN sre_agent.bronze_agent_decisions d ON d.incident_id = i.id
""")

print("Analytics view created: sre_agent.incident_analytics")

# %% Memory Improvement Summary
spark.sql("""  # noqa: F821
SELECT
  incident_class,
  memory_enabled,
  COUNT(*) as runs,
  ROUND(AVG(confidence), 3) as avg_confidence,
  ROUND(AVG(judge_score_total), 2) as avg_judge_score,
  ROUND(AVG(reasoning_steps), 1) as avg_steps
FROM sre_agent.incident_analytics
WHERE confidence IS NOT NULL
GROUP BY incident_class, memory_enabled
ORDER BY incident_class, memory_enabled
""").show()

print("Setup complete! Ready for DLT pipeline and dashboard creation.")
