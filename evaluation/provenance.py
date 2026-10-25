"""Capture source identity without collecting environment files or credentials."""

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def capture_provenance(root: Path) -> dict[str, Any]:
    """Record Git state and hashes of the source actually available for this run."""
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=root, check=True, capture_output=True,
            text=True, timeout=10,
        ).stdout.strip()

    hashes = {}
    for directory in ("agents", "evaluation", "data", "tests"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in (".py", ".sql"):
                hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    project_file = root / "pyproject.toml"
    if project_file.is_file():
        hashes["pyproject.toml"] = hashlib.sha256(project_file.read_bytes()).hexdigest()
    status = git("status", "--porcelain")
    return {
        "git_commit": git("rev-parse", "HEAD"),
        "git_branch": git("branch", "--show-current"),
        "working_tree_dirty": bool(status),
        "git_status": status,
        "source_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        "source_files": hashes,
        "scope": "Python and SQL under agents, evaluation, data, tests; pyproject.toml",
    }
