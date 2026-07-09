"""Shared tree-splitting/merging plumbing used by both directions of
sync-splits: building a filtered tree (project an unclean commit's delta
through an AI-vs-code `keep` predicate) and, symmetrically, overlaying two
partial deltas back onto one tree for unclean reconstruction.

All operations use a scratch git index (`GIT_INDEX_FILE`) so the real
working tree/index is never touched.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import git_ops


@dataclass(frozen=True)
class PathChange:
    status: str  # "A", "M", "D", "R", "C"
    path: str  # new/current path for A/M/D; new path for R/C
    old_path: str | None = None  # old path for R/C, else None


def _first_parent(sha: str, cwd: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{sha}^"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def raw_diff_for_commit(sha: str, cwd: Path) -> list[PathChange]:
    """The commit's own delta vs. its first parent (or the empty tree, for a
    root commit), with rename detection (`-M`), decomposed into PathChange
    entries. Copies (`-C` not passed, so these shouldn't appear) are treated
    defensively like renames-without-removal if they ever do.
    """
    parent = _first_parent(sha, cwd)
    base = parent if parent is not None else git_ops.EMPTY_TREE_SHA

    result = subprocess.run(
        ["git", "diff-tree", "-M", "-r", "--no-commit-id", "--name-status", base, sha],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )

    changes: list[PathChange] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        status = fields[0]
        if status[0] in ("R", "C"):
            old_path, new_path = fields[1], fields[2]
            changes.append(PathChange(status=status[0], path=new_path, old_path=old_path))
        else:
            changes.append(PathChange(status=status, path=fields[1]))
    return changes


def build_filtered_tree(
    parent_target_tree: str,
    sha: str,
    cwd: Path,
    *,
    keep: Callable[[str], bool],
) -> str:
    """Return a new tree = `parent_target_tree` with the subset of `sha`'s
    own changes whose paths satisfy `keep` applied on top.

    A rename is decomposed into an independent delete-of-old-path and
    add-of-new-path, each filtered by `keep` separately -- this is what
    correctly handles a rename crossing the AI/code boundary.
    """
    changes = raw_diff_for_commit(sha, cwd)

    with tempfile.NamedTemporaryFile(suffix=".index", delete=False) as handle:
        index_file = Path(handle.name)

    try:
        git_ops.read_tree_into_index(parent_target_tree, index_file, cwd)

        for change in changes:
            if change.status in ("A", "M"):
                if not keep(change.path):
                    continue
                entry = git_ops.ls_tree_entry(sha, change.path, cwd)
                if entry is None:
                    continue  # shouldn't happen, but don't crash on a stale diff entry
                mode, _obj_type, blob_sha = entry
                git_ops.update_index_add(index_file, mode, blob_sha, change.path, cwd)
            elif change.status == "D":
                if not keep(change.path):
                    continue
                git_ops.update_index_remove(index_file, change.path, cwd)
            elif change.status in ("R", "C"):
                assert change.old_path is not None
                if keep(change.old_path) and change.status == "R":
                    git_ops.update_index_remove(index_file, change.old_path, cwd)
                if keep(change.path):
                    entry = git_ops.ls_tree_entry(sha, change.path, cwd)
                    if entry is not None:
                        mode, _obj_type, blob_sha = entry
                        git_ops.update_index_add(index_file, mode, blob_sha, change.path, cwd)

        return git_ops.write_tree_from_index(index_file, cwd)
    finally:
        index_file.unlink(missing_ok=True)


def apply_path_changes(
    base_tree: str,
    changes: list[PathChange],
    sha_for_blobs: str,
    cwd: Path,
) -> str:
    """Overlay `changes` (as produced by `raw_diff_for_commit`, e.g. for a
    reconstruction merge) onto `base_tree`, resolving added/modified blobs
    from `sha_for_blobs`'s tree. Renames decompose the same way as
    `build_filtered_tree`.
    """
    with tempfile.NamedTemporaryFile(suffix=".index", delete=False) as handle:
        index_file = Path(handle.name)

    try:
        git_ops.read_tree_into_index(base_tree, index_file, cwd)

        for change in changes:
            if change.status in ("A", "M"):
                entry = git_ops.ls_tree_entry(sha_for_blobs, change.path, cwd)
                if entry is None:
                    continue
                mode, _obj_type, blob_sha = entry
                git_ops.update_index_add(index_file, mode, blob_sha, change.path, cwd)
            elif change.status == "D":
                git_ops.update_index_remove(index_file, change.path, cwd)
            elif change.status in ("R", "C"):
                assert change.old_path is not None
                if change.status == "R":
                    git_ops.update_index_remove(index_file, change.old_path, cwd)
                entry = git_ops.ls_tree_entry(sha_for_blobs, change.path, cwd)
                if entry is not None:
                    mode, _obj_type, blob_sha = entry
                    git_ops.update_index_add(index_file, mode, blob_sha, change.path, cwd)

        return git_ops.write_tree_from_index(index_file, cwd)
    finally:
        index_file.unlink(missing_ok=True)
