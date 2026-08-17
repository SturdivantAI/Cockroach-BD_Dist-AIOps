"""Retrieval Agent - Queries CockroachDB for similar past incidents via vector search.

This agent:
1. Takes an alert payload as input
2. Generates an embedding via Bedrock Titan
3. Queries CockroachDB incident_embeddings with cosine similarity (top-k)
4. Queries agent_memory for prior reasoning about this service/alert type
5. Returns structured context for the Analysis Agent

Uses CockroachDB Distributed Vector Indexing (hackathon requirement).
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agents.bedrock_client import bedrock
from agents.config import settings
from agents.db import execute_command, execute_query

logger = logging.getLogger(__name__)


class RetrievalResult:
    """Structured result from the Retrieval Agent."""

    def __init__(
        self,
        similar_incidents: list[dict[str, Any]],
        prior_reasoning: list[dict[str, Any]],
        confidence: float,
        memory_available: bool,
        query_embedding: list[float] | None = None,
    ):
        self.similar_incidents = similar_incidents
        self.prior_reasoning = prior_reasoning
        self.confidence = confidence
        self.memory_available = memory_available
        self.query_embedding = query_embedding

    def to_dict(self) -> dict[str, Any]:
        """Serialize for Step Functions payload."""
        return {
            "similar_incidents": self.similar_incidents,
            "prior_reasoning": self.prior_reasoning,
            "confidence": self.confidence,
            "memory_available": self.memory_available,
            "num_similar": len(self.similar_incidents),
            "num_prior_reasoning": len(self.prior_reasoning),
        }


def _build_query_text(alert: dict[str, Any]) -> str:
    """Build the text to embed for similarity search."""
    parts = [
        f"Service: {alert.get('service', 'unknown')}",
        f"Severity: {alert.get('severity', 'unknown')}",
        f"Description: {alert.get('description', '')}",
    ]

    logs = alert.get("logs") or alert.get("raw_logs") or []
    if logs:
        if isinstance(logs, list):
            parts.append("Logs:\n" + "\n".join(logs[:10]))
        else:
            parts.append(f"Logs:\n{str(logs)[:500]}")

    return "\n".join(parts)


def _vector_search(
    embedding: list[float],
    top_k: int = 5,
    exclude_incident_id: str | None = None,
) -> list[dict[str, Any]]:
    """Query CockroachDB Distributed Vector Index for similar incidents.

    Uses cosine similarity via the vector_cosine index.
    """
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

    query = """
        SELECT
            ie.incident_id,
            ie.chunk_text,
            ie.metadata,
            ie.embedding_type,
            i.alert_id,
            i.service,
            i.severity,
            i.description,
            i.root_cause,
            i.resolution,
            i.incident_class,
            i.resolved_at,
            (ie.embedding <=> %s::VECTOR(1024)) AS distance
        FROM incident_embeddings ie
        JOIN incidents i ON i.id = ie.incident_id
        WHERE i.resolved_at IS NOT NULL
    """
    params: list[Any] = [vec_str]

    if exclude_incident_id:
        query += " AND ie.incident_id != %s"
        params.append(exclude_incident_id)

    query += " ORDER BY distance ASC LIMIT %s"
    params.append(top_k)

    results = execute_query(query, tuple(params))

    # Format results
    similar = []
    for row in results:
        similar.append({
            "incident_id": str(row["incident_id"]),
            "alert_id": row["alert_id"],
            "service": row["service"],
            "severity": row["severity"],
            "description": row["description"],
            "root_cause": row["root_cause"],
            "resolution": row["resolution"],
            "incident_class": row["incident_class"],
            "embedding_type": row["embedding_type"],
            "similarity_score": round(1.0 - float(row["distance"]), 4),
            "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
        })

    return similar


def _query_prior_reasoning(
    service: str | None = None,
    incident_class: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Query agent_memory for prior reasoning about this service or incident type."""
    conditions = []
    params: list[Any] = []

    if service:
        conditions.append(
            "content->>'service' = %s OR content->>'related_service' = %s"
        )
        params.extend([service, service])

    if incident_class:
        conditions.append("content->>'incident_class' = %s")
        params.append(incident_class)

    if not conditions:
        return []

    where_clause = " OR ".join(f"({c})" for c in conditions)

    query = f"""
        SELECT
            id, session_id, agent_name, step_type, content, created_at
        FROM agent_memory
        WHERE ({where_clause})
          AND step_type IN ('outcome', 'hypothesis', 'action')
        ORDER BY created_at DESC
        LIMIT %s
    """
    params.append(limit)

    try:
        results = execute_query(query, tuple(params))
    except Exception as e:
        logger.warning("Failed to query prior reasoning: %s", e)
        return []

    reasoning = []
    for row in results:
        content = row["content"] if isinstance(row["content"], dict) else json.loads(row["content"])
        reasoning.append({
            "memory_id": str(row["id"]),
            "session_id": str(row["session_id"]),
            "agent_name": row["agent_name"],
            "step_type": row["step_type"],
            "content": content,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        })

    return reasoning


