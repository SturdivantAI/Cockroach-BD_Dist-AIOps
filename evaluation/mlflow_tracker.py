"""MLflow experiment tracking for the SRE Memory Agent evaluation pipeline.

Logs each agent run as an MLflow experiment run with:
- Parameters: model_id, temperature, top_k_retrieval, memory_enabled
- Metrics: judge_score, time_to_resolution, confidence, retrieval_relevance
- Artifacts: full reasoning chain, retrieved context, ground truth comparison

Integrates with Databricks MLflow for centralized tracking.

Usage:
    from evaluation.mlflow_tracker import track_run, track_pair_evaluation
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_mlflow_client():
    """Initialize MLflow with Databricks tracking URI."""
    import mlflow
    from agents.config import settings

    if settings.databricks_host and settings.databricks_token:
        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    else:
        # Local fallback for development
        mlflow.set_tracking_uri("file:./mlruns")

    mlflow.set_experiment(settings.mlflow_experiment_name)
    return mlflow


def track_run(
    workflow_result: dict[str, Any],
    ground_truth: dict[str, Any] | None = None,
    judge_score: dict[str, Any] | None = None,
    run_name: str | None = None,
) -> str:
    """Track a single agent workflow run in MLflow.

    Args:
        workflow_result: Full output from orchestrator.run_workflow().
        ground_truth: Optional ground truth for comparison.
        judge_score: Optional judge scores dict.
        run_name: Optional name for the run.

    Returns:
        MLflow run_id.
    """
    mlflow = _get_mlflow_client()
    from agents.config import settings

    ingestion = workflow_result.get("ingestion", {})
    retrieval = workflow_result.get("retrieval", {})
    analysis = workflow_result.get("analysis", {})
    decision = workflow_result.get("decision", {})

    with mlflow.start_run(run_name=run_name) as run:
        # Parameters
        mlflow.log_params({
            "model_id": settings.bedrock_model_id,
            "embedding_model_id": settings.bedrock_embedding_model_id,
            "temperature": 0.3,
            "top_k_retrieval": settings.retrieval_top_k,
            "confidence_threshold": settings.confidence_threshold,
            "memory_enabled": retrieval.get("memory_available", False),
            "service": ingestion.get("service", "unknown"),
            "severity": ingestion.get("severity", "unknown"),
            "incident_class_guess": analysis.get("incident_class_guess", "unknown"),
            "action_type": decision.get("action_type", "unknown"),
        })

        # Metrics
        mlflow.log_metrics({
            "confidence": analysis.get("confidence", 0.0),
            "reasoning_steps": analysis.get("reasoning_steps_count", 0),
            "similar_incidents_found": retrieval.get("num_similar", 0),
            "retrieval_confidence": retrieval.get("confidence", 0.0),
            "total_elapsed_ms": workflow_result.get("total_elapsed_ms", 0),
            "retrieval_elapsed_ms": retrieval.get("elapsed_ms", 0),
            "analysis_elapsed_ms": analysis.get("elapsed_ms", 0),
            "decision_elapsed_ms": decision.get("elapsed_ms", 0),
        })

        # Judge scores (if available)
        if judge_score:
            mlflow.log_metrics({
                "judge_accuracy": judge_score.get("accuracy", 0),
                "judge_resolution": judge_score.get("resolution", 0),
                "judge_reasoning": judge_score.get("reasoning", 0),
                "judge_evidence": judge_score.get("evidence", 0),
                "judge_total": judge_score.get("total", 0),
            })

        # Artifacts
        artifacts_dir = Path("mlflow_artifacts_tmp")
        artifacts_dir.mkdir(exist_ok=True)

        # Reasoning chain artifact
        reasoning_path = artifacts_dir / "reasoning_chain.json"
        with open(reasoning_path, "w") as f:
            json.dump({
                "hypothesis": analysis.get("hypothesis", ""),
                "reasoning_chain": analysis.get("reasoning_chain", []),
                "evidence": analysis.get("evidence", []),
                "confidence": analysis.get("confidence", 0.0),
            }, f, indent=2)
        mlflow.log_artifact(str(reasoning_path))

        # Retrieved context artifact
        context_path = artifacts_dir / "retrieved_context.json"
        with open(context_path, "w") as f:
            json.dump({
                "similar_incidents": retrieval.get("similar_incidents", []),
                "prior_reasoning": retrieval.get("prior_reasoning", []),
                "memory_available": retrieval.get("memory_available", False),
            }, f, indent=2)
        mlflow.log_artifact(str(context_path))

        # Ground truth comparison (if available)
        if ground_truth:
            comparison_path = artifacts_dir / "ground_truth_comparison.json"
            with open(comparison_path, "w") as f:
                json.dump({
                    "agent_hypothesis": analysis.get("hypothesis", ""),
                    "ground_truth_root_cause": ground_truth.get("root_cause", ""),
                    "agent_resolution": decision.get("action", ""),
                    "ground_truth_resolution": ground_truth.get("resolution", ""),
                }, f, indent=2)
            mlflow.log_artifact(str(comparison_path))

        # Cleanup temp artifacts
        for p in artifacts_dir.glob("*"):
            p.unlink()
        artifacts_dir.rmdir()

        return run.info.run_id


def track_pair_evaluation(pair_result: dict[str, Any], class_name: str) -> tuple[str, str]:
    """Track a paired evaluation (Run 1 + Run 2) as two linked MLflow runs.

    Args:
        pair_result: PairResult.to_dict() output.
        class_name: Incident class name.

    Returns:
        Tuple of (run1_id, run2_id).
    """
    mlflow = _get_mlflow_client()
    from agents.config import settings

    run1 = pair_result.get("run1", {})
    run2 = pair_result.get("run2", {})
    deltas = pair_result.get("deltas", {})

    # Run 1: Without memory
    with mlflow.start_run(run_name=f"{class_name}_no_memory") as r1:
        mlflow.log_params({
            "model_id": settings.bedrock_model_id,
            "memory_enabled": False,
            "incident_class": pair_result.get("class_id", "unknown"),
            "pair_role": "first_encounter",
            "evaluation_type": "paired",
        })
        mlflow.log_metrics({
            "confidence": run1.get("confidence", 0.0),
            "reasoning_steps": run1.get("reasoning_steps", 0),
            "elapsed_ms": run1.get("elapsed_ms", 0),
            "memory_available": 0,
        })
        run1_id = r1.info.run_id

    # Run 2: With memory
    with mlflow.start_run(run_name=f"{class_name}_with_memory") as r2:
        mlflow.log_params({
            "model_id": settings.bedrock_model_id,
            "memory_enabled": True,
            "incident_class": pair_result.get("class_id", "unknown"),
            "pair_role": "memory_assisted",
            "evaluation_type": "paired",
            "paired_with_run": run1_id,
        })
        mlflow.log_metrics({
            "confidence": run2.get("confidence", 0.0),
            "reasoning_steps": run2.get("reasoning_steps", 0),
            "elapsed_ms": run2.get("elapsed_ms", 0),
            "memory_available": 1,
            "cited_prior_resolution": 1 if run2.get("cited_prior_resolution") else 0,
            "confidence_delta": deltas.get("confidence_delta", 0.0),
            "steps_delta": deltas.get("steps_delta", 0),
            "time_delta_ms": deltas.get("time_delta_ms", 0),
        })
        run2_id = r2.info.run_id

    return run1_id, run2_id


def track_judge_scores(
    judge_results: list[dict[str, Any]],
) -> list[str]:
    """Track judge scoring results as MLflow runs.

    Args:
        judge_results: List of judge result dicts from judge.py.

    Returns:
        List of MLflow run_ids.
    """
    mlflow = _get_mlflow_client()
    run_ids = []

    for result in judge_results:
        class_id = result.get("class_id", "unknown")
        score_without = result.get("score_without_memory", {})
        score_with = result.get("score_with_memory", {})

        with mlflow.start_run(run_name=f"judge_{class_id}") as run:
            mlflow.log_params({
                "class_id": class_id,
                "class_name": result.get("class_name", ""),
                "evaluation_type": "judge_scoring",
            })
            mlflow.log_metrics({
                "judge_total_without_memory": score_without.get("total", 0),
                "judge_total_with_memory": score_with.get("total", 0),
                "judge_score_delta": result.get("score_delta", 0),
                "judge_accuracy_without": score_without.get("accuracy", 0),
                "judge_accuracy_with": score_with.get("accuracy", 0),
                "judge_resolution_without": score_without.get("resolution", 0),
                "judge_resolution_with": score_with.get("resolution", 0),
            })
            run_ids.append(run.info.run_id)

    return run_ids
