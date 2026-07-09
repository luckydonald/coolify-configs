# Findings: `history_master.py` scratch-branch / checkout-restore analysis

File read in full: `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/history_master.py` (818 lines).

## 1. Plain `update_history_master(..., yes=True)` with ≥1 replay step — does anything restore the original branch?

**No.** Tracing the exact call order for a fresh run (not `--continue`/`--abort`) that has at least one step to replay and no conflicts:

1. `update_history_master` (707) — `abort` false (724), `continue_` false (727), `_read_state` is `None` (730).
2. `_pull_master(cwd, main_branch)` (737) / `_pull_base(cwd)` (742) — since `yes=True`. Inside `_pull_master` (226-238), `_refuse_if_checked_out_dirty(main_branch, cwd)` (235) and `_sync_checkout_if_current(main_branch, remote_sha, cwd)` (237) are only keyed to `main_branch`. If the user's checked-out branch is some unrelated third branch, `branch --show-current` never equals `main_branch`, so these are no-ops with respect to the checkout.
3. `master_tip = git_ops.rev_parse(main_branch, cwd)` (746), `old_history_sha = git_ops.rev_parse(history_ref, cwd)` (750).
4. `_build_plan(...)` (753) — pure plumbing/planning, touches no working tree/checkout.
5. `_run_steps(steps, replay_start_tip, cwd)` (770) → for each step, `_execute_step` (459) dispatches to e.g. `replay_commit` (288), `recreate_base_merge` (309), `_fold_base` (372), or `_create_merge_marker` (404, pure plumbing, no checkout). The non-marker ones each call `_checkout_scratch(onto, cwd)` then, at the end, `_cleanup_scratch(cwd)`. `_checkout_scratch` (153-163) detaches HEAD, deletes/recreates `_base_split_scratch`, checks it out. `_cleanup_scratch` (165-173) detaches HEAD again (to the just-built tip sha) and deletes the scratch branch. **At no point does either helper, or anything calling them, look up or re-checkout the branch that was checked out before the run started.**
6. After all steps succeed, optional base-fold (784-802) — same `_checkout_scratch`/`_cleanup_scratch` pattern via `_fold_base`/`_complete_base_fold`, ending detached again.
7. Tail (804-809): if `first_run`, `git_ops.create_branch(history_ref, tip, cwd)` (805) — pure ref creation, doesn't touch HEAD/checkout. Otherwise `_refuse_if_checked_out_dirty(history_ref_name, cwd)` (807) then `git_ops.move_ref(...)` (808) then `_sync_checkout_if_current(history_ref_name, tip, cwd)` (809) — both keyed to `history_ref_name`; since HEAD is detached at this point (`branch --show-current` returns `""`), neither matches, so no checkout happens here either.

**Definitive end state:** HEAD is left **detached**, pointing at the final replayed tip sha (whatever the last `_cleanup_scratch`/`_complete_base_fold` left it at). The user's original unrelated branch (e.g. `master`-the-third-branch, or any branch that isn't `main_branch`/`history_ref`) is never re-checked-out — its ref is untouched, but the working tree/HEAD no longer points at it.

## 2. `--continue` (`_do_continue`) and `--abort` (`_do_abort`) — same story?

**Yes, both also leave HEAD wherever scratch cleanup left it; neither restores an original checkout.**

- `_do_abort` (613-635): after aborting any pending cherry-pick/merge, it unconditionally calls `_cleanup_scratch(repo_root)` (632), which detaches HEAD at the current tip and deletes the scratch branch — same as above, no restoration logic at all.
- `_do_continue` (638-704): resumes the pending step (cherry-pick-continue at 663-668, or `_complete_base_fold` at 678, which itself ends with `_cleanup_scratch` at line 368), then calls `_run_steps` again (683) for any remaining steps (same `_checkout_scratch`/`_cleanup_scratch` per step), then the tail (697-704) does `_refuse_if_checked_out_dirty(history_ref_short, cwd)` (699), `git_ops.move_ref(...)` (700), `_sync_checkout_if_current(history_ref_short, new_tip, cwd)` (701) — again only keyed to `history_ref`, and HEAD is detached by that point, so it never matches and never restores anything.

Both paths end with HEAD **detached**, exactly like the fresh-run path.

## 3. Existing tests: does any check out a third/unrelated branch (or none/detached) beforehand and assert post-run state?

**No such test exists** in either file.

- `test_git_split_history_master.py`: `setUp` (23-28) calls `init_repo(self.repo_root)` which does `git init -b master` (per `_git_test_helpers.init_repo`, `branch: str = "master"`), so the very first checked-out branch is always `"master"`, which is always also the `main_branch` value passed to every call in this file. Every test that switches checkout before calling `update_history_master` always ends up back on either `"master"` (`main_branch`) or `"ai/history/master"` (`history_ref`) — see lines 69/77, 128/141, 221/232, 295/298, 392-398 (`CheckoutSyncTests.test_history_ref_checkout_stays_in_sync_after_replay`, deliberately checks out `history_ref` right before calling to test the sync helper), 364, 377, 414/416, 424/426. None ever checks out a third branch name.
- `test_git_split_recovery.py`: `git(["checkout", "master"], ...)` appears at 58, 60, 116, 174, 200 — same pattern, always `master`/`main_branch`, or `history_ref` (167). None asserts on final branch/HEAD state for an unrelated branch; the one full-flow test with checkout switching (159-195) only asserts on console/log output, not on the final checked-out ref.

