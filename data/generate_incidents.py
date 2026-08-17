"""Programmatic incident variant generator using Bedrock Claude.

Generates new incident variants for each class by paraphrasing symptoms,
changing service names, and varying severity while preserving the same
root cause pattern. Useful for expanding the evaluation dataset.

Usage:
    python data/generate_incidents.py --class cpu_spike --count 2
    python data/generate_incidents.py --all --count 1
    python data/generate_incidents.py --all --count 2 --output data/sample_incidents/generated.json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Templates for each incident class with the core pattern
INCIDENT_CLASS_TEMPLATES = {
    "cpu_spike": {
        "root_cause_pattern": "Computational resource exhaustion from unbounded/inefficient processing",
        "resolution_pattern": "Identify and bound the runaway computation, deploy fix, add guards",
        "categories": ["unbounded_recursion", "unbounded_concurrency", "regex_backtracking", "hot_loop"],
        "services_pool": [
            "billing-service", "search-api", "ml-inference", "data-export",
            "scheduler-service", "webhook-dispatcher", "image-processor",
        ],
        "example_symptoms": [
            "CPU utilization exceeded 90%",
            "Request latency increased 10x",
            "Thread pool exhausted",
            "GC pause times elevated",
        ],
    },
    "memory_leak": {
        "root_cause_pattern": "Resource retention without proper cleanup or bounded lifecycle",
        "resolution_pattern": "Add TTL/bounds to resource pools, fix reference retention, add monitoring",
        "categories": ["stale_resource_retention", "resource_leak_on_error_path", "unbounded_cache", "connection_pool_leak"],
        "services_pool": [
            "chat-service", "streaming-proxy", "grpc-gateway", "task-queue",
            "metrics-collector", "session-manager", "websocket-relay",
        ],
        "example_symptoms": [
            "Memory RSS growing linearly",
            "OOMKilled after hours of uptime",
            "GC unable to collect objects",
            "File handle count growing",
        ],
    },
    "network_partition": {
        "root_cause_pattern": "Connectivity loss between components causing split-brain or unavailability",
        "resolution_pattern": "Restore connectivity, verify data consistency, add partition detection",
        "categories": ["split_brain_from_config_change", "network_misconfiguration", "dns_failure", "mtu_mismatch"],
        "services_pool": [
            "consensus-cluster", "replication-service", "gossip-protocol",
            "service-registry", "leader-election", "distributed-cache",
        ],
        "example_symptoms": [
            "Peer heartbeat timeout",
            "Split-brain detected",
            "Quorum lost",
            "Cross-zone connectivity failed",
        ],
    },
    "disk_io_saturation": {
        "root_cause_pattern": "Storage bandwidth exhaustion from unbounded writes or competing workloads",
        "resolution_pattern": "Identify and throttle the I/O source, separate workloads, upgrade storage tier",
        "categories": ["ingest_exceeds_compaction", "aggressive_cache_warming", "log_flood", "backup_contention"],
        "services_pool": [
            "write-ahead-log", "snapshot-service", "compaction-worker",
            "index-builder", "archive-service", "wal-replayer",
        ],
        "example_symptoms": [
            "Disk I/O utilization at 100%",
            "Write latency 50x normal",
            "Compaction backlog growing",
            "Write stall detected",
        ],
    },
    "cascading_failure": {
        "root_cause_pattern": "Single component failure propagating through dependency chain due to missing isolation",
        "resolution_pattern": "Isolate failure domain, add circuit breakers, implement graceful degradation",
        "categories": ["timeout_cascade_no_circuit_breaker", "resource_exhaustion_cascade", "thundering_herd", "retry_storm"],
        "services_pool": [
            "checkout-api", "logistics-orchestrator", "notification-hub",
            "feed-aggregator", "payment-orchestrator", "sync-service",
        ],
        "example_symptoms": [
            "Cascading 503 errors across services",
            "Request queue depth growing unbounded",
            "Circuit breaker not configured",
            "Downstream timeout causing upstream pile-up",
        ],
    },
}


GENERATION_PROMPT = """You are generating synthetic incident data for an SRE training system.
Generate a realistic incident variant for the class: {incident_class}

Root cause pattern for this class: {root_cause_pattern}
Resolution pattern: {resolution_pattern}
Target service: {service}
Target category: {category}

Generate a JSON object with these exact fields:
{{
  "alert_id": "gen-{incident_class}-{unique_id}",
  "service": "{service}",
  "severity": "<critical|high|medium>",
  "description": "<1-2 sentence alert description with specific metrics>",
  "logs": [
    "<5 realistic timestamped log lines showing the progression of the incident>"
  ],
  "root_cause": "<specific technical root cause matching the class pattern>",
  "resolution": "<specific actionable resolution steps>",
  "category": "{category}",
  "time_to_resolve_minutes": <integer between 15 and 90>
}}

Requirements:
- Logs must have timestamps in ISO 8601 format (2026-07-25T...)
- Logs should show progression: warning -> error -> impact
- Description should include specific numbers (percentages, latencies, counts)
- Root cause must be technically specific, not generic
- Resolution must include concrete steps (not just "fix it")
- Make it realistic enough that a human SRE would believe this is a real incident

