"""Guard billable live proposals on hosted demos."""

from secrets import compare_digest


def live_access_allowed(mode: str, expected: str, supplied: str) -> bool:
    if mode == "local":
        return True
    return mode == "hosted" and bool(expected) and compare_digest(expected.encode(), supplied.encode())
