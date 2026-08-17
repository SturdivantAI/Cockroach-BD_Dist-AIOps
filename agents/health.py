"""Health check module for verifying system connectivity.

Validates:
- CockroachDB connection
- AWS Bedrock model access
- Configuration completeness

Usage:
    python -m agents.health
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def check_cockroachdb() -> tuple[bool, str]:
    """Verify CockroachDB connectivity."""
    try:
        from agents.db import execute_query
        result = execute_query("SELECT 1 AS health")
        if result and result[0].get("health") == 1:
            return True, "CockroachDB: Connected"
        return False, "CockroachDB: Unexpected response"
    except Exception as e:
        return False, f"CockroachDB: FAILED - {str(e)[:100]}"


def check_bedrock() -> tuple[bool, str]:
    """Verify Bedrock model access."""
    try:
        from agents.bedrock_client import bedrock
        embedding = bedrock.generate_embedding("health check")
        if len(embedding) == 1024:
            return True, "Bedrock Titan: Accessible (1024-dim embedding)"
        return False, f"Bedrock Titan: Unexpected dimension ({len(embedding)})"
    except Exception as e:
        return False, f"Bedrock: FAILED - {str(e)[:100]}"


def check_config() -> tuple[bool, str]:
    """Verify configuration completeness."""
    from agents.config import settings

    issues = []
    if settings.cockroachdb_host == "localhost":
        issues.append("cockroachdb_host is default (localhost)")
    if not settings.cockroachdb_password:
        issues.append("cockroachdb_password is empty")
    if not settings.cockroachdb_cluster_id:
        issues.append("cockroachdb_cluster_id not set (ccloud CLI won't work)")

    if issues:
        return False, f"Config: {len(issues)} issue(s) - {'; '.join(issues)}"
    return True, "Config: All required settings present"


def check_tables() -> tuple[bool, str]:
    """Verify database schema is applied."""
    try:
        from agents.db import execute_query
        tables = execute_query(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name IN "
            "('incidents', 'incident_embeddings', 'agent_memory', 'agent_decisions')"
        )
        table_names = [t["table_name"] for t in tables]
        expected = {"incidents", "incident_embeddings", "agent_memory", "agent_decisions"}
        missing = expected - set(table_names)
        if missing:
            return False, f"Schema: Missing tables: {missing}"
        return True, f"Schema: All 4 tables present"
    except Exception as e:
        return False, f"Schema check: FAILED - {str(e)[:100]}"


def check_seed_data() -> tuple[bool, str]:
    """Verify seed data is present."""
    try:
        from agents.db import execute_query
        result = execute_query("SELECT COUNT(*) AS cnt FROM incidents")
        count = result[0]["cnt"] if result else 0
        if count >= 15:
            return True, f"Seed data: {count} incidents loaded"
        elif count > 0:
            return True, f"Seed data: {count} incidents (partial)"
        return False, "Seed data: No incidents found (run: python data/seed_incidents.py)"
    except Exception as e:
        return False, f"Seed data: FAILED - {str(e)[:100]}"


def run_all_checks() -> bool:
    """Run all health checks and print results."""
    print("=" * 60)
    print("  SRE Memory Agent - Health Check")
    print("=" * 60)
    print()

    checks = [
        ("Configuration", check_config),
        ("CockroachDB", check_cockroachdb),
        ("Schema", check_tables),
        ("Seed Data", check_seed_data),
        ("Bedrock", check_bedrock),
    ]

    all_pass = True
    for name, check_fn in checks:
        try:
            passed, message = check_fn()
        except Exception as e:
            passed, message = False, f"{name}: Exception - {e}"

        icon = "✅" if passed else "❌"
        print(f"  {icon} {message}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("  All checks passed! System is ready.")
    else:
        print("  Some checks failed. Fix the issues above before running the demo.")

    return all_pass


if __name__ == "__main__":
    success = run_all_checks()
    sys.exit(0 if success else 1)
