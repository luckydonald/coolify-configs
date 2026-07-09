"""Shared real-git-repo test helpers for the split-lib test suite."""

from __future__ import annotations

import subprocess
from pathlib import Path

ZERO_SHA = "0" * 40


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def make_commit(cwd: Path, filename: str, message: str, content: str | None = None) -> str:
    path = cwd / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else message)
    git(["add", filename], cwd)
    git(["commit", "-m", message], cwd)
    return git(["rev-parse", "HEAD"], cwd)


def init_repo(cwd: Path, *, branch: str = "master") -> None:
    git(["init", "-b", branch], cwd)
    git(["config", "user.email", "test@example.com"], cwd)
    git(["config", "user.name", "Test"], cwd)
