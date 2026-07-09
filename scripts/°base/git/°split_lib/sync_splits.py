"""(A) sync-splits forward direction: `ai/UNCLEAN/{branch}` -> `{branch}`
(clean) and -> `ai/history/{branch}` (history).

See ai/°base/todo.md and the sync-splits design plan for the full picture.
This module only implements the forward-replay orchestration; tree-splitting
mechanics live in tree_ops.py, trailer plumbing in trailers.py.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import branches, classify, git_ops, identity, trailers, tree_ops
from . import sync_unclean

SOURCE_TRAILER = "X-Base-Split-Source"
KIND_TRAILER = "X-Base-Split-Kind"
COUNTERPART_TREE_TRAILER = "X-Base-Split-Counterpart-Tree"


def find_last_synced_source(target_ref: str, cwd: Path) -> str | None:
    """Read the target branch tip's `X-Base-Split-Source` trailer.

    Returns None if the ref doesn't exist or has no such trailer -- the
    forward cursor is read directly off the branch tip, not a side ref.
    """
    tip = git_ops.rev_parse(target_ref, cwd)
    if tip is None:
        return None
    message = git_ops.commit_message(tip, cwd)
    return trailers.read_trailer_value(message, SOURCE_TRAILER, cwd)


def find_reconstruction_correlated_cursor(unclean_ref: str, target_ref: str, cwd: Path) -> str | None:
    """Fallback cursor for the first forward run after `sync_unclean.
    reconstruct_unclean` built `unclean_ref` from a clean-only/history-only
    branch (e.g. via `bootstrap-branch`).

    That reverse direction tags commits it creates on `unclean_ref` with
    `sync_unclean.RECON_TRAILER`, not `SOURCE_TRAILER` -- so `target_ref`'s
    own tip carries no trailer `find_last_synced_source` can read, even
    though its content is already fully represented on `unclean_ref`.
    Walk `unclean_ref` newest-first and return the first commit whose
    reconstruction trailer resolves to a real commit that is `target_ref`'s
    tip or an ancestor of it -- i.e. the furthest-forward point already
    covered, so replay can resume strictly after it instead of duplicating
    everything back to `merge-base(unclean_ref, lower_bound_ref)`.
    """
    target_tip = git_ops.rev_parse(target_ref, cwd)
    if target_tip is None:
        return None

    for sha in reversed(git_ops.rev_list_reverse(unclean_ref, cwd)):
        message = git_ops.commit_message(sha, cwd)
        candidate = trailers.read_trailer_value(message, sync_unclean.RECON_TRAILER, cwd)
        if candidate is None or not git_ops.rev_exists(candidate, cwd):
            continue
        if candidate == target_tip or git_ops.is_ancestor(candidate, target_tip, cwd):
            return sha
    return None


def commits_to_replay(
    unclean_ref: str,
    last_synced_source: str | None,
    lower_bound_ref: str,
    cwd: Path,
) -> list[str]:
    """Commits on `unclean_ref` still needing replay, oldest first."""
    if last_synced_source is not None:
        return git_ops.rev_list_reverse(f"{last_synced_source}..{unclean_ref}", cwd)

    base = git_ops.merge_base(unclean_ref, lower_bound_ref, cwd)
    if base is None:
        return git_ops.rev_list_reverse(unclean_ref, cwd)
    return git_ops.rev_list_reverse(f"{base}..{unclean_ref}", cwd)


def ensure_branch_started(ref: str, base_ref: str, cwd: Path, *, dry_run: bool = False) -> str:
    """Ensure `ref` exists (creating it at `base_ref`'s tip if missing) and
    return its current tip sha.

    In dry-run mode, no ref is actually created; the "current tip" is
    computed as if it had been.
    """
    tip = git_ops.rev_parse(ref, cwd)
    if tip is not None:
        return tip

    base_tip = git_ops.rev_parse(base_ref, cwd)
    assert base_tip is not None, f"base ref {base_ref!r} does not exist"

    if not dry_run:
        git_ops.create_branch(ref, base_tip, cwd)

    return base_tip


def kind_for(cls: classify.CommitClassification) -> str:
    if cls.is_ai_only_commit:
        return "history"
    if not cls.is_ai_tainted_commit:
        return "code"
    return "mixed"


def _author_info(sha: str, cwd: Path) -> tuple[str, str, str]:
    """Return (author_name, author_email, author_date) for `sha`, author_date
    as a raw git-acceptable date string (`%ad --date=raw`)."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%an%x1f%ae%x1f%ad", "--date=raw", sha],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    name, email, date = result.stdout.strip().split("\x1f")
    return name, email, date


def _committer_date_now() -> str:
    return "%d +0000" % int(time.time())


