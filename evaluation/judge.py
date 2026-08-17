"""LLM-as-Judge evaluation layer.

Scores the agent's diagnosis against ground truth across 4 dimensions (1-5):
1. Root cause accuracy - does hypothesis match ground truth?
2. Resolution appropriateness - would this fix the issue?
3. Reasoning quality - is the chain-of-thought logical?
4. Evidence usage - did it cite relevant prior incidents?

Stores scores in agent_decisions.judge_score_* columns.

Usage:
    python evaluation/judge.py --incident-id <uuid>
    python evaluation/judge.py --all-pairs
    python evaluation/judge.py --results-file evaluation/results/pair_results.json
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("judge")


JUDGE_SYSTEM_PROMPT = """You are an expert evaluator of SRE incident diagnosis quality. \
You score an AI agent's diagnosis against known ground truth. Be rigorous but fair. \
You MUST respond with valid JSON only."""

JUDGE_PROMPT_TEMPLATE = """Evaluate this SRE agent's incident diagnosis against the known ground truth.

## Agent's Hypothesis
{agent_hypothesis}

## Agent's Proposed Resolution
{agent_resolution}

## Agent's Reasoning Chain
{agent_reasoning}

## Agent's Evidence
{agent_evidence}

## Ground Truth Root Cause
{gt_root_cause}

## Ground Truth Resolution
{gt_resolution}

## Was Memory Available?
{memory_status}

## Scoring Instructions
Score each dimension from 1 to 5:

1. **Root Cause Accuracy** (1-5): How well does the agent's hypothesis match the ground truth root cause?
   - 5: Exact match or equivalent technical explanation
   - 4: Correct category, minor details differ
   - 3: Partially correct, identifies the right area
   - 2: Vaguely related but misses key cause
   - 1: Completely wrong

2. **Resolution Appropriateness** (1-5): Would the proposed action fix the issue?
   - 5: Would fully resolve the issue, matches ground truth
   - 4: Would mostly resolve, minor gaps
   - 3: Would partially help but incomplete
   - 2: Might help tangentially
   - 1: Would not fix or would make things worse

3. **Reasoning Quality** (1-5): Is the chain-of-thought logical and well-structured?
   - 5: Clear, logical, step-by-step deduction
   - 4: Mostly logical with minor leaps
   - 3: Some logic but disorganized or missing steps
   - 2: Weak reasoning, mostly guessing
   - 1: Incoherent or no reasoning

4. **Evidence Usage** (1-5): Did the agent cite relevant evidence from logs and memory?
   - 5: Excellent use of logs + memory, all citations relevant
   - 4: Good evidence usage, mostly relevant
   - 3: Some evidence cited but could use more
   - 2: Minimal evidence, mostly unsupported claims
   - 1: No evidence cited

Respond with EXACTLY this JSON:
{{
  "accuracy": <int 1-5>,
  "resolution": <int 1-5>,
  "reasoning": <int 1-5>,
  "evidence": <int 1-5>,
  "total": <float: average of 4 scores>,
  "explanation": "<brief explanation of scoring rationale>"
}}"""


class JudgeScore:
    """Structured judge score for one evaluation."""

    def __init__(
        self,
        accuracy: int,
        resolution: int,
        reasoning: int,
        evidence: int,
        explanation: str = "",
    ):
        self.accuracy = accuracy
        self.resolution = resolution
        self.reasoning = reasoning
        self.evidence = evidence
        self.total = (accuracy + resolution + reasoning + evidence) / 4.0
        self.explanation = explanation

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": self.accuracy,
            "resolution": self.resolution,
            "reasoning": self.reasoning,
            "evidence": self.evidence,
            "total": round(self.total, 2),
            "explanation": self.explanation,
        }


def _parse_judge_response(response_text: str) -> dict[str, Any]:
    """Parse the judge's JSON response."""
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

    logger.warning("Failed to parse judge response")
    return {"accuracy": 1, "resolution": 1, "reasoning": 1, "evidence": 1, "total": 1.0, "explanation": "Parse error"}


