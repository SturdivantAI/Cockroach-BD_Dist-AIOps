"""Seed CockroachDB with 20 realistic incidents adapted from LogHub/OpenStack patterns.

Generates embeddings using AWS Bedrock Titan and inserts into incident_embeddings.

Usage:
    python data/seed_incidents.py
    python data/seed_incidents.py --skip-embeddings  (for testing without AWS)
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.config import settings

# Seed incident data adapted from LogHub (HDFS, OpenStack, BGL) patterns
SEED_INCIDENTS = [
    # === CPU SPIKE (4 incidents) ===
    {
        "alert_id": "cpu-spike-001",
        "severity": "critical",
        "service": "payment-service",
        "incident_class": "cpu_spike",
        "description": "CPU utilization on payment-service exceeded 95% for 5 minutes. Transaction processing latency increased from 50ms to 2300ms.",
        "raw_logs": [
            "2026-07-15T08:23:01Z [payment-service] WARN cpu_usage=96.2% threshold=90%",
            "2026-07-15T08:23:15Z [payment-service] ERROR request_timeout path=/api/v2/charge latency=2341ms",
            "2026-07-15T08:23:22Z [payment-service] WARN thread_pool_exhausted active=200 max=200",
            "2026-07-15T08:23:30Z [payment-service] ERROR circuit_breaker_open service=fraud-check"
        ],
        "root_cause": "Runaway regex in fraud detection pattern matching caused exponential backtracking on malformed input",
        "resolution": "Deployed hotfix to replace catastrophic regex with bounded linear-time pattern. Restarted payment-service pods.",
    },
    {
        "alert_id": "cpu-spike-002",
        "severity": "high",
        "service": "search-indexer",
        "incident_class": "cpu_spike",
        "description": "Search indexer CPU at 92% sustained. Index rebuild triggered unexpectedly during peak hours.",
        "raw_logs": [
            "2026-07-16T14:05:00Z [search-indexer] INFO full_reindex_triggered reason=schema_change",
            "2026-07-16T14:05:12Z [search-indexer] WARN cpu_usage=91.8% during_reindex=true",
            "2026-07-16T14:06:00Z [search-indexer] ERROR query_timeout index=products latency=5200ms",
            "2026-07-16T14:06:30Z [search-indexer] WARN queue_depth=15000 normal_max=500"
        ],
        "root_cause": "Schema migration triggered full reindex during peak traffic instead of scheduled maintenance window",
        "resolution": "Cancelled reindex, rescheduled to maintenance window. Added guard to prevent reindex during peak hours.",
    },
    {
        "alert_id": "cpu-spike-003",
        "severity": "high",
        "service": "auth-service",
        "incident_class": "cpu_spike",
        "description": "Authentication service CPU spiked to 89% after deploying v2.4.1. Login latency doubled.",
        "raw_logs": [
            "2026-07-17T09:12:00Z [auth-service] INFO deployment_complete version=2.4.1",
            "2026-07-17T09:15:30Z [auth-service] WARN cpu_usage=89.4% baseline=35%",
            "2026-07-17T09:16:00Z [auth-service] ERROR bcrypt_rounds=14 expected=10",
            "2026-07-17T09:16:15Z [auth-service] WARN login_latency_p99=1800ms baseline=400ms"
        ],
        "root_cause": "New deployment accidentally increased bcrypt rounds from 10 to 14, quadrupling hash computation time",
        "resolution": "Rolled back to v2.4.0. Fixed config to pin bcrypt rounds=10. Redeployed as v2.4.2.",
    },
    {
        "alert_id": "cpu-spike-004",
        "severity": "medium",
        "service": "notification-service",
        "incident_class": "cpu_spike",
        "description": "Notification service CPU elevated to 78% due to retry storm from downstream failure.",
        "raw_logs": [
            "2026-07-18T11:00:00Z [notification-service] WARN retry_count=45000 in_last_minute",
            "2026-07-18T11:00:15Z [notification-service] ERROR downstream_unavailable target=email-provider",
            "2026-07-18T11:00:30Z [notification-service] WARN cpu_usage=78.2% retry_threads=50",
            "2026-07-18T11:01:00Z [notification-service] INFO backoff_multiplier=1 (no exponential backoff configured)"
        ],
        "root_cause": "Email provider outage combined with aggressive retry policy (no exponential backoff) caused retry storm",
        "resolution": "Implemented exponential backoff with jitter. Added circuit breaker for email provider. Cleared retry queue.",
    },
    # === MEMORY LEAK (4 incidents) ===
    {
        "alert_id": "mem-leak-001",
        "severity": "critical",
        "service": "order-service",
        "incident_class": "memory_leak",
        "description": "Order service memory growing linearly, OOMKilled after 6 hours. Heap dump shows 2.1GB of unreleased HTTP connection objects.",
        "raw_logs": [
            "2026-07-15T02:00:00Z [order-service] WARN memory_usage=78% growing=true",
            "2026-07-15T04:30:00Z [order-service] WARN memory_usage=91% gc_pressure=high",
            "2026-07-15T05:15:00Z [order-service] ERROR OOMKilled container=order-svc-pod-3",
            "2026-07-15T05:15:01Z [kubernetes] INFO pod_restart reason=OOMKilled restart_count=3"
        ],
        "root_cause": "HTTP connection pool not releasing connections when downstream timeout occurs. Connections accumulate over hours.",
        "resolution": "Added connection idle timeout of 30s and max lifetime of 5min. Deployed connection pool monitor.",
    },
    {
        "alert_id": "mem-leak-002",
        "severity": "high",
        "service": "analytics-worker",
        "incident_class": "memory_leak",
        "description": "Analytics worker RSS growing 50MB/hour. Event buffer never flushed when downstream is slow.",
        "raw_logs": [
            "2026-07-16T06:00:00Z [analytics-worker] INFO memory_rss=1.2GB baseline=800MB",
            "2026-07-16T08:00:00Z [analytics-worker] WARN memory_rss=1.8GB growth_rate=50MB/hr",
            "2026-07-16T09:00:00Z [analytics-worker] WARN buffer_size=2.4M_events flush_failures=847",
            "2026-07-16T09:30:00Z [analytics-worker] ERROR memory_rss=2.1GB approaching_limit=2.5GB"
        ],
        "root_cause": "In-memory event buffer grows unbounded when the analytics sink (Kafka) is slow. No backpressure mechanism.",
        "resolution": "Added bounded buffer with max 500K events. Implemented backpressure: drop oldest events when buffer full. Added flush timeout.",
    },
    {
        "alert_id": "mem-leak-003",
        "severity": "high",
        "service": "api-gateway",
        "incident_class": "memory_leak",
        "description": "API Gateway memory at 85% and climbing. Rate limiter cache entries never expire.",
        "raw_logs": [
            "2026-07-17T16:00:00Z [api-gateway] WARN memory_usage=85% cache_entries=4.2M",
            "2026-07-17T16:05:00Z [api-gateway] INFO rate_limiter_cache_size=3.8GB ttl=none",
            "2026-07-17T16:10:00Z [api-gateway] WARN gc_pause_time=450ms frequency=every_30s",
            "2026-07-17T16:15:00Z [api-gateway] ERROR response_latency_p99=3200ms gc_related=true"
        ],
        "root_cause": "Rate limiter per-IP tracking cache configured without TTL. Entries accumulate from all unique IPs over days.",
        "resolution": "Added 5-minute TTL to rate limiter cache. Switched to LRU eviction with max 1M entries. Restarted gateway pods.",
    },
    {
        "alert_id": "mem-leak-004",
        "severity": "medium",
        "service": "recommendation-engine",
        "incident_class": "memory_leak",
        "description": "Recommendation engine memory slowly growing. Model cache holding stale versions.",
        "raw_logs": [
            "2026-07-18T12:00:00Z [recommendation-engine] INFO memory_rss=3.2GB baseline=2.0GB",
            "2026-07-18T12:00:05Z [recommendation-engine] INFO model_cache_versions=12 expected=2",
            "2026-07-18T12:00:10Z [recommendation-engine] WARN stale_model_refs=10 total_size=1.1GB",
            "2026-07-18T12:00:15Z [recommendation-engine] INFO gc_unable_to_collect reason=strong_references"
        ],
        "root_cause": "Model hot-reload keeps strong references to previous model versions. Old models never garbage collected.",
        "resolution": "Switched to weak references for old model versions. Added explicit model unload on version change. Set max cached versions to 2.",
    },
    # === NETWORK PARTITION (4 incidents) ===
    {
        "alert_id": "net-part-001",
        "severity": "critical",
        "service": "database-cluster",
        "incident_class": "network_partition",
        "description": "CockroachDB nodes in us-east-1a lost connectivity to us-east-1b. Split-brain risk detected.",
        "raw_logs": [
            "2026-07-15T15:30:00Z [crdb-node-3] ERROR raft_heartbeat_timeout peer=node-7 duration=15s",
            "2026-07-15T15:30:05Z [crdb-node-4] ERROR raft_heartbeat_timeout peer=node-8 duration=15s",
            "2026-07-15T15:30:10Z [crdb-node-3] WARN range_unavailable ranges=42 quorum_lost=true",
            "2026-07-15T15:30:15Z [load-balancer] ERROR health_check_failed nodes=[node-7,node-8,node-9]"
        ],
        "root_cause": "AWS AZ networking issue caused packet loss between us-east-1a and us-east-1b subnets",
        "resolution": "AWS resolved AZ connectivity issue after 12 minutes. Verified no data inconsistency via consistency checker. No manual intervention needed due to CockroachDB raft consensus.",
    },
    {
        "alert_id": "net-part-002",
        "severity": "high",
        "service": "microservice-mesh",
        "incident_class": "network_partition",
        "description": "Service mesh sidecar proxies timing out between namespace-a and namespace-b. 30% of cross-namespace calls failing.",
        "raw_logs": [
            "2026-07-16T20:00:00Z [envoy-proxy] ERROR upstream_connection_timeout cluster=namespace-b latency=30000ms",
            "2026-07-16T20:00:10Z [istio-pilot] WARN endpoint_health namespace-b healthy=2/8",
            "2026-07-16T20:00:20Z [app-svc-1] ERROR grpc_deadline_exceeded target=inventory-svc",
            "2026-07-16T20:00:30Z [network-policy] INFO deny_count=4500 policy=cross-ns-default"
        ],
        "root_cause": "Network policy update accidentally tightened cross-namespace rules, blocking most inter-service traffic",
        "resolution": "Reverted network policy to previous version. Added CI validation for network policy changes. Implemented canary rollout for policy updates.",
    },
    {
        "alert_id": "net-part-003",
        "severity": "high",
        "service": "cache-cluster",
        "incident_class": "network_partition",
        "description": "Redis cluster split across 2 partitions. Writes accepted on both sides causing data divergence.",
        "raw_logs": [
            "2026-07-17T03:15:00Z [redis-node-1] WARN cluster_state=FAIL nodes_unreachable=3/6",
            "2026-07-17T03:15:05Z [redis-node-4] WARN cluster_state=FAIL nodes_unreachable=3/6",
            "2026-07-17T03:15:10Z [redis-node-1] INFO accepting_writes=true partition=A",
            "2026-07-17T03:15:15Z [redis-node-4] INFO accepting_writes=true partition=B"
        ],
        "root_cause": "Misconfigured Redis cluster min-replicas-to-write allowed writes on both partitions during network split",
        "resolution": "Set cluster-require-full-coverage=yes. Manually reconciled diverged keys. Added partition detection alerting with auto-fence.",
    },
    {
        "alert_id": "net-part-004",
        "severity": "medium",
        "service": "event-bus",
        "incident_class": "network_partition",
        "description": "Kafka brokers in rack-2 unreachable from producers. Messages queuing locally on producer side.",
        "raw_logs": [
            "2026-07-18T07:45:00Z [kafka-producer] WARN broker_unreachable broker_id=3 rack=rack-2",
            "2026-07-18T07:45:10Z [kafka-producer] WARN local_queue_depth=85000 max=100000",
            "2026-07-18T07:45:20Z [kafka-producer] ERROR leader_not_available topic=order-events partition=2",
            "2026-07-18T07:45:30Z [network-monitor] INFO packet_loss rack-1->rack-2 loss=72%"
        ],
        "root_cause": "ToR switch firmware bug caused intermittent packet loss between rack-1 and rack-2 after scheduled maintenance",
        "resolution": "Rebooted affected ToR switch to clear firmware bug. Kafka automatically rebalanced leaders. No message loss due to producer buffering.",
    },
    # === DISK I/O SATURATION (4 incidents) ===
    {
        "alert_id": "disk-io-001",
        "severity": "critical",
        "service": "postgres-primary",
        "incident_class": "disk_io_saturation",
        "description": "Primary PostgreSQL disk I/O at 100% utilization. Write latency 50x normal. All queries timing out.",
        "raw_logs": [
            "2026-07-15T19:00:00Z [postgres] WARN disk_io_util=100% await=450ms normal=8ms",
            "2026-07-15T19:00:05Z [postgres] ERROR checkpoint_timeout disk_write_stall duration=30s",
            "2026-07-15T19:00:10Z [postgres] WARN wal_write_latency=890ms connections_waiting=340",
            "2026-07-15T19:00:15Z [postgres] ERROR too_many_connections_waiting max=500 current=498"
        ],
        "root_cause": "Unoptimized batch job ran full table scan with 50M row UPDATE, generating massive WAL and checkpoint I/O",
        "resolution": "Killed runaway batch job. Implemented batch size limits (10K rows per transaction). Moved batch jobs to read replica.",
    },
    {
        "alert_id": "disk-io-002",
        "severity": "high",
        "service": "elasticsearch-cluster",
        "incident_class": "disk_io_saturation",
        "description": "Elasticsearch merge operations saturating disk. Search latency 10x normal during merge storm.",
        "raw_logs": [
            "2026-07-16T10:30:00Z [es-data-2] WARN merge_throttle_active disk_io=95%",
            "2026-07-16T10:30:10Z [es-data-2] INFO active_merges=12 segments=847 target=50",
            "2026-07-16T10:30:20Z [es-data-2] WARN search_latency_p99=4500ms baseline=400ms",
            "2026-07-16T10:30:30Z [es-data-2] ERROR index_throttled index=logs-2026.07.16 reason=merge_pressure"
        ],
        "root_cause": "Bulk indexing rate exceeded merge capacity. Too many small segments created faster than merges could consolidate.",
        "resolution": "Reduced bulk indexing batch size. Increased merge thread count from 1 to 3. Added index.merge.scheduler.max_merge_count=6.",
    },
    {
        "alert_id": "disk-io-003",
        "severity": "high",
        "service": "logging-pipeline",
        "incident_class": "disk_io_saturation",
        "description": "Log aggregator disk write queue backed up. Logs being dropped at 15% rate.",
        "raw_logs": [
            "2026-07-17T22:00:00Z [fluentd] WARN buffer_queue_full disk_write_pending=12GB",
            "2026-07-17T22:00:10Z [fluentd] ERROR output_retry_exceeded target=s3 retries=5",
            "2026-07-17T22:00:20Z [fluentd] WARN drop_rate=15% reason=buffer_overflow",
            "2026-07-17T22:00:30Z [node-monitor] INFO disk_iops=16000 max_iops=16000 device=nvme0n1"
        ],
        "root_cause": "Sudden 3x log volume spike from debug logging accidentally enabled in production combined with undersized disk IOPS",
        "resolution": "Disabled debug logging in production. Upgraded to io2 volumes with 32K IOPS. Added log sampling for high-volume services.",
    },
    {
        "alert_id": "disk-io-004",
        "severity": "medium",
        "service": "backup-service",
        "incident_class": "disk_io_saturation",
        "description": "Backup service saturating shared EBS volume. Impacting co-located services.",
        "raw_logs": [
            "2026-07-18T01:00:00Z [backup-service] INFO backup_started type=full size_estimate=500GB",
            "2026-07-18T01:15:00Z [monitoring] WARN disk_io_util=98% volume=vol-abc123 shared=true",
            "2026-07-18T01:15:10Z [app-service-1] WARN write_latency=200ms baseline=5ms volume=vol-abc123",
            "2026-07-18T01:15:20Z [app-service-2] ERROR db_connection_timeout volume_related=true"
        ],
        "root_cause": "Full backup running on shared EBS volume without I/O rate limiting. Starving co-located application services.",
        "resolution": "Added ionice/cgroup I/O throttling for backup process. Migrated backup to dedicated volume. Scheduled backups during lowest-traffic window.",
    },
    # === CASCADING SERVICE FAILURE (4 incidents) ===
    {
        "alert_id": "cascade-001",
        "severity": "critical",
        "service": "checkout-flow",
        "incident_class": "cascading_failure",
        "description": "Checkout flow cascading failure. Payment timeout caused cart service backup, which overwhelmed session store.",
        "raw_logs": [
            "2026-07-15T12:00:00Z [payment-gateway] ERROR timeout upstream=stripe latency=30000ms",
            "2026-07-15T12:00:05Z [cart-service] WARN request_queue_depth=5000 blocked_on=payment",
            "2026-07-15T12:00:10Z [session-store] ERROR max_connections_reached limit=1000 current=1000",
            "2026-07-15T12:00:15Z [frontend] ERROR 503_rate=45% all_downstream_failing=true"
        ],
        "root_cause": "Payment gateway timeout (Stripe outage) caused request pile-up. No circuit breaker between cart and payment. Session store overwhelmed by retry connections.",
        "resolution": "Activated circuit breaker on payment calls (fail-fast after 2s). Implemented graceful degradation: allow cart operations without payment verification. Added connection pool limits per upstream.",
    },
    {
        "alert_id": "cascade-002",
        "severity": "critical",
        "service": "data-pipeline",
        "incident_class": "cascading_failure",
        "description": "Data pipeline cascade: Schema registry down caused all Kafka consumers to fail, backing up 50M messages.",
        "raw_logs": [
            "2026-07-16T08:00:00Z [schema-registry] ERROR oom_killed memory=4GB limit=4GB",
            "2026-07-16T08:00:05Z [consumer-group-1] ERROR schema_fetch_failed registry_unavailable=true",
            "2026-07-16T08:00:10Z [consumer-group-2] ERROR deserialization_failed no_schema_available",
            "2026-07-16T08:00:30Z [kafka] WARN consumer_lag topic=events lag=50000000 growing=true"
        ],
        "root_cause": "Schema registry OOM from unbounded schema cache. All consumers depend on registry for deserialization with no local cache fallback.",
        "resolution": "Increased schema registry memory to 8GB. Added local schema cache (last 100 versions) to consumers. Implemented consumer graceful pause when registry unavailable.",
    },
    {
        "alert_id": "cascade-003",
        "severity": "high",
        "service": "user-service",
        "incident_class": "cascading_failure",
        "description": "User service failure cascaded to all dependent services. Auth, profile, preferences all returning 500s.",
        "raw_logs": [
            "2026-07-17T11:30:00Z [user-service] ERROR database_connection_pool_exhausted pool_size=50",
            "2026-07-17T11:30:05Z [auth-service] ERROR user_lookup_failed upstream=user-service status=503",
            "2026-07-17T11:30:10Z [profile-service] ERROR user_data_unavailable fallback=none",
            "2026-07-17T11:30:15Z [api-gateway] WARN error_rate=62% affected_services=5"
        ],
        "root_cause": "User service connection pool exhausted by long-running queries from new analytics feature. No query timeout configured.",
        "resolution": "Added statement_timeout=5s to all user-service DB queries. Increased pool to 100. Added fallback: cached user data for reads when DB unavailable.",
    },
    {
        "alert_id": "cascade-004",
        "severity": "high",
        "service": "cdn-origin",
        "incident_class": "cascading_failure",
        "description": "CDN cache invalidation storm caused origin servers to receive 20x normal traffic. Multiple origins crashed.",
        "raw_logs": [
            "2026-07-18T16:00:00Z [cdn] INFO cache_purge_all triggered_by=deployment reason=asset_update",
            "2026-07-18T16:00:05Z [origin-1] WARN request_rate=50000/s normal=2500/s",
            "2026-07-18T16:00:10Z [origin-2] ERROR oom_killed memory_spike=true",
            "2026-07-18T16:00:15Z [origin-3] ERROR connection_refused max_fd=65536 current=65536"
        ],
        "root_cause": "Deployment triggered full CDN cache purge instead of selective invalidation. All traffic hit origin simultaneously (thundering herd).",
        "resolution": "Implemented staggered cache invalidation (soft purge with 30s stale-while-revalidate). Added origin auto-scaling trigger at 5x normal rate. Changed deployment to use selective path invalidation.",
    },
]


def build_embedding_text(incident: dict) -> str:
    """Build the text that will be embedded for vector search."""
    logs_text = "\n".join(incident.get("raw_logs", []))
    return (
        f"Service: {incident['service']}\n"
        f"Severity: {incident['severity']}\n"
        f"Description: {incident['description']}\n"
        f"Logs:\n{logs_text}\n"
        f"Root Cause: {incident['root_cause']}\n"
        f"Resolution: {incident['resolution']}"
    )


def insert_incidents(conn, incidents: list[dict]) -> dict[str, str]:
    """Insert incidents into the database. Returns mapping of alert_id -> uuid."""
    alert_to_uuid = {}
    now = datetime.now(timezone.utc)

    for i, inc in enumerate(incidents):
        incident_id = str(uuid.uuid4())
        resolved_at = now - timedelta(days=len(incidents) - i, hours=2)
        created_at = resolved_at - timedelta(hours=1)

        conn.execute(
            """
            INSERT INTO incidents (id, alert_id, severity, service, description,
                                   raw_logs, root_cause, resolution, incident_class,
                                   resolved_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                incident_id,
                inc["alert_id"],
                inc["severity"],
                inc["service"],
                inc["description"],
                json.dumps(inc["raw_logs"]),
                inc["root_cause"],
                inc["resolution"],
                inc["incident_class"],
                resolved_at,
                created_at,
            ),
        )
        alert_to_uuid[inc["alert_id"]] = incident_id

    conn.commit()
    return alert_to_uuid


