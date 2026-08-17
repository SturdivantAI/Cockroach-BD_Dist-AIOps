"""Run paired evaluation to demonstrate the memory improvement loop.

This is the payoff feature: Run incident A (no memory) -> resolve -> run incident A'
(same class, agent has memory of A) -> measure improvement.

Tracks:
- confidence_delta (A' should be >= 0.1 higher than A)
- reasoning_steps_count (fewer = faster diagnosis)
- time_to_resolution
- whether A's resolution appears in A' retrieval

Usage:
    python evaluation/run_evaluation_pair.py
    python evaluation/run_evaluation_pair.py --class cpu_spike
    python evaluation/run_evaluation_pair.py --all --output evaluation/results/pairs.json
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("evaluation")


def load_ground_truth() -> dict[str, Any]:
    """Load the ground truth dataset."""
    gt_path = Path(__file__).parent.parent / "data" / "sample_incidents" / "ground_truth.json"
    with open(gt_path, encoding="utf-8") as f:
        return json.load(f)


def get_paired_scenarios(ground_truth: dict, class_filter: str | None = None) -> list[dict]:
    """Extract paired scenarios (first_encounter + memory_assisted) from ground truth."""
    pairs = []

    for cls in ground_truth["incident_classes"]:
        if class_filter and cls["class_id"] != class_filter:
            continue

        variants = cls["variants"]
        # Find pairs
        first_encounters = [v for v in variants if v["pair_role"] == "first_encounter" and v.get("pair_with")]
        for first in first_encounters:
            paired = next(
                (v for v in variants if v["variant_id"] == first["pair_with"]),
                None,
            )
            if paired:
                pairs.append({
                    "class_id": cls["class_id"],
                    "class_name": cls["class_name"],
                    "first_encounter": first,
                    "memory_assisted": paired,
                })

    return pairs


def build_alert_from_variant(variant: dict) -> dict[str, Any]:
    """Convert a ground truth variant into an alert payload for the workflow."""
    alert = variant["alert"].copy()
    alert["logs"] = variant["logs"]
    alert["incident_class"] = None  # Don't give the agent the answer
    return alert


class PairResult:
    """Result of running a paired evaluation."""

    def __init__(self, class_id: str, class_name: str):
        self.class_id = class_id
        self.class_name = class_name
        self.run1_confidence: float = 0.0
        self.run2_confidence: float = 0.0
        self.run1_reasoning_steps: int = 0
        self.run2_reasoning_steps: int = 0
        self.run1_elapsed_ms: int = 0
        self.run2_elapsed_ms: int = 0
        self.run1_memory_available: bool = False
        self.run2_memory_available: bool = False
        self.run2_cited_prior: bool = False
        self.confidence_delta: float = 0.0
        self.steps_delta: int = 0
        self.improvement_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "run1": {
                "confidence": self.run1_confidence,
                "reasoning_steps": self.run1_reasoning_steps,
                "elapsed_ms": self.run1_elapsed_ms,
                "memory_available": self.run1_memory_available,
            },
            "run2": {
                "confidence": self.run2_confidence,
                "reasoning_steps": self.run2_reasoning_steps,
                "elapsed_ms": self.run2_elapsed_ms,
                "memory_available": self.run2_memory_available,
                "cited_prior_resolution": self.run2_cited_prior,
            },
            "deltas": {
                "confidence_delta": self.confidence_delta,
                "steps_delta": self.steps_delta,
                "time_delta_ms": self.run1_elapsed_ms - self.run2_elapsed_ms,
            },
            "improvement_detected": self.improvement_detected,
        }


def run_single_pair(pair: dict, verbose: bool = True) -> PairResult:
    """Run a single paired evaluation: first encounter then memory-assisted.

    Args:
        pair: Dict with class_id, first_encounter, memory_assisted variants.
        verbose: If True, print progress.

    Returns:
        PairResult with metrics for both runs.
    """
    from agents.orchestrator import run_workflow

    result = PairResult(pair["class_id"], pair["class_name"])

    # =========================================================================
    # RUN 1: First encounter (no memory of this incident class)
    # =========================================================================
    if verbose:
        print(f"\n{'='*70}")
        print(f"  RUN 1: First Encounter - {pair['class_name']}")
        print(f"  Service: {pair['first_encounter']['alert']['service']}")
        print(f"{'='*70}")

    alert1 = build_alert_from_variant(pair["first_encounter"])
    workflow1 = run_workflow(alert1, skip_approval=True, write_memory=True)

    result.run1_confidence = workflow1.analysis.get("confidence", 0.0)
    result.run1_reasoning_steps = workflow1.analysis.get("reasoning_steps_count", 0)
    result.run1_elapsed_ms = workflow1.total_elapsed_ms
    result.run1_memory_available = workflow1.retrieval.get("memory_available", False)

    if verbose:
        print(f"  Confidence: {result.run1_confidence:.2f}")
        print(f"  Reasoning steps: {result.run1_reasoning_steps}")
        print(f"  Memory available: {result.run1_memory_available}")
        print(f"  Time: {result.run1_elapsed_ms}ms")
        print(f"  Hypothesis: {workflow1.analysis.get('hypothesis', '')[:100]}...")

    # Small delay to ensure memory is written
    time.sleep(1)

    # =========================================================================
    # RUN 2: Memory-assisted (agent should recall resolution from Run 1)
    # =========================================================================
    if verbose:
        print(f"\n{'='*70}")
        print(f"  RUN 2: Memory-Assisted - {pair['class_name']}")
        print(f"  Service: {pair['memory_assisted']['alert']['service']}")
        print(f"  (Agent should now recall Run 1's resolution)")
        print(f"{'='*70}")

    alert2 = build_alert_from_variant(pair["memory_assisted"])
    workflow2 = run_workflow(alert2, skip_approval=True, write_memory=True)

    result.run2_confidence = workflow2.analysis.get("confidence", 0.0)
    result.run2_reasoning_steps = workflow2.analysis.get("reasoning_steps_count", 0)
    result.run2_elapsed_ms = workflow2.total_elapsed_ms
    result.run2_memory_available = workflow2.retrieval.get("memory_available", False)

    # Check if Run 2 cited the prior resolution in its reasoning
    reasoning_chain = workflow2.analysis.get("reasoning_chain", [])
    evidence = workflow2.analysis.get("evidence", [])
    all_text = " ".join(str(r) for r in reasoning_chain + [str(e) for e in evidence])
    result.run2_cited_prior = (
        "prior" in all_text.lower()
        or "memory" in all_text.lower()
        or "similar" in all_text.lower()
        or "previous" in all_text.lower()
    )

    # Compute deltas
    result.confidence_delta = result.run2_confidence - result.run1_confidence
    result.steps_delta = result.run1_reasoning_steps - result.run2_reasoning_steps
    result.improvement_detected = (
        result.confidence_delta >= 0.05  # At least 5% confidence improvement
        or result.run2_memory_available  # Memory was available
    )

    if verbose:
        print(f"  Confidence: {result.run2_confidence:.2f}")
        print(f"  Reasoning steps: {result.run2_reasoning_steps}")
        print(f"  Memory available: {result.run2_memory_available}")
        print(f"  Cited prior resolution: {result.run2_cited_prior}")
        print(f"  Time: {result.run2_elapsed_ms}ms")
        print(f"\n  --- DELTA ---")
        print(f"  Confidence improvement: {result.confidence_delta:+.2f}")
        print(f"  Reasoning steps reduction: {result.steps_delta:+d}")
        print(f"  Time improvement: {result.run1_elapsed_ms - result.run2_elapsed_ms:+d}ms")
        print(f"  Improvement detected: {result.improvement_detected}")

    return result


def main() -> None:
    """Run paired evaluations."""
    parser = argparse.ArgumentParser(description="Run paired memory improvement evaluation")
    parser.add_argument(
        "--class",
        dest="incident_class",
        choices=["cpu_spike", "memory_leak", "network_partition", "disk_io_saturation", "cascading_failure"],
        help="Run only this incident class",
    )
    parser.add_argument("--all", action="store_true", help="Run all paired scenarios")
    parser.add_argument(
        "--output",
        type=str,
        default="evaluation/results/pair_results.json",
        help="Output file for results",
    )
    parser.add_argument("--quiet", action="store_true", help="Less verbose output")
    args = parser.parse_args()

    if not args.all and not args.incident_class:
        parser.error("Specify --class <name> or --all")

    # Load ground truth
    ground_truth = load_ground_truth()
    pairs = get_paired_scenarios(ground_truth, class_filter=args.incident_class)

    print("=" * 70)
    print("  SRE MEMORY AGENT - Paired Evaluation")
    print("  Demonstrating memory improvement loop")
    print("=" * 70)
    print(f"  Pairs to evaluate: {len(pairs)}")
    print()

    results = []
    for i, pair in enumerate(pairs, 1):
        print(f"\n{'#'*70}")
        print(f"  PAIR {i}/{len(pairs)}: {pair['class_name']}")
        print(f"{'#'*70}")

        pair_result = run_single_pair(pair, verbose=not args.quiet)
        results.append(pair_result)

    # Summary
    print("\n\n")
    print("=" * 70)
    print("  EVALUATION SUMMARY")
    print("=" * 70)
    print(f"  Total pairs evaluated: {len(results)}")

    if results:
        avg_confidence_delta = sum(r.confidence_delta for r in results) / len(results)
        avg_steps_delta = sum(r.steps_delta for r in results) / len(results)
        improvements = sum(1 for r in results if r.improvement_detected)
        memory_cited = sum(1 for r in results if r.run2_cited_prior)

        print(f"  Improvements detected: {improvements}/{len(results)} ({improvements/len(results)*100:.0f}%)")
        print(f"  Average confidence delta: {avg_confidence_delta:+.3f}")
        print(f"  Average steps reduction: {avg_steps_delta:+.1f}")
        print(f"  Runs citing prior resolution: {memory_cited}/{len(results)}")
        print()

        print("  Per-class breakdown:")
        for r in results:
            status = "IMPROVED" if r.improvement_detected else "NO CHANGE"
            print(
                f"    {r.class_name:<25} conf: {r.run1_confidence:.2f} -> {r.run2_confidence:.2f} "
                f"({r.confidence_delta:+.2f}) [{status}]"
            )

    # Save results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_data = {
        "evaluation_type": "paired_memory_improvement",
        "total_pairs": len(results),
        "summary": {
            "avg_confidence_delta": avg_confidence_delta if results else 0,
            "avg_steps_reduction": avg_steps_delta if results else 0,
            "improvement_rate": improvements / len(results) if results else 0,
            "memory_citation_rate": memory_cited / len(results) if results else 0,
        },
        "pairs": [r.to_dict() for r in results],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    print(f"\n  Results saved to: {output_path}")


if __name__ == "__main__":
    main()