def score_diagnosis(
    agent_hypothesis: str,
    agent_resolution: str,
    agent_reasoning: list[str],
    agent_evidence: list[dict[str, Any]],
    gt_root_cause: str,
    gt_resolution: str,
    memory_available: bool = False,
) -> JudgeScore:
    """Score an agent's diagnosis against ground truth using LLM-as-Judge.

    Args:
        agent_hypothesis: The agent's root cause hypothesis.
        agent_resolution: The agent's proposed resolution action.
        agent_reasoning: The agent's reasoning chain steps.
        agent_evidence: The agent's cited evidence.
        gt_root_cause: Ground truth root cause.
        gt_resolution: Ground truth resolution.
        memory_available: Whether memory was available for this run.

    Returns:
        JudgeScore with scores for each dimension.
    """
    from agents.bedrock_client import bedrock

    # Format reasoning and evidence for the prompt
    reasoning_text = "\n".join(f"  {i+1}. {step}" for i, step in enumerate(agent_reasoning))
    if not reasoning_text:
        reasoning_text = "  (no reasoning chain provided)"

    evidence_text = "\n".join(
        f"  - [{e.get('source', '?')}] {e.get('content', '')[:100]}"
        for e in agent_evidence
    )
    if not evidence_text:
        evidence_text = "  (no evidence cited)"

    memory_status = (
        "Yes - agent had access to prior incident resolutions from memory"
        if memory_available
        else "No - this was the agent's first encounter with this type of incident"
    )

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        agent_hypothesis=agent_hypothesis or "(no hypothesis)",
        agent_resolution=agent_resolution or "(no resolution proposed)",
        agent_reasoning=reasoning_text,
        agent_evidence=evidence_text,
        gt_root_cause=gt_root_cause,
        gt_resolution=gt_resolution,
        memory_status=memory_status,
    )

    response_text = bedrock.invoke_claude(
        prompt=prompt,
        system=JUDGE_SYSTEM_PROMPT,
        max_tokens=1024,
        temperature=0.1,  # Low temp for consistent scoring
    )

    parsed = _parse_judge_response(response_text)

    return JudgeScore(
        accuracy=max(1, min(5, int(parsed.get("accuracy", 1)))),
        resolution=max(1, min(5, int(parsed.get("resolution", 1)))),
        reasoning=max(1, min(5, int(parsed.get("reasoning", 1)))),
        evidence=max(1, min(5, int(parsed.get("evidence", 1)))),
        explanation=parsed.get("explanation", ""),
    )


def update_decision_scores(decision_id: str, score: JudgeScore) -> None:
    """Write judge scores to agent_decisions table."""
    from agents.db import execute_command

    try:
        execute_command(
            """
            UPDATE agent_decisions
            SET judge_score_accuracy = %s,
                judge_score_resolution = %s,
                judge_score_reasoning = %s,
                judge_score_evidence = %s,
                judge_score_total = %s
            WHERE id = %s
            """,
            (
                float(score.accuracy),
                float(score.resolution),
                float(score.reasoning),
                float(score.evidence),
                float(score.total),
                decision_id,
            ),
        )
    except Exception as e:
        logger.warning("Failed to update decision scores: %s", e)


def evaluate_workflow_result(
    workflow_result: dict[str, Any],
    ground_truth: dict[str, Any],
) -> JudgeScore:
    """Evaluate a complete workflow result against ground truth.

    Args:
        workflow_result: Output from run_workflow or paired evaluation.
        ground_truth: Ground truth dict with root_cause and resolution.

    Returns:
        JudgeScore.
    """
    analysis = workflow_result.get("analysis", {})
    decision = workflow_result.get("decision", {})
    retrieval = workflow_result.get("retrieval", {})

    return score_diagnosis(
        agent_hypothesis=analysis.get("hypothesis", ""),
        agent_resolution=decision.get("action", analysis.get("suggested_resolution", "")),
        agent_reasoning=analysis.get("reasoning_chain", []),
        agent_evidence=analysis.get("evidence", []),
        gt_root_cause=ground_truth.get("root_cause", ""),
        gt_resolution=ground_truth.get("resolution", ""),
        memory_available=retrieval.get("memory_available", False),
    )