def insert_embeddings(conn, incidents: list[dict], alert_to_uuid: dict, use_bedrock: bool = True) -> None:
    """Generate and insert embeddings for each incident."""
    if use_bedrock:
        from agents.bedrock_client import bedrock

    for inc in incidents:
        incident_id = alert_to_uuid[inc["alert_id"]]
        chunk_text = build_embedding_text(inc)

        if use_bedrock:
            embedding = bedrock.generate_embedding(chunk_text)
        else:
            # Deterministic fake embedding for testing without AWS
            import hashlib
            seed_bytes = hashlib.sha256(chunk_text.encode()).digest()
            import struct
            # Generate 1024 floats from repeated hash
            fake_vec = []
            for i in range(0, 1024, 4):
                h = hashlib.sha256(seed_bytes + struct.pack("i", i)).digest()
                val = struct.unpack("f", h[:4])[0]
                # Normalize to [-1, 1]
                val = (val % 2.0) - 1.0
                fake_vec.append(round(val, 6))
            embedding = fake_vec

        # Format as CockroachDB vector literal
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

        conn.execute(
            """
            INSERT INTO incident_embeddings (id, incident_id, embedding, chunk_text, metadata, embedding_type)
            VALUES (%s, %s, %s::VECTOR(1024), %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                incident_id,
                vec_str,
                chunk_text,
                json.dumps({
                    "service": inc["service"],
                    "incident_class": inc["incident_class"],
                    "severity": inc["severity"],
                }),
                "incident",
            ),
        )

        # Also embed just the resolution (for memory recall)
        resolution_text = f"Resolution for {inc['incident_class']} in {inc['service']}: {inc['resolution']}"
        if use_bedrock:
            res_embedding = bedrock.generate_embedding(resolution_text)
        else:
            h = hashlib.sha256(resolution_text.encode()).digest()
            res_embedding = []
            for i in range(0, 1024, 4):
                hh = hashlib.sha256(h + struct.pack("i", i)).digest()
                val = struct.unpack("f", hh[:4])[0]
                val = (val % 2.0) - 1.0
                res_embedding.append(round(val, 6))

        res_vec_str = "[" + ",".join(str(v) for v in res_embedding) + "]"

        conn.execute(
            """
            INSERT INTO incident_embeddings (id, incident_id, embedding, chunk_text, metadata, embedding_type)
            VALUES (%s, %s, %s::VECTOR(1024), %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                incident_id,
                res_vec_str,
                resolution_text,
                json.dumps({
                    "service": inc["service"],
                    "incident_class": inc["incident_class"],
                    "type": "resolution",
                }),
                "resolution",
            ),
        )

    conn.commit()


