"""Prepare dedicated deployment credentials, SQL grants, and a private ECR repo.

Run explicitly with an authorized AWS identity. Secret values are never printed.
"""

import json
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any

import boto3
import psycopg
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from agents.config import settings

USERNAME = "sre_agent_v2_app"
REGION = "us-east-1"
TAGS = [{"Key": "Project", "Value": "SRE-Agent-v2"}]


def stored_secret(client: Any, name: str, initial: dict[str, str]) -> tuple[str, dict[str, str]]:
    try:
        response = client.get_secret_value(SecretId=name)
        return response["ARN"], json.loads(response["SecretString"])
    except client.exceptions.ResourceNotFoundException:
        response = client.create_secret(Name=name, SecretString=json.dumps(initial), Tags=TAGS)
        return response["ARN"], initial


def main() -> None:
    key = settings.nebius_api_key.get_secret_value()
    if not key:
        raise ValueError("A configured Nebius key is required")
    session = boto3.Session(region_name=REGION)
    ecr = session.client("ecr")
    try:
        repository = ecr.describe_repositories(repositoryNames=["sre-agent-v2"])["repositories"][0]
    except ecr.exceptions.RepositoryNotFoundException:
        repository = ecr.create_repository(
            repositoryName="sre-agent-v2", imageTagMutability="IMMUTABLE",
            imageScanningConfiguration={"scanOnPush": True}, tags=TAGS,
        )["repository"]
    sm = session.client("secretsmanager")
    db = session.client("rds").describe_db_instances(DBInstanceIdentifier="sre-agent-v2-db")["DBInstances"][0]
    admin = json.loads(sm.get_secret_value(SecretId=db["MasterUserSecret"]["SecretArn"])["SecretString"])
    db_secret_arn, credentials = stored_secret(sm, "sre-agent-v2/database-app", {
        "username": USERNAME, "password": secrets.token_urlsafe(36),
    })
    if credentials.get("username") != USERNAME or not credentials.get("password"):
        raise ValueError("Existing application secret has an unexpected credentials schema")
    runtime_arn, runtime = stored_secret(sm, "sre-agent-v2/runtime", {
        "NEBIUS_API_KEY": key, "DEMO_ACCESS_CODE": secrets.token_urlsafe(24),
        "ORIGIN_TOKEN": secrets.token_hex(32),
    })
    if not runtime.get("DEMO_ACCESS_CODE") or not runtime.get("ORIGIN_TOKEN"):
        raise ValueError("Existing runtime secret is incomplete")
    if runtime.get("NEBIUS_API_KEY") != key:
        runtime["NEBIUS_API_KEY"] = key
        sm.put_secret_value(SecretId=runtime_arn, SecretString=json.dumps(runtime))
    certificate = Path(settings.rds_sslrootcert)
    if not certificate.is_absolute():
        certificate = ROOT / certificate
    common = {"host": db["Endpoint"]["Address"], "port": 5432, "dbname": "sre_agent",
              "sslmode": "verify-full", "sslrootcert": str(certificate), "connect_timeout": 10}
    with psycopg.connect(**common, user=admin["username"], password=admin["password"]) as conn:
        existing = conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=%s", (USERNAME,)).fetchone()
        if existing and any(existing):
            raise RuntimeError("Existing deployment role has elevated privileges; refusing to reuse it")
        if existing is None:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(USERNAME), sql.Literal(credentials["password"])))
        else:
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(USERNAME), sql.Literal(credentials["password"])))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE sre_agent TO {}").format(sql.Identifier(USERNAME)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(USERNAME)))
        conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(USERNAME)))
        conn.execute(sql.SQL("GRANT SELECT ON incidents, incident_embeddings, agent_memory, agent_decisions, action_executions, outcome_verifications TO {}").format(sql.Identifier(USERNAME)))
        conn.execute(sql.SQL("GRANT INSERT ON incidents, agent_memory, agent_decisions TO {}").format(sql.Identifier(USERNAME)))
        if conn.execute("SELECT has_schema_privilege(%s,'public','CREATE')", (USERNAME,)).fetchone()[0]:
            raise RuntimeError("Application role inherits schema CREATE permission; deployment stopped")
    # Prove the application can write proposals but cannot manufacture verification.
    with psycopg.connect(**common, user=USERNAME, password=credentials["password"]) as conn:
        incident_id, session_id = uuid.uuid4(), uuid.uuid4()
        conn.execute("INSERT INTO incidents (id,alert_id,severity,service,description) VALUES (%s,%s,%s,%s,%s)", (incident_id,"permission-smoke","low","deployment-check","Rolled-back permissions check"))
        conn.execute("INSERT INTO agent_decisions (incident_id,session_id,hypothesis,confidence,proposed_action) VALUES (%s,%s,%s,%s,%s)", (incident_id,session_id,"Permission check",0.0,"No action"))
        conn.execute("INSERT INTO agent_memory (session_id,incident_id,agent_name,step_type,content) VALUES (%s,%s,%s,%s,%s)", (session_id,incident_id,"decision","observation",json.dumps({"source":"permission_smoke"})))
        conn.rollback()
        for table in ("outcome_verifications", "action_executions"):
            try:
                conn.execute(sql.SQL("INSERT INTO {} DEFAULT VALUES").format(sql.Identifier(table)))
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
            else:
                raise RuntimeError("Application role unexpectedly wrote to the verifier ledger")
    state = {
        "region": REGION, "repository_uri": repository["repositoryUri"],
        "app_db_secret_arn": db_secret_arn, "runtime_secret_arn": runtime_arn,
        "database_host": db["Endpoint"]["Address"],
        "database_security_group": db["VpcSecurityGroups"][0]["VpcSecurityGroupId"],
        "vpc_id": db["DBSubnetGroup"]["VpcId"],
    }
    target = Path(__file__).with_name("deployment-state.json")
    target.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print("PASS: application inserts work; verification and execution writes are denied")
    print("Prepared repository:", state["repository_uri"])
    print("Deployment state saved without secret values:", target.name)


if __name__ == "__main__":
    main()