def run_judge_on_pairs(pairs_file: str | None = None) -> list[dict[str, Any]]:
    """Run the judge on all paired evaluation results.

    Runs actual workflows for each pair and scores the real agent output.
    """
    gt_path = Path(__file__).parent.parent / "data" / "sample_incidents" / "ground_truth.json"
    with open(gt_path, encoding="utf-8") as f:
        ground_truth = json.load(f)

    from evaluation.run_evaluation_pair import get_paired_scenarios, build_alert_from_variant
    from agents.orchestrator import run_workflow

    pairs = get_paired_scenarios(ground_truth)
    results = []

    for pair in pairs:
        class_id = pair["class_id"]
        gt_first = pair["first_encounter"]["ground_truth"]
        gt_memory = pair["memory_assisted"]["ground_truth"]

        print(f"\n  Judging pair: {pair['class_name']}")

        # Run 1: First encounter (no prior memory of this class)
        print(f"    Running first encounter workflow...")
        alert1 = build_alert_from_variant(pair["first_encounter"])
        wf1 = run_workflow(alert1, skip_approval=True, write_memory=True)

        print(f"    Scoring first encounter...")
        score1 = score_diagnosis(
            agent_hypothesis=wf1.analysis.get("hypothesis", ""),
            agent_resolution=wf1.decision.get("action", ""),
            agent_reasoning=wf1.analysis.get("reasoning_chain", []),
            agent_evidence=wf1.analysis.get("evidence", []),
            gt_root_cause=gt_first["root_cause"],
            gt_resolution=gt_first["resolution"],
            memory_available=wf1.retrieval.get("memory_available", False),
        )

        # Run 2: Memory-assisted (agent should recall Run 1's resolution)
        import time
        time.sleep(1)
        print(f"    Running memory-assisted workflow...")
        alert2 = build_alert_from_variant(pair["memory_assisted"])
        wf2 = run_workflow(alert2, skip_approval=True, write_memory=True)

        print(f"    Scoring memory-assisted...")
        score2 = score_diagnosis(
            agent_hypothesis=wf2.analysis.get("hypothesis", ""),
            agent_resolution=wf2.decision.get("action", ""),
            agent_reasoning=wf2.analysis.get("reasoning_chain", []),
            agent_evidence=wf2.analysis.get("evidence", []),
            gt_root_cause=gt_memory["root_cause"],
            gt_resolution=gt_memory["resolution"],
            memory_available=wf2.retrieval.get("memory_available", False),
        )

        results.append({
            "class_id": class_id,
            "class_name": pair["class_name"],
            "score_without_memory": score1.to_dict(),
            "score_with_memory": score2.to_dict(),
            "score_delta": round(score2.total - score1.total, 2),
        })

        print(f"    Without memory: {score1.total:.2f} | With memory: {score2.total:.2f} | Delta: {score2.total - score1.total:+.2f}")

    return results


def main() -> None:
    """Run LLM-as-Judge evaluation."""
    parser = argparse.ArgumentParser(description="LLM-as-Judge evaluation scoring")
    parser.add_argument("--incident-id", type=str, help="Score a specific incident decision")
    parser.add_argument("--all-pairs", action="store_true", help="Run judge on all paired scenarios")
    parser.add_argument("--results-file", type=str, help="Load previous pair results to score")
    parser.add_argument(
        "--output",
        type=str,
        default="evaluation/results/judge_scores.json",
        help="Output file for judge scores",
    )
    args = parser.parse_args()

    if not any([args.incident_id, args.all_pairs, args.results_file]):
        parser.error("Specify --incident-id, --all-pairs, or --results-file")

    print("=" * 70)
    print("  SRE MEMORY AGENT - LLM-as-Judge Evaluation")
    print("=" * 70)

    if args.all_pairs:
        results = run_judge_on_pairs()

        # Summary
        if results:
            avg_delta = sum(r["score_delta"] for r in results) / len(results)
            avg_without = sum(r["score_without_memory"]["total"] for r in results) / len(results)
            avg_with = sum(r["score_with_memory"]["total"] for r in results) / len(results)

            print(f"\n{'='*70}")
            print("  JUDGE SCORING SUMMARY")
            print(f"{'='*70}")
            print(f"  Pairs scored: {len(results)}")
            print(f"  Avg score WITHOUT memory: {avg_without:.2f}/5.0")
            print(f"  Avg score WITH memory:    {avg_with:.2f}/5.0")
            print(f"  Avg score delta:          {avg_delta:+.2f}")
            print()
            positive = sum(1 for r in results if r["score_delta"] > 0)
            print(f"  Positive improvement: {positive}/{len(results)} ({positive/len(results)*100:.0f}%)")

        # Save results
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        output_data = {
            "evaluation_type": "llm_as_judge",
            "scoring_dimensions": ["accuracy", "resolution", "reasoning", "evidence"],
            "scoring_scale": "1-5",
            "results": results,
            "summary": {
                "avg_without_memory": avg_without if results else 0,
                "avg_with_memory": avg_with if results else 0,
                "avg_delta": avg_delta if results else 0,
                "positive_improvement_rate": positive / len(results) if results else 0,
            },
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        print(f"\n  Results saved to: {output_path}")

    elif args.incident_id:
        # Score a specific incident from the database
        from agents.db import execute_query

        rows = execute_query(
            "SELECT * FROM agent_decisions WHERE incident_id = %s ORDER BY created_at DESC LIMIT 1",
            (args.incident_id,),
        )
        if not rows:
            print(f"  No decision found for incident {args.incident_id}")
            return

        decision = rows[0]
        print(f"  Scoring decision for incident: {args.incident_id}")
        print(f"  Hypothesis: {decision['hypothesis'][:80]}...")
        print(f"  Confidence: {decision['confidence']:.2f}")

        # For single incident scoring, we need ground truth from user
        print("  NOTE: Single incident scoring requires ground truth input.")
        print("  Use --all-pairs for automated scoring against ground truth dataset.")


if __name__ == "__main__":
    main()