def main() -> None:
    """Seed the database with incidents and embeddings."""
    parser = argparse.ArgumentParser(description="Seed CockroachDB with sample incidents")
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Skip embedding generation (use fake vectors for testing)",
    )
    args = parser.parse_args()

    use_bedrock = not args.skip_embeddings

    print("=" * 50)
    print("SRE Memory Agent - Seed Data")
    print("=" * 50)
    print(f"Incidents to seed: {len(SEED_INCIDENTS)}")
    print(f"Embedding source: {'AWS Bedrock Titan' if use_bedrock else 'Fake (deterministic)'}")
    print(f"Database: {settings.cockroachdb_host}")
    print()

    import psycopg
    from psycopg.rows import dict_row

    try:
        conn = psycopg.connect(settings.cockroachdb_dsn, row_factory=dict_row)
    except Exception as e:
        print(f"ERROR: Could not connect to CockroachDB: {e}")
        print("Ensure migrations have been applied first: python data/migrations/apply.py")
        sys.exit(1)

    with conn:
        # Check if already seeded
        result = conn.execute("SELECT COUNT(*) as cnt FROM incidents")
        row = result.fetchone()
        if row and row["cnt"] > 0:
            print(f"Database already has {row['cnt']} incidents. Skipping seed.")
            print("To re-seed, truncate the incidents table first:")
            print("  TRUNCATE incidents CASCADE;")
            conn.close()
            return

        # Insert incidents
        print("[1/2] Inserting incidents...")
        alert_to_uuid = insert_incidents(conn, SEED_INCIDENTS)
        print(f"  Inserted {len(alert_to_uuid)} incidents.")

        # Generate and insert embeddings
        print("[2/2] Generating embeddings and inserting...")
        insert_embeddings(conn, SEED_INCIDENTS, alert_to_uuid, use_bedrock=use_bedrock)
        print(f"  Inserted {len(SEED_INCIDENTS) * 2} embeddings (incident + resolution per record).")

    conn.close()
    print()
    print("Seed complete!")
    print()
    print("Verify with:")
    print("  SELECT service, incident_class, severity FROM incidents LIMIT 5;")
    print("  SELECT COUNT(*) FROM incident_embeddings;")


if __name__ == "__main__":
    main()
