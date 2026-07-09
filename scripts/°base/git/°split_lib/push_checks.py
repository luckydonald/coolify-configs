"""Pure push-policy logic (no subprocess calls) for the clean/unclean/history split.

See ai/°base/todo.md:155-163 for the original spec, and the approved plan for
the confirmed per-format content-policy matrix and the origin-only name policy.
"""

from __future__ import annotations

from dataclasses import dataclass

from .branches import BranchClassification, BranchFormat
from .classify import CommitClassification

ORIGIN_REMOTE_NAME = "origin"


@dataclass(frozen=True)
class RefUpdate:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def is_zero_sha(sha: str) -> bool:
    return len(sha) in (40, 64) and set(sha) == {"0"}


def check_content_policy(branch: BranchClassification, commits: list[CommitClassification]) -> list[str]:
    """Return a violation message per offending commit, empty if all allowed."""
    violations: list[str] = []

    if branch.format is BranchFormat.CLEAN:
        for commit in commits:
            if commit.is_ai_tainted_commit:
                violations.append(
                    f"commit {commit.sha[:12]} ({commit.subject!r}) contains AI/base content "
                    f"but branch {branch.ref!r} is clean-format; only unclean/history branches may."
                )
    elif branch.format is BranchFormat.HISTORY:
        for commit in commits:
            if commit.is_code_containing_commit:
                violations.append(
                    f"commit {commit.sha[:12]} ({commit.subject!r}) contains code content "
                    f"but branch {branch.ref!r} is history-format; only unclean/clean branches may."
                )
    # BranchFormat.UNCLEAN: no content restriction.

    return violations


def check_name_policy(branch: BranchClassification, remote_name: str) -> str | None:
    if remote_name != ORIGIN_REMOTE_NAME:
        return None
    if branch.format in (BranchFormat.UNCLEAN, BranchFormat.HISTORY):
        return (
            f"branch {branch.ref!r} is {branch.format.value}-format and must not be pushed "
            f"to the {ORIGIN_REMOTE_NAME!r} remote."
        )
    return None


def evaluate_ref_update(
    ref_update: RefUpdate,
    branch: BranchClassification,
    remote_name: str,
    commits: list[CommitClassification],
) -> list[str]:
    if is_zero_sha(ref_update.local_sha):
        return []  # deletion: exempt from both checks

    violations: list[str] = []
    name_violation = check_name_policy(branch, remote_name)
    if name_violation:
        violations.append(name_violation)
    violations.extend(check_content_policy(branch, commits))
    return violations
