"""Check publishable Git files for known local secrets without printing values."""

import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, check=True, capture_output=True,
    )
    names = result.stdout.decode().split("\0")
    local = dotenv_values(root / ".env")
    keys = ("NEBIUS_API_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "DATABRICKS_TOKEN", "COCKROACHDB_PASSWORD", "DEMO_ACCESS_CODE")
    values = [local[key].encode() for key in keys if local.get(key) and len(local[key]) >= 12
              and not local[key].startswith(("your-", "your_"))]
    patterns = (
        re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    flagged = []
    for name in names:
        if not name:
            continue
        path = root / name
        if path.is_symlink() or not path.is_file():
            continue
        if path.name.startswith(".env") and path.name not in (".env.example", ".env.deployment.example"):
            flagged.append(name)
            continue
        content = path.read_bytes()
        if any(value in content for value in values) or any(pattern.search(content) for pattern in patterns):
            flagged.append(name)
    if flagged:
        raise SystemExit("Potential credentials found in: " + ", ".join(flagged))
    print("PASS: publishable files contain no detected local secrets, AWS access keys, or private keys")
    print("Scope: known-value and pattern checks; this is not an exhaustive credential detector")


if __name__ == "__main__":
    main()
