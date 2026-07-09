"""Git side effects for files mentioned in a prompt."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Sibling-module import (the hooks dir can't be imported as a real package
# because parent dirs contain non-ASCII / hyphenated names).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib import base_ai_commit_subject  # noqa: E402

from .mentions import extract_candidate_paths


def is_tracked(relpath: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relpath],
        capture_output=True,
    )
    return result.returncode == 0


def handle_referenced_files(prompt: str, subproject: Path) -> None:
    """Best-effort: stage/commit files mentioned in `prompt` that exist on disk.

    Assumes cwd is already the git root (as `resolve_log_path` leaves it).
    """
    for candidate in extract_candidate_paths(prompt):
        abspath = (subproject / candidate).resolve()
        if not abspath.is_file():
            continue
        try:
            relpath = str(abspath.relative_to(Path.cwd()))
        except ValueError:
            continue  # outside the repo — skip

        if is_tracked(relpath):
            subprocess.run(["git", "add", "--", relpath], capture_output=True)
            continue

        add_cmd = ["git", "add"]
        if relpath.startswith("ai/"):
            add_cmd.append("-f")  # bypass .gitignore for AI artifacts
        subprocess.run([*add_cmd, "--", relpath], capture_output=True)
        subprocess.run(
            ["git", "commit", "--no-verify", "--only", relpath,
             "-m", base_ai_commit_subject("ai: referenced file for task added.")],
            capture_output=True,
        )
