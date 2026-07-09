"""Shared git-trailer read/write helpers, built on `git interpret-trailers`."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def read_trailers(message: str, cwd: Path) -> dict[str, list[str]]:
    """Parse trailers out of a full commit message body.

    Returns a dict of trailer key -> list of values (a key may repeat).
    """
    result = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        cwd=cwd,
        input=message,
        capture_output=True,
        text=True,
        check=True,
    )
    trailers: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        trailers.setdefault(key.strip(), []).append(value.strip())
    return trailers


def read_trailer_value(message: str, key: str, cwd: Path) -> str | None:
    """Convenience: return the first value for `key`, or None if absent."""
    values = read_trailers(message, cwd).get(key)
    return values[0] if values else None


def write_trailers(message: str, trailers: dict[str, str], cwd: Path) -> str:
    """Append the given trailers to a commit message, returning the new message text."""
    args = ["git", "interpret-trailers"]
    for key, value in trailers.items():
        args += ["--trailer", f"{key}: {value}"]

    with tempfile.NamedTemporaryFile("w", suffix=".msg", delete=False) as handle:
        handle.write(message)
        path = Path(handle.name)

    try:
        result = subprocess.run(
            [*args, str(path)],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    finally:
        path.unlink(missing_ok=True)