So there is currently **zero test coverage** for "user has some unrelated branch checked out" → nothing exercises or pins today's detached-HEAD-at-end behavior for that scenario.

## 4. Exact current code bodies

**`_checkout_scratch`** (lines 153-163):
```python
def _checkout_scratch(onto: str, cwd: Path) -> None:
    """(Re)create the scratch branch at `onto` and check it out.

    Detaches HEAD first so this is safe to call even when currently sitting
    on a stale scratch branch from a previous (finished) step.
    """
    _git(["checkout", "--detach", "HEAD"], cwd)
    _delete_ref(SCRATCH_REF, cwd)
    git_ops.create_branch(SCRATCH_REF, onto, cwd)
    git_ops.checkout_branch(SCRATCH_BRANCH, cwd)
```

**`_cleanup_scratch`** (lines 165-173):
```python
def _cleanup_scratch(cwd: Path) -> None:
    """Detach off the scratch branch and delete it. Only call this once a
    step has *cleanly* finished -- never while a conflict is still open,
    since resuming (--continue) needs the scratch branch's mid-operation
    state (CHERRY_PICK_HEAD/MERGE_HEAD) to still be there.
    """
    tip = _head_sha(cwd)
    _git(["checkout", "--detach", tip], cwd, check=True)
    _delete_ref(SCRATCH_REF, cwd)
```

**`_refuse_if_checked_out_dirty`** (lines 191-207):
```python
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
```

**`_sync_checkout_if_current`** (lines 210-223):
```python
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
```

**`_do_abort`** (lines 613-635):
```python
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
    _clear_state(repo_root)
    logger.info("aborted; state cleared")
    return {"status": "aborted"}
```

**`_do_continue`** (lines 638-704):
```python
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
    _clear_state(repo_root)
    logger.info("%s -> %s (resumed)", history_ref_short, new_tip[:8])
    return {"status": "ok", "history_master": new_tip}
```

**Tail of `update_history_master`, from right after `_run_steps` succeeds through the final `return`** (lines 770-818):
```python
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
                },
            )
            return {"status": "conflict", "pending": pending}

    if first_run:
        git_ops.create_branch(history_ref, tip, cwd)
    else:
        _refuse_if_checked_out_dirty(history_ref_name, cwd)
        git_ops.move_ref(history_ref, tip, old_history_sha, cwd)
        _sync_checkout_if_current(history_ref_name, tip, cwd)

    logger.info("%s -> %s (%s)", history_ref_name, tip[:8], "created" if first_run else "updated")
    return {
        "status": "ok",
        "history_master": tip,
        "first_run": first_run,
        "merged_branches": [step["branch"] for step in steps if step["kind"] == "marker"],
        "base_merge": base_merge_result,
    }
```

## 5. Where to capture, and does it need persisting for a separate `--continue`/`--abort` process?

Yes to both.

- **Capture point:** the top of `update_history_master`, before the `if abort:` / `if continue_:` dispatch. Currently the function body starts at line 719 (`force_merge = list(force_merge or [])`) through line 728 (the `continue_` branch). The natural spot is right at entry (e.g. inserted around line 719-722, before line 724's `if abort:`), using something like `git symbolic-ref --short -q HEAD` in `cwd` — this returns the short branch name with exit 0 when on a branch, and empty stdout with a non-zero exit code when HEAD is already detached (distinguishing "was on branch X" from "was already detached," so a fresh run's capture can also serve `_do_abort`/`_do_continue`'s need to know the pre-run checkout, and detached-at-start is representable as `None`/empty rather than crashing or being conflated with a real branch name).
- **Persistence requirement:** since `--continue` and `--abort` are typically invoked as a **separate process** later (they call `_read_state`/`_do_continue`/`_do_abort` independently, at lines 638-639 and 613-614, with no access to any in-memory value from the original run), the captured value must be written into the JSON state file via `_write_state` (260-261) alongside the existing keys. Today `_write_state` is called with dicts containing `remaining`, `tip`, `force_merge`, `original_sha`, `pending` (e.g. lines 772-781, 792-801, and inside `_do_continue` at 685/694). A new key (e.g. `"original_checkout"`) would need to be added to each of those three `_write_state` call sites so that `_do_continue` (638) and `_do_abort` (613) — reading `state` back via `_read_state` — can retrieve it and use it to restore the original checkout once their own scratch-cleanup/ref-move logic finishes.