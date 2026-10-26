"""Pin the installed runtime dependency closure, excluding optional legacy tools."""

import importlib.metadata as metadata
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text())
    pending = [Requirement(item) for item in project["project"]["dependencies"]]
    visited: set[tuple[str, tuple[str, ...]]] = set()
    locked = {}
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        extras = tuple(sorted(requirement.extras))
        identity = (name, extras)
        if identity in visited:
            continue
        visited.add(identity)
        distribution = metadata.distribution(name)
        if not requirement.specifier.contains(distribution.version):
            raise RuntimeError(f"Installed {name} does not satisfy {requirement.specifier}")
        locked[name] = distribution.version
        for raw in distribution.requires or []:
            child = Requirement(raw)
            if child.marker is None or any(child.marker.evaluate({"extra": extra}) for extra in ("", *extras)):
                pending.append(child)
    lines = ["# Pinned from the tested Python 3.12 Linux environment.",
             "# Regenerate deliberately with python tools/lock_environment.py."]
    lines.extend(f"{name}=={version}" for name, version in sorted(locked.items()))
    target = root / "requirements.lock"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Pinned {len(locked)} runtime distributions in {target.name}")


if __name__ == "__main__":
    main()
