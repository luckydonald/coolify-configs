# Verify/fix update-history-master's master-pull -> rebase -> merge-history -> base-pull ordering

## Context

The user asked me to confirm `update-history-master --yes` does, in order: 0) work against clean `master`, 1) rebase `ai/history/master`'s own existing commits onto the new `master` tip, 2) merge in other branches' `history` that are now newly available (their clean branch got merged into `master`), 3) pull in and merge the newest `base/base` — all after the previous steps.

**I traced `history_master.py` and confirmed steps 1-3 already execute in exactly this order**, with evidence:
- `_build_plan()` (`history_master.py:403-489`) builds one `steps` list: first the replay of history-master's *own* pre-existing commits onto the new master tip (`history_master.py:439-446`, walking `old_master_sha..old_history_sha` via `_first_parent_chain_reverse`) — step **1** — then, appended to the *same* list, the newly-merged-clean-branch detection and their history-branch replay + marker commits (`history_master.py:448-487`) — step **2**.
- `update_history_master()` runs that combined list via `_run_steps(steps, replay_start_tip, cwd)` (`history_master.py:629`) — i.e. steps 1 and 2 complete — and only *after* that does it fetch-check `base_sha` and fold it in via `_fold_base` (`history_master.py:643-660`) — step **3**, confirmed to run strictly after 1 and 2, never before or interleaved.
- `_pull_base(cwd)` (a `git fetch base`, `history_master.py:173-174`) is called early (before planning) when `pull_base`/`yes` is set, so the "newest" `base/base` tip is known *before* replay begins — but the actual merge/fold of it is still deferred to the end, matching "pull in **and merge**... after previous steps."

**So the ordering itself needs no fix.** But tracing this surfaced a real, untested, practically-significant bug in step 0/the ref-move mechanics: `_pull_master()` (`history_master.py:161-171`) and the final `history_ref` update (`history_master.py:662-665`, and equivalently in `_do_continue`, `history_master.py:562`) both move branch refs via plumbing (`git_ops.move_ref`/`create_branch`, i.e. `git update-ref`) — which **never touches the working tree or index**. If the ref being moved happens to be the *currently checked-out branch* in the repo the tool is running against, the checkout silently goes stale: the ref now points at a new tip, but the working tree/index still reflect the old one, until the user notices and manually re-syncs. Concretely, this bug is hit by the **most common real invocation path**: `get-base.py`'s auto-mode runs `update-history-master --yes` specifically *when you're currently on `master`* (or on `ai/history/master`) — so `main_branch`'s pull-fast-forward, and/or `history_ref`'s own final move, will very often be moving the ref you're actively sitting on.

No existing test exercises this (confirmed via grep: no test in `test_git_split_history_master.py` uses `pull_master=True` with a real diverged `origin`, or checks out `main_branch`/`history_ref` and leaves it checked out across a call to `update_history_master`).

## Fix

Add a small helper that keeps a ref's checkout in sync whenever the tool moves a ref that happens to be currently checked out, refusing rather than clobbering if the working tree isn't clean (mirrors how a real `git rebase`/`git pull` on your current branch would behave):

- **`history_master.py`**: add `_sync_checkout_if_current(ref_short_name: str, new_sha: str, cwd: Path) -> None`:
  1. `current = _git(["branch", "--show-current"], cwd).stdout.strip()` (empty string if detached — never matches, so detached HEAD is left alone, consistent with today's behavior).
  2. If `current != ref_short_name`: no-op (the common case — nothing checked out there, plumbing-only move is correct and lighter-weight, exactly as today).
  3. Else: check `git status --porcelain` is empty; if not, raise `HistoryMasterError` telling the user to commit/stash before running (never silently discard local changes). If clean, `git reset --hard <new_sha>` in `cwd` — safe here specifically *because* we just confirmed there's nothing to lose, and this is the one operation that updates ref + index + working tree together in one step (equivalent to what `git pull --ff-only`/`git rebase` do automatically when you're on the branch being moved — our implementation deliberately avoids driving those directly, per the errors/16-17 precedent, so this one spot needs to do it explicitly instead).
- Call it in two places:
  - Right after `_pull_master`'s `git_ops.move_ref(f"refs/heads/{main_branch}", remote_sha, local_sha, cwd)` (`history_master.py:170`), passing `main_branch`/`remote_sha`.
  - Right after the final `history_ref` update in both `update_history_master` (`history_master.py:662-665`) and `_do_continue` (`history_master.py:562`), passing `history_ref_name`/`tip`.
- No change needed for `_fold_base`/`recreate_base_merge`/`replay_commit`'s own internal scratch-branch checkouts — those already operate on a dedicated scratch branch precisely to avoid ever touching the user's actual checkout mid-flight; this fix only concerns the two points where a *real* branch ref (`main_branch`, `history_ref`) gets its final position moved.

## Tests

Add to `scripts/°base/tests/test_git_split_history_master.py`:
- `test_pull_master_keeps_current_checkout_in_sync`: real repo with a fake `origin` remote (clone pattern, matching the existing `base`-remote test setup) that's ahead of local `master`; check out `master`; call `update_history_master(pull_master=True, ...)`; assert `git status --porcelain` is empty and the working tree's `README.md`-equivalent content matches the new tip afterward (not just the ref).
- `test_pull_master_refuses_with_dirty_checkout`: same setup but with an uncommitted local change; assert the call raises `HistoryMasterError` and neither the ref nor the dirty file are touched.
- `test_history_ref_checkout_stays_in_sync_after_replay`: check out `ai/history/master` itself, advance `master`, run `update_history_master()` (a normal replay, not first-run); assert the working tree matches the new `ai/history/master` tip afterward (this is the case where the "new tip" is *not* a fast-forward of the old one, since replay rebuilds commits — confirms `git reset --hard` handles that correctly too, not just the fast-forward case).
- `test_full_yes_run_pulls_master_replays_and_folds_base_in_order`: the integration test settling the user's original question directly in one assertion-rich test — real `origin` remote ahead of `master`, a previously-merged branch with unreplayed history commits waiting, and a real `base` remote with a new commit, all set up simultaneously; call `update_history_master(pull_master=True, pull_base=True, ...)` (or `yes=True`) once; assert, in order: `master` ends up at `origin`'s tip; the pre-existing history-master commits were replayed (not lost); the newly-available branch's history commits + marker are present; and the base-merge commit is the very last commit reachable, appearing only after all of the above (assert via `git log --first-parent` position, or by checking the base-merge commit's parent is the tip produced by steps 1+2).

## Verification

1. `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v` — all pass.
2. Manual scratch-repo repro of the discovered bug: create a fake `origin`, check out `master` locally behind it, run `update-history-master --yes` from another cwd (or directly), confirm `git status` is clean and file contents match afterward (pre-fix, this would show a stale/conflicting working tree).
