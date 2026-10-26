"""Saved replay must reject mismatched responses and forged scores."""

import copy
import json
from pathlib import Path

import pytest

from frontend.reporting import read_report, summary_rows

REPORT = Path(__file__).resolve().parents[1] / "evaluation/results/controlled-comparison-20261024T135140Z-3c5e0acb.json"


def test_archived_report_replays_all_21_episodes() -> None:
    report = read_report(REPORT.read_bytes())
    assert len(report["episodes"]) == 21
    assert report["summary"]["stale/ordinary"]["correct_resolutions"] == 0
    assert len(summary_rows(report)) == 7


@pytest.mark.parametrize("mutation", ["prompt", "score", "duplicate"])
def test_report_tampering_is_rejected(mutation: str) -> None:
    report = json.loads(REPORT.read_bytes())
    if mutation == "prompt":
        report["episodes"][0]["prompt"] += " changed"
    elif mutation == "score":
        row = report["episodes"][0]
        row["score"]["correct_resolution"] = not row["score"]["correct_resolution"]
    else:
        report["episodes"].append(copy.deepcopy(report["episodes"][0]))
    with pytest.raises(ValueError):
        read_report(json.dumps(report).encode())
