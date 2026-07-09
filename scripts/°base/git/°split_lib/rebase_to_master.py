"""(C) part 2: rebase a branch's clean/history/unclean variants onto their
current masters.

Missing-branch handling per the plan: skip + clearly report, never
auto-synthesize a missing variant via sync-splits as a side effect of a
rebase command. `unclean`'s rebase target is `history`'s *just-rebased* tip
(a real dependency, not an independent one) -- so if `history` is missing or
its own rebase fails, `unclean`'s rebase is skipped too.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import branches, git_ops


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _rebase_onto(branch_ref: str, onto: str, cwd: Path) -> tuple[bool, str]:
    """Run `git rebase <onto>` with `branch_ref` checked out.

    Returns (success, detail). On failure, aborts the rebase so the repo is
    left clean for the caller (this is a normal linear rebase, not the
    base-merge-recreation case, so there's no automatic conflict resolution
    to attempt here).
    """
    checkout = _git(["checkout", branch_ref], cwd)
    if checkout.returncode != 0:
        return False, f"could not check out {branch_ref!r}: {checkout.stderr.strip()}"

    result = _git(["rebase", onto], cwd)
    if result.returncode != 0:
        _git(["rebase", "--abort"], cwd)
        return False, f"rebase onto {onto!r} failed: {result.stderr.strip() or result.stdout.strip()}"

    return True, f"rebased onto {onto!r}"


def rebase_branches_to_master(
    base_branch: str,
    *,
    repo_root: Path,
    main_branch: str,
    yes: bool = False,
    dry_run: bool = False,
) -> dict:
    """Rebase `base_branch`'s clean/history/unclean variants onto their
    current masters. Returns a dict of per-variant status strings, e.g.:

        {"clean": "rebased onto 'master'", "history": "skipped: ...", ...}

    Raises HistoryMasterError-style ValueError if none of the three variants
    exist at all (likely a typo in `base_branch`).
    """
    cwd = repo_root
    clean_ref = base_branch
    history_ref = branches.history_name(base_branch)
    unclean_ref = branches.unclean_name(base_branch)

    clean_exists = git_ops.rev_parse(clean_ref, cwd) is not None
    history_exists = git_ops.rev_parse(history_ref, cwd) is not None
    unclean_exists = git_ops.rev_parse(unclean_ref, cwd) is not None

    if not (clean_exists or history_exists or unclean_exists):
        raise ValueError(
            f"None of {clean_ref!r}, {history_ref!r}, {unclean_ref!r} exist -- "
            f"likely a typo in {base_branch!r}."
        )

    status: dict[str, str] = {}
    original_ref = _current_branch_or_head(cwd)

    if dry_run:
        if clean_exists:
            status["clean"] = f"dry-run: would rebase onto {main_branch!r}"
        else:
            status["clean"] = "skipped: branch does not exist"

        history_new_tip: str | None = None
        if history_exists:
            history_name = branches.history_name(main_branch)
            status["history"] = f"dry-run: would rebase onto {history_name!r}"
            history_new_tip = git_ops.rev_parse(history_ref, cwd)
        else:
            status["history"] = "skipped: branch does not exist"

        if unclean_exists:
            if not history_exists:
                status["unclean"] = "skipped: history missing (unclean rebases onto history's rebased tip)"
            else:
                status["unclean"] = f"dry-run: would rebase onto history's rebased tip"
        else:
            status["unclean"] = "skipped: branch does not exist"

        _restore_ref(original_ref, cwd)
        return status

    # --- clean ---
    if clean_exists:
        ok, detail = _rebase_onto(clean_ref, main_branch, cwd)
        status["clean"] = detail if ok else f"failed: {detail}"
    else:
        status["clean"] = "skipped: branch does not exist"

    # --- history ---
    history_rebase_ok = False
    history_new_tip: str | None = None
    if history_exists:
        history_main_ref = branches.history_name(main_branch)
        ok, detail = _rebase_onto(history_ref, history_main_ref, cwd)
        status["history"] = detail if ok else f"failed: {detail}"
        history_rebase_ok = ok
        if ok:
            history_new_tip = git_ops.rev_parse(history_ref, cwd)
    else:
        status["history"] = "skipped: branch does not exist"

    # --- unclean ---
    if unclean_exists:
        if not history_exists:
            status["unclean"] = "skipped: history missing (unclean rebases onto history's rebased tip)"
        elif not history_rebase_ok:
            status["unclean"] = "skipped: history rebase failed (unclean rebases onto history's rebased tip)"
        else:
            assert history_new_tip is not None
            ok, detail = _rebase_onto(unclean_ref, history_new_tip, cwd)
            status["unclean"] = detail if ok else f"failed: {detail}"
    else:
        status["unclean"] = "skipped: branch does not exist"

    _restore_ref(original_ref, cwd)
    return status


def _current_branch_or_head(cwd: Path) -> str:
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return git_ops.rev_parse("HEAD", cwd) or "HEAD"


def _restore_ref(ref: str, cwd: Path) -> None:
    subprocess.run(["git", "checkout", ref], cwd=cwd, capture_output=True)
