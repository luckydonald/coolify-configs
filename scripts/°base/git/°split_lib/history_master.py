"""(C) part 1: keep `ai/history/master` in sync with `master` and `base/base`.

No `git rebase --exec` (see ai/°base/errors/16.txt, 17.txt for the two real
failure classes that a `--exec`-driven rebase already hit in
`rebase_strip_claude_authorship.py`): a stale self-relocated script path, and
an unhandled conflict on a file that needed manual resolution. Both are
avoided here by driving the walk from a plain Python loop, using
`git_ops.cherry_pick`/`cherry_pick_continue`/`cherry_pick_abort` per ordinary
commit and an explicit merge-recreation procedure for base-merges.

See ai/°base/todo.md and the update-history-master design plan for the full
picture.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import branches, git_ops, identity, trailers

MERGE_KIND_TRAILER = "X-Base-History-Merge-Kind"
MERGE_SHA_TRAILER = "X-Base-History-Merge-Sha"
MERGE_REPLAYED_FROM_TRAILER = "X-Base-History-Merge-Replayed-From"
CLEAN_BRANCH_TRAILER = "X-Base-Split-Clean-Branch"
MERGE_MARKER_TRAILER = "X-Base-Split-Merge-Marker-For"

STATE_FILENAME = "BASE_SPLIT_HISTORY_MASTER_STATE"
SCRATCH_BRANCH = "_base_split_scratch"
SCRATCH_REF = f"refs/heads/{SCRATCH_BRANCH}"
BASE_REMOTE_REF = "refs/remotes/base/base"

# Fixed name (not `logging.getLogger(__name__)`) so cli.py can attach handlers
# to exactly this logger without needing to know this module's dotted path
# (which, thanks to the `°split_lib` package name, is awkward to spell out).
LOGGER_NAME = "base-split.history-master"
logger = logging.getLogger(LOGGER_NAME)
# Standard library-code practice: stay silent when nothing (cli.py, a test)
# has attached real handlers, instead of falling back to logging's WARNING+
# "handler of last resort" on stderr.
logger.addHandler(logging.NullHandler())


class HistoryMasterError(RuntimeError):
    """Raised for errors that update_history_master cannot resolve itself."""


class CherryPickConflict(HistoryMasterError):
    """An ordinary commit replay conflicted and needs manual resolution.

    Carries enough info for a CLI layer to print recovery instructions and
    for update_history_master to persist resumable state.
    """

    def __init__(self, sha: str, onto: str, stdout: str, stderr: str) -> None:
        super().__init__(
            f"Cherry-pick of {sha} onto {onto} conflicted.\n"
            "Resolve the conflict in the working tree (currently checked out "
            f"on {SCRATCH_BRANCH!r}), `git add` the resolved paths, then rerun "
            "update-history-master with --continue (or --abort to cancel).\n"
            f"{stderr or stdout}"
        )
        self.sha = sha
        self.onto = onto


class MergeConflict(HistoryMasterError):
    """A fresh (non-recreation) merge conflicted and needs manual resolution."""

    def __init__(self, sha: str, onto: str, stderr: str) -> None:
        super().__init__(
            f"Merge of {sha} onto {onto} conflicted and could not be "
            "auto-resolved (only base-merge *recreation* resolves "
            "automatically). Resolve conflicts, `git add` the resolved paths, "
            "then rerun update-history-master with --continue (or --abort).\n"
            f"{stderr}"
        )
        self.sha = sha
        self.onto = onto


# --------------------------------------------------------------------------
# Small subprocess helpers local to this module (git_ops.py is shared/frozen
# for this task; anything genuinely missing is implemented here instead).
# --------------------------------------------------------------------------


def _git(args: list[str], cwd: Path, *, check: bool = False) -> subprocess.CompletedProcess:
    logger.debug("$ git %s", " ".join(args))
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    _log_completed(result)
    if check and result.returncode != 0:
        raise HistoryMasterError(f"git {' '.join(args)} failed: {result.stderr or result.stdout}")
    return result


def _log_completed(result: subprocess.CompletedProcess, *, label: str | None = None) -> subprocess.CompletedProcess:
    """DEBUG-log the outcome of a `git_ops.py` call (frozen/shared plumbing
    that doesn't log itself -- see the module docstring). Called at each
    call site instead of touching git_ops.py.
    """
    prefix = f"{label}: " if label else ""
    if result.returncode == 0:
        logger.debug("%src=0", prefix)
        return result
    logger.debug("%src=%s", prefix, result.returncode)
    if result.stderr and result.stderr.strip():
        logger.debug("  stderr: %s", result.stderr.strip())
    if result.stdout and result.stdout.strip():
        logger.debug("  stdout: %s", result.stdout.strip())
    return result


def _head_sha(cwd: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd, check=True).stdout.strip()


def _first_parent_chain_reverse(range_expr: str, cwd: Path) -> list[str]:
    """`git rev-list --first-parent --reverse <range_expr>`.

    Used specifically for walking history-master's *own* previously-added
    commits when replaying them onto a moved master: history-master's base-
    merge commits have a second parent (base/base's tip) that must NOT be
    treated as an independent commit needing its own replay -- it's handled
    wholesale by recreate_base_merge() re-merging against the recorded
    X-Base-History-Merge-Sha trailer instead. A plain (all-parents) rev-list
    would otherwise walk into that second-parent side and misclassify its
    commits as ordinary standalone commits to cherry-pick.
    """
    result = subprocess.run(
        ["git", "rev-list", "--first-parent", "--reverse", range_expr],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _delete_ref(ref: str, cwd: Path) -> None:
    subprocess.run(["git", "update-ref", "-d", ref], cwd=cwd, capture_output=True)


def _conflicted_paths(cwd: Path) -> list[str]:
    result = _git(["diff", "--name-only", "--diff-filter=U"], cwd, check=True)
    return [line for line in result.stdout.splitlines() if line]


def _checkout_scratch(onto: str, cwd: Path) -> None:
    """(Re)create the scratch branch at `onto` and check it out.

    Detaches HEAD first so this is safe to call even when currently sitting
    on a stale scratch branch from a previous (finished) step.
    """
    _git(["checkout", "--detach", "HEAD"], cwd)
    _delete_ref(SCRATCH_REF, cwd)
    git_ops.create_branch(SCRATCH_REF, onto, cwd)
    git_ops.checkout_branch(SCRATCH_BRANCH, cwd)


def _cleanup_scratch(cwd: Path) -> None:
    """Detach off the scratch branch and delete it. Only call this once a
    step has *cleanly* finished -- never while a conflict is still open,
    since resuming (--continue) needs the scratch branch's mid-operation
    state (CHERRY_PICK_HEAD/MERGE_HEAD) to still be there.
    """
    tip = _head_sha(cwd)
    _git(["checkout", "--detach", tip], cwd, check=True)
    _delete_ref(SCRATCH_REF, cwd)


def _finish_merge_commit(cwd: Path) -> str:
    env_editor_true = ["-c", "core.editor=true"]
    _git([*env_editor_true, "commit", "--no-edit"], cwd, check=True)
    return _head_sha(cwd)


def _prompt_yes_no(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    sys.stdout.write(f"{prompt} [y/N] ")
    sys.stdout.flush()
    answer = sys.stdin.readline().strip().lower()
    return answer in ("y", "yes")


def _refuse_if_checked_out_dirty(ref_short_name: str, cwd: Path) -> None:
    """Plumbing ref moves (`git update-ref`) never touch the working tree or
    index. If `ref_short_name` happens to be the branch currently checked out
    in `cwd` with uncommitted changes, moving it out from under that checkout
    would leave those changes stranded against a tree that no longer matches
    the ref -- refuse up front, before anything is moved, exactly like `git
    pull`/`git rebase` would refuse on a dirty checkout.
    """
    current = _git(["branch", "--show-current"], cwd, check=True).stdout.strip()
    if current != ref_short_name:
        return
    status = _git(["status", "--porcelain"], cwd, check=True).stdout
    if status.strip():
        raise HistoryMasterError(
            f"{ref_short_name!r} is currently checked out with uncommitted changes; "
            "commit or stash them before running this, so the checkout can be kept in sync."
        )


def _sync_checkout_if_current(ref_short_name: str, new_sha: str, cwd: Path) -> None:
    """Bring `ref_short_name`'s checkout in sync after its ref has just been
    moved to `new_sha` via plumbing. Only call this after
    `_refuse_if_checked_out_dirty` already confirmed there's nothing to lose.

    Uses `reset --hard` rather than a fast-forward-only update since
    `history_ref`'s own tip is not always a literal fast-forward of its old
    position (replay rebuilds commits with new shas) -- only `main_branch`'s
    pull happens to be one.
    """
    current = _git(["branch", "--show-current"], cwd, check=True).stdout.strip()
    if current != ref_short_name:
        return
    _git(["reset", "--hard", new_sha], cwd, check=True)


def _current_checkout(cwd: Path) -> str | None:
    """The branch currently checked out in `cwd`, or `None` if HEAD is
    already detached (`git symbolic-ref` exits non-zero in that case --
    that's the expected way to detect it, not an error).
    """
    result = _git(["symbolic-ref", "--short", "-q", "HEAD"], cwd)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _restore_checkout(original: str | None, cwd: Path) -> None:
    """Best-effort: check `original` back out if it was captured (see
    `_current_checkout`). Every step this tool takes runs through the
    `_base_split_scratch` scratch branch, which always leaves HEAD detached
    when it's done (see `_checkout_scratch`/`_cleanup_scratch`) -- without
    this, a run that started on `master` (or any branch that isn't the one
    this tool moves) ends with the user silently dropped into detached HEAD.
    Never raises: a cosmetic restore failing (e.g. the branch was deleted
    meanwhile) shouldn't turn an otherwise-successful run into a failure.
    """
    if original is None:
        return
    result = _git(["checkout", original], cwd)
    if result.returncode != 0:
        logger.warning(
            "could not restore checkout onto %r after finishing (left on detached HEAD): %s",
            original,
            (result.stderr or result.stdout).strip(),
        )


def _pull_master(cwd: Path, main_branch: str) -> None:
    fetch = _git(["fetch", "origin", main_branch], cwd)
    if fetch.returncode != 0:
        return  # best-effort; no "origin" remote (or offline) is not fatal here
    remote_sha = git_ops.rev_parse(f"refs/remotes/origin/{main_branch}", cwd)
    if remote_sha is None:
        return
    local_sha = git_ops.rev_parse(main_branch, cwd)
    if local_sha is None or git_ops.is_ancestor(local_sha, remote_sha, cwd):
        _refuse_if_checked_out_dirty(main_branch, cwd)
        git_ops.move_ref(f"refs/heads/{main_branch}", remote_sha, local_sha, cwd)
        _sync_checkout_if_current(main_branch, remote_sha, cwd)


def _pull_base(cwd: Path) -> None:
    _git(["fetch", "base"], cwd)  # best-effort; no "base" remote is not fatal here


# --------------------------------------------------------------------------
# State file
# --------------------------------------------------------------------------


def _state_path(repo_root: Path) -> Path:
    return repo_root / ".git" / STATE_FILENAME


def _read_state(repo_root: Path) -> dict | None:
    path = _state_path(repo_root)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write_state(repo_root: Path, state: dict) -> None:
    _state_path(repo_root).write_text(json.dumps(state, indent=2))


def _clear_state(repo_root: Path) -> None:
    _state_path(repo_root).unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Per-commit primitives
# --------------------------------------------------------------------------


def is_base_merge(sha: str, cwd: Path) -> bool:
    return trailers.read_trailer_value(git_ops.commit_message(sha, cwd), MERGE_KIND_TRAILER, cwd) == "base-merge"


def _is_empty_cherry_pick(result: subprocess.CompletedProcess, cwd: Path) -> bool:
    """True if a failed cherry-pick failed only because the resulting diff is
    empty (e.g. re-picking an already-empty marker commit onto a tree that
    happens to already match) -- not a real content conflict.
    """
    if _conflicted_paths(cwd):
        return False
    combined = f"{result.stdout}\n{result.stderr}"
    return "previous cherry-pick is now empty" in combined


def replay_commit(sha: str, onto: str, cwd: Path) -> str:
    """Cherry-pick an ordinary commit onto `onto`. Message (and any
    `X-Base-Split-*` trailers on it) is preserved verbatim by cherry-pick.
    """
    _checkout_scratch(onto, cwd)
    result = _log_completed(git_ops.cherry_pick(sha, cwd), label=f"cherry-pick {sha}")
    if result.returncode != 0:
        if _is_empty_cherry_pick(result, cwd):
            # git_ops.cherry_pick() can't pass --allow-empty (it's shared,
            # frozen plumbing with a fixed argv) -- finish the paused pick
            # manually instead of treating "nothing to commit" as a real
            # conflict. The commit message git already staged for us (from
            # the cherry-pick sequencer) is reused verbatim by --no-edit.
            _git(["commit", "--allow-empty", "--no-edit"], cwd, check=True)
        else:
            raise CherryPickConflict(sha, onto, result.stdout, result.stderr)
    new_sha = _head_sha(cwd)
    _cleanup_scratch(cwd)
    return new_sha


def recreate_base_merge(old_merge_sha: str, onto: str, cwd: Path) -> str:
    """Recreate a `base/base`-folding merge commit on top of a rebased `onto`.

    Conflicts are resolved automatically and only for paths that conflict in
    *this* merge, by reusing the original merge commit's resolved blob for
    that path -- never a wholesale tree replace (would clobber unrelated new
    changes) and never a raw patch-apply (undefined base).
    """
    message = git_ops.commit_message(old_merge_sha, cwd)
    base_old_sha = trailers.read_trailer_value(message, MERGE_SHA_TRAILER, cwd)
    if not base_old_sha:
        raise HistoryMasterError(
            f"{old_merge_sha} is tagged {MERGE_KIND_TRAILER}: base-merge but has no "
            f"{MERGE_SHA_TRAILER} trailer; cannot recreate."
        )

    _checkout_scratch(onto, cwd)
    result = _log_completed(git_ops.merge_no_commit(base_old_sha, cwd), label=f"merge (recreate) {base_old_sha}")
    if result.returncode != 0:
        conflicted = _conflicted_paths(cwd)
        if not conflicted:
            logger.debug("$ git merge --abort")
            git_ops.merge_abort(cwd)
            _cleanup_scratch(cwd)
            raise HistoryMasterError(
                f"merge of {base_old_sha} onto {onto} failed for a non-conflict reason: {result.stderr}"
            )
        for path in conflicted:
            content = git_ops.show_path_at(old_merge_sha, path, cwd)
            target = cwd / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            _git(["add", "--", path], cwd, check=True)

    new_sha = _finish_merge_commit(cwd)
    new_message = trailers.write_trailers(
        git_ops.commit_message(new_sha, cwd),
        {
            MERGE_KIND_TRAILER: "base-merge",
            MERGE_SHA_TRAILER: base_old_sha,
            MERGE_REPLAYED_FROM_TRAILER: old_merge_sha,
        },
        cwd,
    )
    _git(["commit", "--amend", "-m", new_message], cwd, check=True)
    new_sha = _head_sha(cwd)
    _cleanup_scratch(cwd)
    return new_sha


def _complete_base_fold(base_sha: str, cwd: Path) -> str:
    new_sha = _finish_merge_commit(cwd)
    new_message = trailers.write_trailers(
        git_ops.commit_message(new_sha, cwd),
        {MERGE_KIND_TRAILER: "base-merge", MERGE_SHA_TRAILER: base_sha},
        cwd,
    )
    _git(["commit", "--amend", "-m", new_message], cwd, check=True)
    new_sha = _head_sha(cwd)
    _cleanup_scratch(cwd)
    return new_sha


def _fold_base(base_sha: str, onto: str, cwd: Path) -> str:
    """Fold a genuinely-new `base/base` tip into history-master. Unlike
    `recreate_base_merge`, there's no prior resolution to reuse here, so real
    conflicts are surfaced for manual resolution rather than auto-resolved.

    `base/base` is, by design, a separate template repo with no shared
    ancestor with a fresh consuming repo (see README.md "Setup: c) Merge
    base/base" -- initial adoption there is documented as
    `git merge --allow-unrelated-histories --no-ff base/base`). The very
    first fold onto a given history-master line hits exactly that case, so
    detect it here (rather than gating on "first run" bookkeeping, which
    doesn't cover a repo that adopted `base/base` after the fact) and pass
    the flag only when it's actually needed.
    """
    _checkout_scratch(onto, cwd)
    unrelated = git_ops.merge_base(onto, base_sha, cwd) is None
    merge_args = ["merge", "--no-commit", "--no-ff"]
    if unrelated:
        merge_args.append("--allow-unrelated-histories")
    merge_args.append(base_sha)
    result = _git(merge_args, cwd)
    if result.returncode != 0:
        conflicted = _conflicted_paths(cwd)
        if not conflicted:
            logger.debug("$ git merge --abort")
            git_ops.merge_abort(cwd)
            _cleanup_scratch(cwd)
            raise HistoryMasterError(f"merge of {base_sha} onto {onto} failed: {result.stderr}")
        raise MergeConflict(base_sha, onto, result.stderr)
    return _complete_base_fold(base_sha, cwd)


def _create_merge_marker(clean_merge_sha: str, branch_name: str, onto: str, cwd: Path) -> str:
    """An empty commit on history-master marking where a branch's replayed
    history commits end. Pure plumbing (commit-tree with `onto`'s own tree) --
    no working-tree checkout needed.
    """
    tree = git_ops.tree_for_commit(onto, cwd)
    now = f"{int(datetime.now(timezone.utc).timestamp())} +0000"
    base_message = f"[base] history: mark end of {branch_name!r}'s replayed history\n"
    message = trailers.write_trailers(base_message, {MERGE_MARKER_TRAILER: clean_merge_sha}, cwd)
    return git_ops.commit_tree(
        tree,
        [onto],
        message,
        cwd,
        author_name=identity.BOT_NAME,
        author_email=identity.BOT_EMAIL,
        author_date=now,
        committer_name=identity.BOT_NAME,
        committer_email=identity.BOT_EMAIL,
        committer_date=now,
    )


def find_newly_merged_clean_branches(old_master_sha: str | None, new_master_sha: str, cwd: Path) -> list[tuple[str, str]]:
    """Walk newly-added master commits for the `X-Base-Split-Clean-Branch` trailer.

    Deviation from the plan's literal `(sha, sha)` signature: `old_master_sha`
    is typed `str | None` here so the very first update-history-master run --
    which has no prior master reference point to bound the range with -- can
    reuse this same scan over master's *entire* history, instead of a
    parallel bespoke implementation. Passing `None` scans `new_master_sha`'s
    whole ancestry.
    """
    range_expr = new_master_sha if old_master_sha is None else f"{old_master_sha}..{new_master_sha}"
    found: list[tuple[str, str]] = []
    for sha in git_ops.rev_list_reverse(range_expr, cwd):
        branch = trailers.read_trailer_value(git_ops.commit_message(sha, cwd), CLEAN_BRANCH_TRAILER, cwd)
        if branch:
            found.append((sha, branch))
    return found


def has_merge_marker(history_master_tip: str, clean_merge_sha: str, cwd: Path) -> bool:
    for sha in git_ops.rev_list_reverse(history_master_tip, cwd):
        value = trailers.read_trailer_value(git_ops.commit_message(sha, cwd), MERGE_MARKER_TRAILER, cwd)
        if value == clean_merge_sha:
            return True
    return False


# --------------------------------------------------------------------------
# Plan execution
# --------------------------------------------------------------------------


def _execute_step(step: dict, tip: str, cwd: Path) -> str:
    kind = step["kind"]
    if kind == "commit":
        return replay_commit(step["sha"], tip, cwd)
    if kind == "base_merge":
        return recreate_base_merge(step["sha"], tip, cwd)
    if kind == "marker":
        return _create_merge_marker(step["clean_sha"], step.get("branch", ""), tip, cwd)
    if kind == "base_fold":
        return _fold_base(step["sha"], tip, cwd)
    raise HistoryMasterError(f"unknown step kind {kind!r}")


def _describe_step(step: dict, cwd: Path) -> str:
    kind = step["kind"]
    if kind == "commit":
        sha = step["sha"]
        try:
            subject = git_ops.subject_for_commit(sha, cwd)
        except subprocess.CalledProcessError:
            subject = ""
        return f"commit {sha[:8]} {subject!r}"
    if kind == "base_merge":
        return f"recreate base-merge {step['sha'][:8]}"
    if kind == "marker":
        return f"merge marker for branch {step.get('branch', '')!r}"
    if kind == "base_fold":
        return f"fold base {step['sha'][:8]}"
    return repr(step)


def _run_steps(steps: list[dict], tip: str, cwd: Path) -> tuple[str, list[dict], dict | None]:
    remaining = list(steps)
    total = len(steps)
    while remaining:
        step = remaining[0]
        idx = total - len(remaining) + 1
        logger.info("[%d/%d] %s", idx, total, _describe_step(step, cwd))
        try:
            tip = _execute_step(step, tip, cwd)
        except CherryPickConflict as exc:
            logger.warning("  -> CONFLICT")
            return tip, remaining, {"kind": "cherry-pick", "step": step, "message": str(exc)}
        except MergeConflict as exc:
            logger.warning("  -> CONFLICT")
            return tip, remaining, {"kind": "merge", "step": step, "message": str(exc)}
        logger.info("  -> ok (%s)", tip[:8])
        remaining.pop(0)
    return tip, [], None


def _build_plan(
    *,
    cwd: Path,
    main_branch: str,
    master_tip: str,
    old_history_sha: str | None,
    force_merge: list[str],
) -> tuple[list[dict], str, str | None]:
    """Compute the ordered list of steps to bring history-master up to date,
    plus the sha to start replaying from. Pure planning -- no mutation.

    Returns (steps, replay_start_tip, old_master_sha).
    """
    first_run = old_history_sha is None

    if first_run:
        old_master_sha: str | None = None
        replay_start_tip = master_tip
        steps: list[dict] = []
    else:
        # Design decision (the plan designates no ref for this specific
        # correspondence -- only a per-branch fork-point ref, see below):
        # derive the master-tip history-master last agreed with as the
        # merge-base between history-master's tip and master's current tip.
        # history-master's "master-derived" commits are literal, un-rebased
        # copies of master's own commits until master's history is itself
        # rewritten upstream, so this merge-base is exactly the fork point
        # that needs replaying forward.
        old_master_sha = git_ops.merge_base(old_history_sha, master_tip, cwd)
        if old_master_sha is None:
            raise HistoryMasterError(
                f"{branches.history_name(main_branch)} and {main_branch} share no common ancestor"
            )
        if old_master_sha == master_tip:
            replay_start_tip = old_history_sha
            steps = []
        else:
            replay_start_tip = master_tip
            steps = []
            for sha in _first_parent_chain_reverse(f"{old_master_sha}..{old_history_sha}", cwd):
                if is_base_merge(sha, cwd):
                    steps.append({"kind": "base_merge", "sha": sha})
                else:
                    steps.append({"kind": "commit", "sha": sha})

    # --- newly-merged clean-branch detection ---
    normal_detected = find_newly_merged_clean_branches(old_master_sha, master_tip, cwd)
    combined: dict[str, str] = {sha: branch for sha, branch in normal_detected}

    if force_merge:
        wanted = set(force_merge)
        for sha, branch in find_newly_merged_clean_branches(None, master_tip, cwd):
            if branch in wanted:
                combined[sha] = branch

    master_order = git_ops.rev_list_reverse(master_tip, cwd)
    order_index = {sha: idx for idx, sha in enumerate(master_order)}
    merged_branches = sorted(combined.items(), key=lambda item: order_index.get(item[0], len(master_order)))

    planned_markers: set[str] = set()
    for clean_sha, branch_name in merged_branches:
        if clean_sha in planned_markers:
            continue
        if old_history_sha is not None and has_merge_marker(old_history_sha, clean_sha, cwd):
            continue

        history_branch_name = branches.history_name(branch_name)
        history_branch_tip = git_ops.rev_parse(f"refs/heads/{history_branch_name}", cwd)
        if history_branch_tip is not None:
            fork_point = git_ops.rev_parse(branches.history_fork_point_ref(branch_name), cwd)
            if fork_point is None:
                # Fallback when (A)'s fork-point ref hasn't been written for
                # this branch (older branch, or (A) hasn't run yet): the plan
                # reserves that ref for (A) to write, not for (C) to read, but
                # (C) has no other source of truth for "unique to this
                # branch's history" commits without it. merge-base against
                # the pre-run history-master tip is the best available
                # approximation.
                fork_point = git_ops.merge_base(history_branch_tip, old_history_sha or master_tip, cwd)
            if fork_point and fork_point != history_branch_tip:
                for sha in git_ops.rev_list_reverse(f"{fork_point}..{history_branch_tip}", cwd):
                    steps.append({"kind": "commit", "sha": sha})

        steps.append({"kind": "marker", "clean_sha": clean_sha, "branch": branch_name})
        planned_markers.add(clean_sha)

    return steps, replay_start_tip, old_master_sha


def _pending_git_marker(pending: dict) -> str:
    return "CHERRY_PICK_HEAD" if pending["kind"] == "cherry-pick" else "MERGE_HEAD"


def _pending_git_op_missing(pending: dict, repo_root: Path) -> bool:
    """True if the state file claims a pending cherry-pick/merge but git shows
    no such operation actually in progress -- e.g. someone ran a raw
    `git cherry-pick --abort` by hand instead of going through this tool's
    own --abort, leaving the state file orphaned (observed live in the wild:
    see ai/°base/errors/18.md).
    """
    return not (repo_root / ".git" / _pending_git_marker(pending)).exists()


def _do_abort(repo_root: Path) -> dict:
    state = _read_state(repo_root)
    if state is None:
        logger.info("nothing to abort (no update-history-master run in progress)")
        return {"status": "no-op", "detail": "no update-history-master run is in progress"}
    pending = state.get("pending")
    if pending is not None:
        if _pending_git_op_missing(pending, repo_root):
            logger.info(
                "state file says a %s was pending, but git shows none in progress "
                "(already resolved/aborted outside the tool) -- clearing stale state only",
                pending["kind"],
            )
        elif pending["kind"] == "cherry-pick":
            logger.debug("$ git cherry-pick --abort")
            git_ops.cherry_pick_abort(repo_root)
        elif pending["kind"] == "merge":
            logger.debug("$ git merge --abort")
            git_ops.merge_abort(repo_root)
    _cleanup_scratch(repo_root)
    _restore_checkout(state.get("original_checkout"), repo_root)
    _clear_state(repo_root)
    logger.info("aborted; state cleared")
    return {"status": "aborted"}


def _do_continue(repo_root: Path, history_ref: str) -> dict:
    state = _read_state(repo_root)
    if state is None:
        raise HistoryMasterError("No update-history-master run is in progress to continue.")

    cwd = repo_root
    tip = state["tip"]
    remaining = state["remaining"]
    pending = state.get("pending")

    if pending is not None and _pending_git_op_missing(pending, repo_root):
        marker = _pending_git_marker(pending)
        raise HistoryMasterError(
            f"Saved state says a {pending['kind']} is still pending, but git shows no "
            f"{marker} in progress (most likely someone ran a raw `git {pending['kind']} "
            "--abort` by hand instead of this tool's own --abort). Run "
            "`update-history-master --abort` to clear this stale state, then re-run normally "
            "(nothing already applied is lost -- the target ref is only ever moved once the "
            "whole plan finishes)."
        )

    if pending is not None:
        step = pending["step"]
        if pending["kind"] == "cherry-pick":
            logger.debug("$ git cherry-pick --continue")
            result = _log_completed(git_ops.cherry_pick_continue(cwd))
            if result.returncode != 0:
                _write_state(repo_root, state)
                return {"status": "conflict", "pending": pending, "detail": result.stderr or result.stdout}
            tip = _head_sha(cwd)
            _cleanup_scratch(cwd)
        elif pending["kind"] == "merge":
            conflicted = _conflicted_paths(cwd)
            if conflicted:
                return {"status": "conflict", "pending": pending, "detail": f"still conflicted: {conflicted}"}
            if step["kind"] != "base_fold":
                # recreate_base_merge() auto-resolves conflicts itself and
                # never raises MergeConflict, so a pending "merge" whose step
                # isn't a base_fold should be unreachable.
                raise HistoryMasterError(f"unexpected pending merge step kind {step['kind']!r}")
            tip = _complete_base_fold(step["sha"], cwd)
        else:
            raise HistoryMasterError(f"unknown pending kind {pending['kind']!r}")
        remaining = remaining[1:]

    new_tip, still_remaining, conflict = _run_steps(remaining, tip, cwd)
    if conflict is not None:
        _write_state(repo_root, {**state, "remaining": still_remaining, "tip": new_tip, "pending": conflict})
        return {"status": "conflict", "pending": conflict}

    base_sha = git_ops.rev_parse(BASE_REMOTE_REF, cwd)
    if base_sha is not None and not git_ops.is_ancestor(base_sha, new_tip, cwd):
        try:
            new_tip = _fold_base(base_sha, new_tip, cwd)
        except MergeConflict as exc:
            pending = {"kind": "merge", "step": {"kind": "base_fold", "sha": base_sha}, "message": str(exc)}
            _write_state(repo_root, {**state, "remaining": [], "tip": new_tip, "pending": pending})
            return {"status": "conflict", "pending": pending}

    original_sha = state.get("original_sha")
    history_ref_short = history_ref.removeprefix("refs/heads/")
    _refuse_if_checked_out_dirty(history_ref_short, cwd)
    git_ops.move_ref(history_ref, new_tip, original_sha, cwd)
    _sync_checkout_if_current(history_ref_short, new_tip, cwd)
    _restore_checkout(state.get("original_checkout"), cwd)
    _clear_state(repo_root)
    logger.info("%s -> %s (resumed)", history_ref_short, new_tip[:8])
    return {"status": "ok", "history_master": new_tip}


def update_history_master(
    *,
    repo_root: Path,
    main_branch: str,
    force_merge: list[str] | None = None,
    pull_master: bool = False,
    pull_base: bool = False,
    yes: bool = False,
    continue_: bool = False,
    abort: bool = False,
    dry_run: bool = False,
) -> dict:
    force_merge = list(force_merge or [])
    cwd = repo_root
    history_ref_name = branches.history_name(main_branch)
    history_ref = f"refs/heads/{history_ref_name}"

    if abort:
        return _do_abort(repo_root)

    if continue_:
        return _do_continue(repo_root, history_ref)

    if _read_state(repo_root) is not None:
        raise HistoryMasterError(
            "A previous update-history-master run is mid-conflict. "
            "Run with --continue to resume, or --abort to cancel."
        )

    # Every replay step runs through the scratch branch and leaves HEAD
    # detached when done (see _checkout_scratch/_cleanup_scratch) -- capture
    # whatever was checked out before we start so it can be restored at the
    # end (or, if this run conflicts, persisted into the state file so a
    # later --continue/--abort invocation can still restore it).
    original_checkout = _current_checkout(cwd)

    if pull_master or yes:
        _pull_master(cwd, main_branch)
    elif _prompt_yes_no(f"Pull {main_branch}?"):
        _pull_master(cwd, main_branch)

    if pull_base or yes:
        _pull_base(cwd)
    elif _prompt_yes_no("Pull base?"):
        _pull_base(cwd)

    master_tip = git_ops.rev_parse(main_branch, cwd)
    if master_tip is None:
        raise HistoryMasterError(f"branch {main_branch!r} does not exist")

    old_history_sha = git_ops.rev_parse(history_ref, cwd)
    first_run = old_history_sha is None

    steps, replay_start_tip, _old_master_sha = _build_plan(
        cwd=cwd,
        main_branch=main_branch,
        master_tip=master_tip,
        old_history_sha=old_history_sha,
        force_merge=force_merge,
    )
    logger.info("planned %d step(s) (%s)", len(steps), "first run" if first_run else f"replaying onto {history_ref_name}")

    if dry_run:
        return {
            "dry_run": True,
            "first_run": first_run,
            "steps": steps,
            "merged_branches": [step["branch"] for step in steps if step["kind"] == "marker"],
        }

    tip, remaining, conflict = _run_steps(steps, replay_start_tip, cwd)
    if conflict is not None:
        _write_state(
            repo_root,
            {
                "remaining": remaining,
                "tip": tip,
                "force_merge": force_merge,
                "original_sha": old_history_sha,
                "pending": conflict,
                "original_checkout": original_checkout,
            },
        )
        return {"status": "conflict", "pending": conflict}

    base_sha = git_ops.rev_parse(BASE_REMOTE_REF, cwd)
    base_merge_result = None
    if base_sha is not None and not git_ops.is_ancestor(base_sha, tip, cwd):
        try:
            tip = _fold_base(base_sha, tip, cwd)
            base_merge_result = tip
        except MergeConflict as exc:
            pending = {"kind": "merge", "step": {"kind": "base_fold", "sha": base_sha}, "message": str(exc)}
            _write_state(
                repo_root,
                {
                    "remaining": [],
                    "tip": tip,
                    "force_merge": force_merge,
                    "original_sha": old_history_sha,
                    "pending": pending,
                    "original_checkout": original_checkout,
                },
            )
            return {"status": "conflict", "pending": pending}

    if first_run:
        git_ops.create_branch(history_ref, tip, cwd)
    else:
        _refuse_if_checked_out_dirty(history_ref_name, cwd)
        git_ops.move_ref(history_ref, tip, old_history_sha, cwd)
        _sync_checkout_if_current(history_ref_name, tip, cwd)

    _restore_checkout(original_checkout, cwd)

    logger.info("%s -> %s (%s)", history_ref_name, tip[:8], "created" if first_run else "updated")
    return {
        "status": "ok",
        "history_master": tip,
        "first_run": first_run,
        "merged_branches": [step["branch"] for step in steps if step["kind"] == "marker"],
        "base_merge": base_merge_result,
    }
