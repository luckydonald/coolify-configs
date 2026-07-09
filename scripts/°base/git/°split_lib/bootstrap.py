"""Phase 3: bootstrap the split workflow for a branch that currently exists
only as a plain `{branch}` (clean-format) branch with real commits -- no
`ai/UNCLEAN/{branch}` or `ai/history/{branch}` exist yet.

Mostly glue: `sync_unclean.reconstruct_unclean` already tolerates a missing
`history` branch correctly (every clean commit buckets as unmatched/code-only
and gets cherry-picked as-is), so this module's own job is just: (1) refuse
clearly if `ai/history/master` doesn't exist yet (never auto-run
update-history-master as a side effect -- same "skip + report" principle as
rebase_to_master.py), (2) create an empty `ai/history/{branch}` with its
fork-point ref if missing, (3) delegate to reconstruct_unclean.
"""

from __future__ import annotations

from pathlib import Path

from . import branches, git_ops, sync_unclean


def bootstrap_branch(
    base_branch: str,
    *,
    repo_root: Path,
    main_branch: str,
    dry_run: bool = False,
) -> dict:
    cwd = repo_root
    history_main_ref = branches.history_name(main_branch)

    if git_ops.rev_parse(history_main_ref, cwd) is None:
        return {
            "branch": base_branch,
            "ok": False,
            "error": (
                f"{history_main_ref!r} does not exist yet -- run "
                "update-history-master first."
            ),
        }

    if git_ops.rev_parse(base_branch, cwd) is None:
        return {
            "branch": base_branch,
            "ok": False,
            "error": f"clean branch {base_branch!r} does not exist -- nothing to bootstrap from.",
        }

    history_ref = branches.history_name(base_branch)
    history_created = False
    if git_ops.rev_parse(history_ref, cwd) is None:
        history_created = True
        if not dry_run:
            history_tip = git_ops.rev_parse(history_main_ref, cwd)
            git_ops.create_branch(history_ref, history_tip, cwd)
            git_ops.create_branch(branches.history_fork_point_ref(base_branch), history_tip, cwd)

    reconstruct_result = sync_unclean.reconstruct_unclean(
        base_branch,
        repo_root=repo_root,
        main_branch=main_branch,
        dry_run=dry_run,
    )

    return {
        "branch": base_branch,
        "ok": True,
        "history_ref": history_ref,
        "history_created": history_created,
        "reconstruct": reconstruct_result,
    }
