"""Centralized configuration for the SRE Memory Agent system."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


def _default_rds_certificate() -> str:
    packaged = Path(__file__).resolve().parent / "certs/aws-rds-global.pem"
    if packaged.is_file():
        return str(packaged)
    return str(Path(__file__).resolve().parent.parent / "certs/aws-rds-global.pem")


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    db_backend: Literal["cockroachdb", "rds_postgresql"] = "cockroachdb"
    rds_host: str = ""
    rds_port: int = Field(default=5432, ge=1, le=65535)
    rds_database: str = "sre_agent"
    rds_secret_arn: str = ""
    rds_sslrootcert: str = _default_rds_certificate()
    db_connect_timeout: int = Field(default=10, ge=1, le=120)

    inference_provider: Literal["bedrock", "nebius"] = "bedrock"
    nebius_api_key: SecretStr = SecretStr("")
    nebius_model_id: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    inference_timeout_seconds: float = Field(default=120, gt=0, le=300)
    memory_validation_enabled: bool = True
    memory_max_age_days: int = Field(default=30, ge=1)
    memory_allow_simulation: bool = False
    deployment_mode: Literal["local", "hosted"] = "local"
    demo_access_code: SecretStr = SecretStr("")

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

    model_config = {
        "env_file": Path(__file__).resolve().parent.parent / ".env",
        "env_file_encoding": "utf-8",
    }

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