Return ONLY the JSON object, no markdown formatting or explanation."""


def generate_variant(
    incident_class: str,
    bedrock_client,
    service: str | None = None,
    category: str | None = None,
) -> dict:
    """Generate a single incident variant using Bedrock Claude."""
    import random

    template = INCIDENT_CLASS_TEMPLATES[incident_class]

    if service is None:
        service = random.choice(template["services_pool"])
    if category is None:
        category = random.choice(template["categories"])

    unique_id = str(uuid.uuid4())[:8]

    prompt = GENERATION_PROMPT.format(
        incident_class=incident_class,
        root_cause_pattern=template["root_cause_pattern"],
        resolution_pattern=template["resolution_pattern"],
        service=service,
        category=category,
        unique_id=unique_id,
    )

    response = bedrock_client.invoke_claude(
        prompt=prompt,
        system="You are a precise JSON generator. Return only valid JSON.",
        temperature=0.7,
    )

    # Parse the response - handle potential markdown wrapping
    text = response.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        variant = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  WARNING: Failed to parse Claude response: {e}")
        print(f"  Raw response: {text[:200]}...")
        return None

    # Ensure required fields
    variant["incident_class"] = incident_class
    variant.setdefault("alert_id", f"gen-{incident_class}-{unique_id}")
    variant.setdefault("service", service)

    return variant


def generate_paired_variant(
    incident_class: str,
    first_encounter: dict,
    bedrock_client,
) -> dict:
    """Generate a memory_assisted variant paired with a first_encounter incident."""
    import random

    template = INCIDENT_CLASS_TEMPLATES[incident_class]
    service = random.choice(template["services_pool"])
    unique_id = str(uuid.uuid4())[:8]

    prompt = f"""You are generating a PAIRED incident for an SRE memory improvement demo.

The FIRST incident (already resolved) was:
- Service: {first_encounter['service']}
- Description: {first_encounter['description']}
- Root Cause: {first_encounter['root_cause']}
- Resolution: {first_encounter['resolution']}
- Category: {first_encounter.get('category', 'unknown')}

Now generate a SECOND incident that:
1. Has the SAME root cause CATEGORY (same type of problem)
2. Affects a DIFFERENT service: {service}
3. Has different surface symptoms (different metrics, different log patterns)
4. Would be FASTER to resolve if the agent remembers the first incident

Generate a JSON object:
{{
  "alert_id": "gen-{incident_class}-paired-{unique_id}",
  "service": "{service}",
  "severity": "<critical|high|medium>",
  "description": "<different symptoms, same underlying pattern>",
  "logs": ["<5 different log lines showing similar progression pattern>"],
  "root_cause": "<same category as first, different specific details>",
  "resolution": "<similar approach to first, adapted for this service>",
  "category": "{first_encounter.get('category', template['categories'][0])}",
  "time_to_resolve_minutes": <should be 40-60% of first incident's time>,
  "pair_role": "memory_assisted",
  "pair_with": "{first_encounter['alert_id']}"
}}

Return ONLY the JSON object."""

    response = bedrock_client.invoke_claude(
        prompt=prompt,
        system="You are a precise JSON generator. Return only valid JSON.",
        temperature=0.6,
    )

    text = response.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        variant = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  WARNING: Failed to parse paired variant response: {e}")
        return None

    variant["incident_class"] = incident_class
    variant.setdefault("pair_role", "memory_assisted")
    variant.setdefault("pair_with", first_encounter["alert_id"])

    return variant


def main() -> None:
    """Generate incident variants."""
    parser = argparse.ArgumentParser(description="Generate synthetic incident variants via Bedrock Claude")
    parser.add_argument(
        "--class",
        dest="incident_class",
        choices=list(INCIDENT_CLASS_TEMPLATES.keys()),
        help="Incident class to generate variants for",
    )
    parser.add_argument("--all", action="store_true", help="Generate for all classes")
    parser.add_argument("--count", type=int, default=1, help="Number of variants per class (default: 1)")
    parser.add_argument(
        "--paired", action="store_true",
        help="Also generate a paired memory_assisted variant for each",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/sample_incidents/generated.json",
        help="Output file path",
    )
    args = parser.parse_args()

    if not args.all and not args.incident_class:
        parser.error("Specify --class <name> or --all")

    classes = list(INCIDENT_CLASS_TEMPLATES.keys()) if args.all else [args.incident_class]

    print("=" * 50)
    print("SRE Memory Agent - Incident Generator")
    print("=" * 50)
    print(f"Classes: {', '.join(classes)}")
    print(f"Variants per class: {args.count}")
    print(f"Generate pairs: {args.paired}")
    print(f"Output: {args.output}")
    print()

    from agents.bedrock_client import bedrock

    generated = []
    for cls in classes:
        print(f"Generating for class: {cls}")
        for i in range(args.count):
            print(f"  Variant {i + 1}/{args.count}...", end=" ")
            variant = generate_variant(cls, bedrock)
            if variant:
                variant["pair_role"] = "first_encounter"
                generated.append(variant)
                print("OK")

                if args.paired:
                    print(f"  Paired variant...", end=" ")
                    paired = generate_paired_variant(cls, variant, bedrock)
                    if paired:
                        generated.append(paired)
                        print("OK")
                    else:
                        print("FAILED")
            else:
                print("FAILED")

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator_version": "1.0.0",
        "total_variants": len(generated),
        "incidents": generated,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print()
    print(f"Generated {len(generated)} incident(s) -> {output_path}")
    print()

    # Print sample
    if generated:
        sample = generated[0]
        print("Sample generated incident:")
        print(f"  Service: {sample.get('service')}")
        print(f"  Severity: {sample.get('severity')}")
        print(f"  Description: {sample.get('description', '')[:100]}...")
        print(f"  Root Cause: {sample.get('root_cause', '')[:100]}...")


if __name__ == "__main__":
    main()
