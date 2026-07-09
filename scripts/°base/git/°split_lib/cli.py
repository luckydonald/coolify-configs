from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import branches, classify, git_ops, push_checks
from . import bootstrap as bootstrap_lib
from . import history_master as history_master_lib
from . import rebase_to_master as rebase_to_master_lib
from . import recovery
from . import sync_splits as sync_splits_lib
from . import sync_unclean as sync_unclean_lib


def _resolve_repo_root(args: argparse.Namespace) -> Path:
    if getattr(args, "repo_root", None) is not None:
        return Path(args.repo_root)
    return git_ops.repo_root()


def _build_logger(repo_root: Path) -> logging.Logger:
    """One logger per invocation, shared with history_master.py via a fixed
    name (see history_master_lib.LOGGER_NAME) -- console gets a terse,
    readable narration (INFO+); the full detail (every git command run, ref
    snapshots, rollback commands) always lands in .rebase-recovery.tmp
    (DEBUG), so nothing is lost even when the console stays quiet.
    """
    logger = logging.getLogger(history_master_lib.LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    plain = logging.Formatter("%(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(plain)
    logger.addHandler(console)

    file_handler = logging.FileHandler(repo_root / recovery.RECOVERY_FILENAME)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(plain)
    logger.addHandler(file_handler)

    return logger


def _run_with_recovery(
    *,
    repo_root: Path,
    main_branch: str,
    branch: str | None,
    dry_run: bool,
    invocation: str,
    run_fn: Callable[[], int],
) -> int:
    """Snapshot every ref an invocation could touch and log undo commands
    for it *before* running anything -- so recovery info survives even a
    crash mid-operation. See scripts/°base/git/°split_lib/recovery.py.

    The full ref table + rollback commands always go to .rebase-recovery.tmp
    (DEBUG); the console (INFO) only gets a one-line pointer to it, plus the
    full "after" summary if the run didn't cleanly succeed -- that's the
    detail actually worth seeing without going and opening the file.
    """
    if dry_run:
        return run_fn()

    logger = _build_logger(repo_root)
    try:
        watched = recovery.resolve_watched_refs(branch, main_branch, repo_root)
        before = recovery.snapshot(watched, repo_root)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = recovery.format_recovery_entry(invocation, before, timestamp)
        logger.debug(entry)
        logger.info("snapshotted %d ref(s) -> %s", len(watched), repo_root / recovery.RECOVERY_FILENAME)

        try:
            return run_fn()
        finally:
            after = recovery.snapshot(watched, repo_root)
            summary = recovery.format_after_summary(before, after)
            if before != after:
                logger.info(summary)
            else:
                logger.debug(summary)
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def _parse_ref_lines(text: str) -> list[push_checks.RefUpdate]:
    updates: list[push_checks.RefUpdate] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        local_ref, local_sha, remote_ref, remote_sha = line.split()
        updates.append(push_checks.RefUpdate(local_ref, local_sha, remote_ref, remote_sha))
    return updates


def _check_push(remote_name: str, remote_url: str, stdin_text: str, *, repo_root: Path) -> int:
    ref_updates = _parse_ref_lines(stdin_text)
    main_branch = branches.detect_main_branch(repo_root)

    all_violations: list[str] = []
    for ref_update in ref_updates:
        if push_checks.is_zero_sha(ref_update.local_sha):
            continue  # deletion

        branch = branches.classify_branch(ref_update.local_ref, main_branch=main_branch)

        shas = git_ops.commits_new_to_remote(
            ref_update.local_sha, ref_update.remote_sha, remote_name, repo_root
        )
        commits = [
            classify.classify_commit(
                sha,
                git_ops.subject_for_commit(sha, repo_root),
                git_ops.changed_paths_for_commit(sha, repo_root),
            )
            for sha in shas
        ]

        violations = push_checks.evaluate_ref_update(ref_update, branch, remote_name, commits)
        all_violations.extend(violations)

    if all_violations:
        print("Push blocked by base branch-split policy:", file=sys.stderr)
        for violation in all_violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1

    return 0


def _sync_splits(args: argparse.Namespace, *, repo_root: Path, main_branch: str) -> int:
    if args.direction == "to-clean-history":
        targets = [args.branch] if args.branch else sync_splits_lib.discover_unclean_branches(repo_root)
        for branch in targets:
            result = sync_splits_lib.sync_branch(
                branch, repo_root=repo_root, main_branch=main_branch, dry_run=args.dry_run
            )
            print(
                f"{branch}: clean +{result.clean_commits_created} "
                f"(skipped {result.clean_commits_skipped_ai_only}), "
                f"history +{result.history_commits_created}"
            )
        return 0

    # to-unclean
    targets = [args.branch] if args.branch else sync_splits_lib.discover_unclean_branches(repo_root)
    exit_code = 0
    for branch in targets:
        try:
            result = sync_unclean_lib.reconstruct_unclean(
                branch,
                repo_root=repo_root,
                main_branch=main_branch,
                allow_diverge_rewrite=args.allow_diverge_rewrite,
                force=args.force,
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            print(f"{branch}: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        print(f"{branch}: {result}")
        if result.get("divergences_found") and not args.allow_diverge_rewrite:
            exit_code = 1
    return exit_code


_CONFLICT_NEXT_STEPS = """\
Choose one:

  [1] Resolve and continue
      - Resolve the conflict in the working tree (branch `{scratch_branch}`)
      - git add <resolved files>
      - scripts/°base/git/split.py --repo-root {repo_root} update-history-master --continue

  [2] Abort this run (keeps any already-pulled `{main_branch}`)
      - scripts/°base/git/split.py --repo-root {repo_root} update-history-master --abort

  [3] Full manual rollback (only if [2] isn't enough, e.g. to also undo the
      `{main_branch}` pull) -- see the ref table and `git update-ref` commands
      already logged to .rebase-recovery.tmp for this run."""


def _update_history_master(args: argparse.Namespace, *, repo_root: Path, main_branch: str) -> int:
    logger = logging.getLogger(history_master_lib.LOGGER_NAME)
    try:
        result = history_master_lib.update_history_master(
            repo_root=repo_root,
            main_branch=main_branch,
            force_merge=args.force_merge,
            pull_master=args.pull_master,
            pull_base=args.pull_base,
            yes=args.yes,
            continue_=args.continue_,
            abort=args.abort,
            dry_run=args.dry_run,
        )
    except history_master_lib.HistoryMasterError as exc:
        logger.error("update-history-master: %s", exc)
        return 1

    logger.debug("result: %r", result)
    if result.get("status") == "conflict":
        pending = result.get("pending", {})
        message = result.get("message") or pending.get("message") or pending.get("detail")
        block = "== CONFLICT ==\n"
        if message:
            block += f"{message}\n\n"
        block += _CONFLICT_NEXT_STEPS.format(
            scratch_branch=history_master_lib.SCRATCH_BRANCH,
            repo_root=repo_root,
            main_branch=main_branch,
        )
        logger.warning(block)
        return 1

    if result.get("status") not in ("ok", "aborted", "no-op") or args.dry_run:
        # dry-run / unrecognized shapes: fall back to the raw dict so nothing
        # is silently hidden.
        logger.info("%r", result)
    return 0


def _rebase_branches_to_master(args: argparse.Namespace, *, repo_root: Path, main_branch: str) -> int:
    targets = [args.branch] if args.branch else sync_splits_lib.discover_unclean_branches(repo_root)
    exit_code = 0
    for branch in targets:
        try:
            result = rebase_to_master_lib.rebase_branches_to_master(
                branch, repo_root=repo_root, main_branch=main_branch, yes=args.yes, dry_run=args.dry_run
            )
        except ValueError as exc:
            print(f"{branch}: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        print(f"{branch}: {result}")
    return exit_code


def _bootstrap_branch(args: argparse.Namespace, *, repo_root: Path, main_branch: str) -> int:
    result = bootstrap_lib.bootstrap_branch(
        args.branch, repo_root=repo_root, main_branch=main_branch, dry_run=args.dry_run
    )
    if not result["ok"]:
        print(f"{args.branch}: {result['error']}", file=sys.stderr)
        return 1
    print(f"{args.branch}: {result}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="split.py")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Target repo to operate on. Defaults to the repo containing cwd. "
        "Lets the tool be invoked from elsewhere (e.g. a standalone clone or "
        "worktree) without cd'ing into the target repo first.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_push = subparsers.add_parser(
        "check-push", help="Enforce clean/unclean/history push name+content policy."
    )
    check_push.add_argument("--remote-name", required=True)
    check_push.add_argument("--remote-url", required=True)

    sync_splits = subparsers.add_parser(
        "sync-splits", help="Generate/update clean+history from unclean, or reconstruct unclean from them."
    )
    sync_splits.add_argument("branch", nargs="?", help="Base branch name. Omit to process all ai/UNCLEAN/* branches.")
    sync_splits.add_argument(
        "--direction", choices=["to-clean-history", "to-unclean"], default="to-clean-history"
    )
    sync_splits.add_argument("--dry-run", action="store_true")
    sync_splits.add_argument("--force", action="store_true")
    sync_splits.add_argument("--allow-diverge-rewrite", action="store_true")

    update_history = subparsers.add_parser("update-history-master", help="Rebuild ai/history/master.")
    update_history.add_argument("--force-merge", action="append", default=[], metavar="BRANCH")
    update_history.add_argument("--pull-master", action="store_true")
    update_history.add_argument("--pull-base", action="store_true")
    update_history.add_argument("--yes", action="store_true")
    update_history.add_argument("--continue", dest="continue_", action="store_true")
    update_history.add_argument("--abort", action="store_true")
    update_history.add_argument("--dry-run", action="store_true")

    rebase_branches = subparsers.add_parser(
        "rebase-branches-to-master", help="Rebase clean/history/unclean onto their current masters."
    )
    rebase_branches.add_argument("branch", nargs="?", help="Base branch name. Omit to process all detected branches.")
    rebase_branches.add_argument("--yes", action="store_true")
    rebase_branches.add_argument("--dry-run", action="store_true")

    bootstrap_branch = subparsers.add_parser(
        "bootstrap-branch",
        help="Start the split workflow for a branch that only exists as clean so far.",
    )
    bootstrap_branch.add_argument("branch", help="Base branch name (must already exist as a clean branch).")
    bootstrap_branch.add_argument("--dry-run", action="store_true")

    real_argv = argv if argv is not None else sys.argv[1:]
    args = parser.parse_args(real_argv)
    invocation = "scripts/°base/git/split.py " + " ".join(real_argv)

    if args.command == "check-push":
        stdin_text = sys.stdin.read()
        root = _resolve_repo_root(args)
        return _check_push(args.remote_name, args.remote_url, stdin_text, repo_root=root)

    if args.command == "sync-splits":
        root = _resolve_repo_root(args)
        main_branch = branches.detect_main_branch(root)
        return _run_with_recovery(
            repo_root=root,
            main_branch=main_branch,
            branch=args.branch,
            dry_run=args.dry_run,
            invocation=invocation,
            run_fn=lambda: _sync_splits(args, repo_root=root, main_branch=main_branch),
        )

    if args.command == "update-history-master":
        root = _resolve_repo_root(args)
        main_branch = branches.detect_main_branch(root)
        return _run_with_recovery(
            repo_root=root,
            main_branch=main_branch,
            branch=None,
            dry_run=args.dry_run,
            invocation=invocation,
            run_fn=lambda: _update_history_master(args, repo_root=root, main_branch=main_branch),
        )

    if args.command == "rebase-branches-to-master":
        root = _resolve_repo_root(args)
        main_branch = branches.detect_main_branch(root)
        return _run_with_recovery(
            repo_root=root,
            main_branch=main_branch,
            branch=args.branch,
            dry_run=args.dry_run,
            invocation=invocation,
            run_fn=lambda: _rebase_branches_to_master(args, repo_root=root, main_branch=main_branch),
        )

    if args.command == "bootstrap-branch":
        root = _resolve_repo_root(args)
        main_branch = branches.detect_main_branch(root)
        return _run_with_recovery(
            repo_root=root,
            main_branch=main_branch,
            branch=args.branch,
            dry_run=args.dry_run,
            invocation=invocation,
            run_fn=lambda: _bootstrap_branch(args, repo_root=root, main_branch=main_branch),
        )

    parser.error(f"Unknown command: {args.command}")
    return 2