def make_split_commit(
    parent: str,
    tree: str,
    source_sha: str,
    kind: str,
    extra_trailers: dict[str, str],
    cwd: Path,
) -> str:
    author_name, author_email, author_date = _author_info(source_sha, cwd)
    base_message = git_ops.commit_message(source_sha, cwd)

    trailer_values = {SOURCE_TRAILER: source_sha, KIND_TRAILER: kind}
    trailer_values.update(extra_trailers)
    message = trailers.write_trailers(base_message, trailer_values, cwd)

    return git_ops.commit_tree(
        tree,
        [parent],
        message,
        cwd,
        author_name=author_name,
        author_email=author_email,
        author_date=author_date,
        committer_name=identity.BOT_NAME,
        committer_email=identity.BOT_EMAIL,
        committer_date=_committer_date_now(),
    )


@dataclass(frozen=True)
class SyncSplitsResult:
    branch: str
    clean_ref: str
    clean_commits_created: int
    clean_commits_skipped_ai_only: int
    history_ref: str
    history_commits_created: int


def sync_branch(
    base_branch: str,
    *,
    repo_root: Path,
    main_branch: str,
    dry_run: bool = False,
) -> SyncSplitsResult:
    cwd = repo_root
    unclean_ref = branches.unclean_name(base_branch)
    clean_ref = base_branch
    history_ref = branches.history_name(base_branch)
    history_main_ref = branches.history_name(main_branch)

    clean_tip = ensure_branch_started(clean_ref, main_branch, cwd, dry_run=dry_run)

    history_existed = git_ops.rev_parse(history_ref, cwd) is not None
    history_tip = ensure_branch_started(history_ref, history_main_ref, cwd, dry_run=dry_run)
    if not history_existed and not dry_run:
        # Record which ai/history/master commit this branch's history forked
        # from, so update-history-master can later replay only the commits
        # unique to it without relying on a merge-base fallback.
        git_ops.create_branch(branches.history_fork_point_ref(base_branch), history_tip, cwd)

    # --- clean pass ---
    clean_last_source = find_last_synced_source(clean_ref, cwd)
    if clean_last_source is None:
        clean_last_source = find_reconstruction_correlated_cursor(unclean_ref, clean_ref, cwd)
    clean_source_shas = commits_to_replay(unclean_ref, clean_last_source, main_branch, cwd)

    source_to_clean_tree: dict[str, str] = {}
    clean_commits_created = 0
    clean_commits_skipped_ai_only = 0

    for source_sha in clean_source_shas:
        cls = classify.classify_commit(
            source_sha,
            git_ops.subject_for_commit(source_sha, cwd),
            git_ops.changed_paths_for_commit(source_sha, cwd),
        )
        kind = kind_for(cls)

        if cls.is_ai_only_commit:
            clean_commits_skipped_ai_only += 1
            continue

        clean_tree = tree_ops.build_filtered_tree(
            git_ops.tree_for_commit(clean_tip, cwd),
            source_sha,
            cwd,
            keep=lambda p: not classify.is_ai_base_path(p),
        )
        source_to_clean_tree[source_sha] = clean_tree

        new_clean_tip = make_split_commit(clean_tip, clean_tree, source_sha, kind, {}, cwd)
        clean_tip = new_clean_tip
        clean_commits_created += 1

    if not dry_run and clean_commits_created > 0:
        git_ops.move_ref(clean_ref, clean_tip, None, cwd)

    # --- history pass ---
    history_last_source = find_last_synced_source(history_ref, cwd)
    if history_last_source is None:
        history_last_source = find_reconstruction_correlated_cursor(unclean_ref, history_ref, cwd)
    history_source_shas = commits_to_replay(unclean_ref, history_last_source, history_main_ref, cwd)

    history_commits_created = 0

    for source_sha in history_source_shas:
        cls = classify.classify_commit(
            source_sha,
            git_ops.subject_for_commit(source_sha, cwd),
            git_ops.changed_paths_for_commit(source_sha, cwd),
        )
        kind = kind_for(cls)

        history_tree = tree_ops.build_filtered_tree(
            git_ops.tree_for_commit(history_tip, cwd),
            source_sha,
            cwd,
            keep=classify.is_ai_base_path,
        )

        extra_trailers: dict[str, str] = {}
        counterpart_tree = source_to_clean_tree.get(source_sha)
        if counterpart_tree is not None:
            extra_trailers[COUNTERPART_TREE_TRAILER] = counterpart_tree

        new_history_tip = make_split_commit(history_tip, history_tree, source_sha, kind, extra_trailers, cwd)
        history_tip = new_history_tip
        history_commits_created += 1

    if not dry_run and history_commits_created > 0:
        git_ops.move_ref(history_ref, history_tip, None, cwd)

    return SyncSplitsResult(
        branch=base_branch,
        clean_ref=clean_ref,
        clean_commits_created=clean_commits_created,
        clean_commits_skipped_ai_only=clean_commits_skipped_ai_only,
        history_ref=history_ref,
        history_commits_created=history_commits_created,
    )


def discover_unclean_branches(cwd: Path) -> list[str]:
    result = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/ai/UNCLEAN/"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    names: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        base_name = branches.base_name_from_unclean(line)
        if base_name is not None:
            names.append(base_name)
    return names