def _write_retrieval_to_memory(
    session_id: str,
    incident_id: str | None,
    alert: dict[str, Any],
    result: RetrievalResult,
) -> None:
    """Write retrieval step to agent_memory for future reference."""
    content = {
        "service": alert.get("service"),
        "alert_description": alert.get("description", "")[:200],
        "similar_count": len(result.similar_incidents),
        "memory_available": result.memory_available,
        "top_match_score": (
            result.similar_incidents[0]["similarity_score"]
            if result.similar_incidents
            else 0.0
        ),
        "top_match_class": (
            result.similar_incidents[0].get("incident_class")
            if result.similar_incidents
            else None
        ),
    }

    try:
        execute_command(
            """
            INSERT INTO agent_memory (id, session_id, incident_id, agent_name, step_type, content)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                session_id,
                incident_id,
                "retrieval",
                "observation",
                json.dumps(content),
            ),
        )
    except Exception as e:
        logger.warning("Failed to write retrieval memory: %s", e)


def run(
    alert: dict[str, Any],
    session_id: str | None = None,
    incident_id: str | None = None,
    top_k: int | None = None,
) -> RetrievalResult:
    """Execute the Retrieval Agent.

    Args:
        alert: Alert payload with service, severity, description, logs.
        session_id: Current workflow session ID.
        incident_id: The incident ID if already ingested.
        top_k: Override for number of similar results to retrieve.

    Returns:
        RetrievalResult with similar incidents and prior reasoning.
    """
    if session_id is None:
        session_id = str(uuid.uuid4())
    if top_k is None:
        top_k = settings.retrieval_top_k

    logger.info(
        "Retrieval agent running for service=%s, session=%s",
        alert.get("service"),
        session_id,
    )

    # Step 1: Build query text and generate embedding
    query_text = _build_query_text(alert)
    logger.info("Generating embedding for query (%d chars)", len(query_text))

    try:
        embedding = bedrock.generate_embedding(query_text)
    except Exception as e:
        logger.error("Failed to generate embedding: %s", e)
        return RetrievalResult(
            similar_incidents=[],
            prior_reasoning=[],
            confidence=0.0,
            memory_available=False,
        )

    # Step 2: Vector similarity search in CockroachDB
    logger.info("Querying CockroachDB vector index (top_k=%d)", top_k)
    try:
        similar_incidents = _vector_search(
            embedding=embedding,
            top_k=top_k,
            exclude_incident_id=incident_id,
        )
    except Exception as e:
        logger.error("Vector search failed: %s", e)
        similar_incidents = []

    # Step 3: Query prior reasoning from agent_memory
    service = alert.get("service")
    incident_class = (
        similar_incidents[0].get("incident_class")
        if similar_incidents
        else None
    )

    logger.info(
        "Querying prior reasoning (service=%s, class=%s)",
        service,
        incident_class,
    )
    prior_reasoning = _query_prior_reasoning(
        service=service,
        incident_class=incident_class,
    )

    # Step 4: Compute confidence based on retrieval quality
    memory_available = len(similar_incidents) > 0 or len(prior_reasoning) > 0

    if similar_incidents:
        top_score = similar_incidents[0]["similarity_score"]
        # Confidence is based on how similar the top match is
        confidence = min(top_score, 0.95)
    else:
        confidence = 0.0

    result = RetrievalResult(
        similar_incidents=similar_incidents,
        prior_reasoning=prior_reasoning,
        confidence=confidence,
        memory_available=memory_available,
        query_embedding=embedding,
    )

    # Step 5: Write retrieval observation to memory
    _write_retrieval_to_memory(session_id, incident_id, alert, result)

    logger.info(
        "Retrieval complete: %d similar incidents, %d prior reasoning entries, confidence=%.3f",
        len(similar_incidents),
        len(prior_reasoning),
        confidence,
    )

    return result
