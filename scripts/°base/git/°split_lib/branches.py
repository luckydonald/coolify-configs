"""Branch naming/classification for the clean/unclean/history split.

See ai/°base/todo.md for the full design; this module only implements the
naming scheme, not the sync/rebase machinery.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

UNCLEAN_RE = re.compile(r"^ai/UNCLEAN/(.+)$")
HISTORY_RE = re.compile(r"^ai/history/(.+)$")

FORK_POINT_REF_TEMPLATE = "refs/base-split/history-master-fork-point/{branch}"


def history_fork_point_ref(base_branch: str) -> str:
    return FORK_POINT_REF_TEMPLATE.format(branch=base_branch)


class BranchFormat(str, Enum):
    CLEAN = "clean"
    UNCLEAN = "unclean"
    HISTORY = "history"


@dataclass(frozen=True)
class BranchClassification:
    ref: str
    format: BranchFormat
    base_name: str
    is_history_master: bool


def strip_refs_heads(ref: str) -> str:
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


def classify_branch(ref: str, *, main_branch: str = "master") -> BranchClassification:
    """Classify a branch name/ref as clean, unclean, or history."""
    name = strip_refs_heads(ref)

    match = UNCLEAN_RE.match(name)
    if match:
        base_name = match.group(1)
        return BranchClassification(ref=name, format=BranchFormat.UNCLEAN, base_name=base_name, is_history_master=False)

    match = HISTORY_RE.match(name)
    if match:
        base_name = match.group(1)
        return BranchClassification(
            ref=name,
            format=BranchFormat.HISTORY,
            base_name=base_name,
            is_history_master=base_name == main_branch,
        )

    return BranchClassification(ref=name, format=BranchFormat.CLEAN, base_name=name, is_history_master=False)


def unclean_name(base_branch: str) -> str:
    return f"ai/UNCLEAN/{base_branch}"


def history_name(base_branch: str) -> str:
    return f"ai/history/{base_branch}"


def base_name_from_unclean(ref: str) -> str | None:
    match = UNCLEAN_RE.match(strip_refs_heads(ref))
    return match.group(1) if match else None


def base_name_from_history(ref: str) -> str | None:
    match = HISTORY_RE.match(strip_refs_heads(ref))
    return match.group(1) if match else None


def detect_main_branch(repo_root: Path) -> str:
    """Best-effort detection of the repo's main branch name."""
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        origin_head = result.stdout.strip()
        if origin_head.startswith("origin/"):
            return origin_head[len("origin/"):]
    except subprocess.CalledProcessError:
        pass

    for candidate in ("main", "master", "mane"):
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"],
            cwd=repo_root,
            capture_output=True,
        )
        if result.returncode == 0:
            return candidate

    return "master"
