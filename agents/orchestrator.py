"""Proposal-only orchestration: ingest, retrieve/validate, analyze, decide, record.

This runner performs no operational remediation and never auto-approves actions.
"""

import time
import uuid
from typing import Any


class WorkflowResult:
    """Inspectable workflow state, including failures and memory gate decisions."""

    def __init__(self) -> None:
        self.session_id = ""
        self.incident_id = ""
        self.ingestion: dict[str, Any] = {}
        self.retrieval: dict[str, Any] = {}
        self.analysis: dict[str, Any] = {}
        self.decision: dict[str, Any] = {}
        self.memory_write: dict[str, Any] = {}
        self.total_elapsed_ms = 0
        self.steps_completed: list[str] = []
        self.errors: list[dict[str, Any]] = []
        self.status = "pending"
        self.execution_status = "not_executed"
        self.approval_status = "not_requested"

    @property
    def success(self) -> bool:
        return not self.errors and self.status in ("proposal_ready", "proposal_recorded")

    def to_dict(self) -> dict[str, Any]:
        return {**vars(self), "success": self.success}


def run_workflow(
    alert: dict[str, Any], session_id: str | None = None,
    skip_approval: bool = True, write_memory: bool = True,
    validation_enabled: bool | None = None,
) -> WorkflowResult:
    """Generate a proposal; stop on stage errors instead of fabricating success.

    skip_approval is retained for v1 callers but grants no permission and triggers
    no execution. Approval remains pending when the proposed action requires it.
    """
    from agents import lambda_handlers as handlers
    from agents.config import settings

    result = WorkflowResult()
    result.session_id = session_id or str(uuid.uuid4())
    started = time.perf_counter()
    enabled = settings.memory_validation_enabled if validation_enabled is None else validation_enabled
    result.inference_provider = settings.inference_provider
    result.model_id = settings.nebius_model_id if settings.inference_provider == "nebius" else settings.bedrock_model_id
    result.embedding_model_id = settings.bedrock_embedding_model_id
    result.validation_enabled = enabled

    def stage(name: str, handler: Any, event: dict[str, Any]) -> dict[str, Any]:
        payload = handler(event, None)
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError(f"{name} failed; inspect the recorded handler error")
        result.steps_completed.append(name)
        return payload

    current = "ingestion"
    try:
        result.ingestion = stage(current, handlers.ingest_handler, {**alert, "session_id": result.session_id})
        result.incident_id = result.ingestion.get("incident_id", "")
        if not result.incident_id:
            raise RuntimeError("Ingestion returned no incident ID")
        current = "retrieval"
        result.retrieval = stage(current, handlers.retrieval_handler, {
            "ingestion": {"Payload": result.ingestion}, "validation_enabled": enabled,
        })
        current = "analysis"
        result.analysis = stage(current, handlers.analysis_handler, {
            "retrieval": {"Payload": result.retrieval}, "ingestion": {"Payload": result.ingestion},
        })
        current = "decision"
        result.decision = stage(current, handlers.decision_handler, {
            "analysis": {"Payload": result.analysis}, "retrieval": {"Payload": result.retrieval},
            "ingestion": {"Payload": result.ingestion},
        })
        if not result.decision.get("decision_id"):
            raise RuntimeError("Decision was not persisted")
        result.approval_status = "pending" if result.decision.get("requires_approval") else "not_requested"
        result.status = "proposal_ready"
        if write_memory:
            current = "write_memory"
            result.memory_write = stage(current, handlers.write_memory_handler, {
                "ingestion": {"Payload": result.ingestion}, "retrieval": {"Payload": result.retrieval},
                "analysis": {"Payload": result.analysis}, "decision": {"Payload": result.decision},
            })
            if result.memory_write.get("status") != "proposal_recorded":
                raise RuntimeError("Proposal recording returned an unexpected status")
            result.status = "proposal_recorded"
    except Exception as exc:
        result.status = "failed"
        result.errors.append({"step": current, "error": str(exc)})
    finally:
        result.total_elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result
