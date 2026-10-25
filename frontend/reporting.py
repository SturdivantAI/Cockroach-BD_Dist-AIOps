"""Read experiment reports without trusting their precomputed score summaries."""

import hashlib
import json
from typing import Any

from evaluation.run_controlled_comparison import parse_proposal, score_action, summarize


def read_report(payload: bytes) -> dict[str, Any]:
    if len(payload) > 5_000_000:
        raise ValueError("Report exceeds the 5 MB replay limit")
    report = json.loads(payload)
    if not isinstance(report, dict) or not isinstance(report.get("episodes"), list):
        raise ValueError("Expected an experiment report with episodes")
    if not report["episodes"] or len(report["episodes"]) > 1000:
        raise ValueError("Expected between 1 and 1000 episodes")
    seen = set()
    for episode in report["episodes"]:
        if not isinstance(episode, dict):
            raise ValueError("Invalid episode")
        if episode.get("condition") not in ("clean", "stale", "poisoned", "none"):
            raise ValueError("Unknown memory condition")
        if episode.get("mode") not in ("ordinary", "validated", "shared_control"):
            raise ValueError("Unknown comparison mode")
        identity = (episode["condition"], episode["mode"], episode.get("repeat"))
        if identity in seen:
            raise ValueError("Duplicate episode identity")
        seen.add(identity)
        prompt = episode.get("prompt")
        if not isinstance(prompt, str) or hashlib.sha256(prompt.encode()).hexdigest() != episode.get("prompt_sha256"):
            raise ValueError("Prompt fingerprint does not match")
        if episode.get("status") == "scored":
            proposal = parse_proposal(episode["raw_response"])
            if proposal.model_dump() != episode.get("proposal"):
                raise ValueError("Saved proposal does not match model response")
            computed = score_action(proposal.action.model_dump())
            for key in ("correct_resolution", "unsafe_recommendation"):
                if episode.get("score", {}).get(key) is not computed[key]:
                    raise ValueError("Saved action score does not match simulator rules")
        elif episode.get("status") != "error":
            raise ValueError("Unknown episode status")
    report["summary"] = summarize(report["episodes"])
    return report


def summary_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "Memory / mode": name,
        "Correct / attempts": f"{row['correct_resolutions']}/{row['attempts']}",
        "Unsafe recommendations": row["unsafe_recommendations"],
        "Errors": row["errors"],
        "Validation (ms)": round(row["mean_validation_ms"], 1),
        "Request + parsing (ms)": round(row["mean_request_and_parse_ms"], 1),
        "Reported tokens": row["reported_total_tokens"],
    } for name, row in report["summary"].items()]
