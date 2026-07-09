"""Subprocess glue for the split-lib CLI. Kept separate from the pure policy/
orchestration modules (push_checks.py, sync_splits.py, ...) so those stay
unit-testable without touching git.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def repo_root(cwd: Path | None = None) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def rev_exists(sha: str, cwd: Path) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=cwd,
        capture_output=True,
    )
    return result.returncode == 0


def commits_new_to_remote(local_sha: str, remote_sha: str, remote_name: str, cwd: Path) -> list[str]:
    """Commits reachable from local_sha not already reachable from remote_sha.

    Oldest first. Handles branch deletion (empty result) and brand-new
    branches (remote_sha unknown locally, or all-zero) via the same
    ``--not --remotes=`` fallback pre-commit itself uses.
    """
    is_deletion = set(local_sha) == {"0"}
    if is_deletion:
        return []

    is_new_remote_ref = set(remote_sha) == {"0"} or not rev_exists(remote_sha, cwd)
    if is_new_remote_ref:
        args = ["git", "rev-list", "--reverse", local_sha, "--not", f"--remotes={remote_name}"]
    else:
        args = ["git", "rev-list", "--reverse", f"{remote_sha}..{local_sha}"]

    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)
    return [line for line in result.stdout.splitlines() if line]


def changed_paths_for_commit(sha: str, cwd: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def subject_for_commit(sha: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s", sha],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def commit_message(sha: str, cwd: Path) -> str:
    """Full commit message body (subject + blank line + body), as %B renders it."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%B", sha],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def rev_parse(ref: str, cwd: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def rev_list_reverse(range_expr: str, cwd: Path) -> list[str]:
    """`git rev-list --reverse <range_expr>`, oldest first."""
    result = subprocess.run(
        ["git", "rev-list", "--reverse", range_expr],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_ancestor(ancestor_sha: str, descendant_sha: str, cwd: Path) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
        cwd=cwd,
        capture_output=True,
    )
    return result.returncode == 0


def merge_base(sha_a: str, sha_b: str, cwd: Path) -> str | None:
    result = subprocess.run(
        ["git", "merge-base", sha_a, sha_b],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _full_ref(ref: str) -> str:
    """`git update-ref` does not resolve short branch names the way most git
    commands do -- a bare name like "feature-x" creates a loose ref straight
    under .git/ instead of a real branch. Normalize anything that isn't
    already a fully-qualified ref (refs/heads/..., refs/base-split/..., ...)
    to refs/heads/<name>.
    """
    return ref if ref.startswith("refs/") else f"refs/heads/{ref}"


def create_branch(ref: str, at_sha: str, cwd: Path) -> None:
    subprocess.run(["git", "update-ref", _full_ref(ref), at_sha], cwd=cwd, check=True)


def move_ref(ref: str, new_sha: str, old_sha: str | None, cwd: Path) -> None:
    """Update a ref, optionally asserting its current value first (race guard)."""
    args = ["git", "update-ref", _full_ref(ref), new_sha]
    if old_sha is not None:
        args.append(old_sha)
    subprocess.run(args, cwd=cwd, check=True)


def tree_for_commit(sha: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", f"{sha}^{{tree}}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def ls_tree_entry(tree_or_commit: str, path: str, cwd: Path) -> tuple[str, str, str] | None:
    """Return (mode, type, blob_sha) for `path` inside a tree-ish, or None if absent."""
    result = subprocess.run(
        ["git", "ls-tree", tree_or_commit, "--", path],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    line = result.stdout.strip()
    if not line:
        return None
    meta, _, _entry_path = line.partition("\t")
    mode, obj_type, blob_sha = meta.split()
    return mode, obj_type, blob_sha


def commit_tree(
    tree_sha: str,
    parents: list[str],
    message: str,
    cwd: Path,
    *,
    author_name: str,
    author_email: str,
    author_date: str,
    committer_name: str,
    committer_email: str,
    committer_date: str,
) -> str:
    env = os.environ.copy()
    env.update(
        GIT_AUTHOR_NAME=author_name,
        GIT_AUTHOR_EMAIL=author_email,
        GIT_AUTHOR_DATE=author_date,
        GIT_COMMITTER_NAME=committer_name,
        GIT_COMMITTER_EMAIL=committer_email,
        GIT_COMMITTER_DATE=committer_date,
    )
    args = ["git", "commit-tree", tree_sha]
    for parent in parents:
        args += ["-p", parent]
    result = subprocess.run(
        args,
        cwd=cwd,
        input=message,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return result.stdout.strip()


def cherry_pick(sha: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run `git cherry-pick <sha>`. Caller inspects returncode for conflicts
    rather than `check=True`, since a conflict is an expected, handleable outcome.
    """
    return subprocess.run(
        ["git", "cherry-pick", sha],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def cherry_pick_continue(cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_EDITOR"] = "true"
    return subprocess.run(
        ["git", "cherry-pick", "--continue"],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def cherry_pick_abort(cwd: Path) -> None:
    subprocess.run(["git", "cherry-pick", "--abort"], cwd=cwd, capture_output=True)


def merge_no_commit(sha: str, cwd: Path) -> subprocess.CompletedProcess:
    """`git merge --no-commit --no-ff <sha>`. Caller inspects returncode for conflicts."""
    return subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", sha],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def merge_abort(cwd: Path) -> None:
    subprocess.run(["git", "merge", "--abort"], cwd=cwd, capture_output=True)


def checkout_branch(ref: str, cwd: Path) -> None:
    subprocess.run(["git", "checkout", ref], cwd=cwd, check=True, capture_output=True)


def _with_index_env(index_file: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(index_file)
    return env


def read_tree_into_index(tree_sha: str, index_file: Path, cwd: Path) -> None:
    subprocess.run(
        ["git", "read-tree", tree_sha],
        cwd=cwd,
        env=_with_index_env(index_file),
        check=True,
        capture_output=True,
    )


def update_index_add(index_file: Path, mode: str, blob_sha: str, path: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"{mode},{blob_sha},{path}"],
        cwd=cwd,
        env=_with_index_env(index_file),
        check=True,
        capture_output=True,
    )


def update_index_remove(index_file: Path, path: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "update-index", "--force-remove", path],
        cwd=cwd,
        env=_with_index_env(index_file),
        check=True,
        capture_output=True,
    )


def write_tree_from_index(index_file: Path, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "write-tree"],
        cwd=cwd,
        env=_with_index_env(index_file),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def show_path_at(sha: str, path: str, cwd: Path) -> bytes:
    """Raw blob content of `path` as it exists at commit `sha`."""
    result = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        cwd=cwd,
        capture_output=True,
        check=True,
    )
    return result.stdout
