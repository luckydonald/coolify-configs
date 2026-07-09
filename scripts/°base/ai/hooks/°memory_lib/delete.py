"""Shared logic for originating a memory-file deletion commit.

The repo copy is the durable, authoritative one (see
`ai/°base/plans/007_prevent-accidental-memory-deletion.md`) -- deleting a
memory is only ever done here, explicitly, never inferred from the external
Claude-side source file merely being absent.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Sibling-module import (the hooks dir can't be imported as a real package
# because parent dirs contain non-ASCII / hyphenated names).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib import base_ai_commit_subject  # noqa: E402


def is_tracked(relpath: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relpath],
        capture_output=True,
    )
    return result.returncode == 0


def unlink_path(path: Path) -> None:
    if path.is_symlink() or path.exists():
        path.unlink()


def delete_memory(name: str, *, src_dir: Path, dst_dir: Path, dst_dir_rel: str) -> bool:
    """Unlink both the repo mirror and external source copies of memory
    `name`, and commit the repo-side deletion with the required
    `Deleted Memory: <name>` marker.

    Returns True if a commit was made; False if the repo copy wasn't tracked
    (nothing to do).
    """
    dst = dst_dir / name
    dst_rel = f"{dst_dir_rel}/{name}"
    if not is_tracked(dst_rel):
        return False

    unlink_path(dst)
    unlink_path(src_dir / name)

    subprocess.run(["git", "add", "--", dst_rel], check=True)
    subject = base_ai_commit_subject(f"ai: delete memory {Path(name).stem}")
    marker = f"Deleted Memory: {name}"
    result = subprocess.run(
        ["git", "commit", "--only", dst_rel, "-m", subject, "-m", marker],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
