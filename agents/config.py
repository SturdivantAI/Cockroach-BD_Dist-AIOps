"""Centralized configuration for the SRE Memory Agent system."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # CockroachDB
    cockroachdb_host: str = Field(default="localhost")
    cockroachdb_port: int = Field(default=26257)
    cockroachdb_database: str = Field(default="defaultdb")
    cockroachdb_user: str = Field(default="root")
    cockroachdb_password: str = Field(default="")
    cockroachdb_ssl_mode: str = Field(default="verify-full")
    cockroachdb_cluster_id: str = Field(default="")

    # AWS
    aws_region: str = Field(default="us-east-1")
    aws_account_id: str = Field(default="")
    bedrock_model_id: str = Field(default="us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    bedrock_embedding_model_id: str = Field(default="amazon.titan-embed-text-v2:0")
    step_functions_arn: str = Field(default="")
    s3_bucket: str = Field(default="sre-memory-agent-artifacts")

    # Databricks
    databricks_host: str = Field(default="")
    databricks_token: str = Field(default="")
    mlflow_tracking_uri: str = Field(default="databricks")
    mlflow_experiment_name: str = Field(default="/sre-memory-agent/evaluation")

    # Application
    log_level: str = Field(default="INFO")
    retrieval_top_k: int = Field(default=5)
    confidence_threshold: float = Field(default=0.7)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def cockroachdb_dsn(self) -> str:
        """Build the CockroachDB connection string."""
        return (
            f"postgresql://{self.cockroachdb_user}:{self.cockroachdb_password}"
            f"@{self.cockroachdb_host}:{self.cockroachdb_port}"
            f"/{self.cockroachdb_database}?sslmode={self.cockroachdb_ssl_mode}"
        )


# Singleton settings instance
settings = Settings()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MIGRATIONS_DIR = DATA_DIR / "migrations"
SAMPLE_INCIDENTS_DIR = DATA_DIR / "sample_incidents"
