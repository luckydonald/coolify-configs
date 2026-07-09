# Restore the original checkout after update-history-master finishes

## Context

While finishing the previous fix (`ai/°base/errors/18.md`, already committed as `ebf9b19`), the
`ssp` repo was found left on a **detached HEAD** after a successful `update-history-master` run
(and after the `--abort` that preceded it) — not because of anything new, but because the tool has
never restored a real branch checkout once it's done.

Root cause, confirmed by reading `scripts/°base/git/°split_lib/history_master.py` in full: the
tool drives essentially all of its work through a scratch branch (`_base_split_scratch`).
`_checkout_scratch` (153-163) always starts with `git checkout --detach HEAD`, and `_cleanup_scratch`
(165-173) always ends by detaching HEAD again at the just-built tip before deleting the scratch
branch. This runs once per replay step (`_execute_step` → `replay_commit`/`recreate_base_merge`/
`_fold_base`), and also inside `_do_continue` and `_do_abort`.

The only checkout-restoring logic that exists, `_sync_checkout_if_current` (210-223), only fires
for two specific refs: `main_branch` (after a pull, called from `_pull_master`) and `history_ref`
(after the final `move_ref`, called from the tails of `update_history_master` and `_do_continue`).
Both checks compare against `branch --show-current`, which is always empty by the time they run
(HEAD is detached from the last `_cleanup_scratch`) — so **neither ever actually re-checks-out a
branch**; at best they happen to leave HEAD detached at a SHA that coincidentally matches the ref
they were trying to sync. `_do_abort` has no restoration logic at all.

Net effect, confirmed by tracing every path: a **fresh run**, **`--continue`**, and **`--abort`**
all unconditionally leave HEAD detached at the end, regardless of what branch (if any — `master`,
some unrelated third branch, or already detached) was checked out before the command started.
There is zero existing test coverage for this (every existing test only ever checks out
`main_branch` or `history_ref` before calling the tool, per `test_git_split_history_master.py` and
`test_git_split_recovery.py`).

**Goal:** whatever branch (or detached state) was checked out when the command started, restore it
when the command finishes — for a fresh run, for `--continue`, and for `--abort` — without
disturbing the existing `_sync_checkout_if_current`/`_refuse_if_checked_out_dirty` logic (which
already correctly handles the narrower "main_branch/history_ref itself was checked out" case and
is tested).

## Fix

**`scripts/°base/git/°split_lib/history_master.py`**

1. Add two small helpers near `_refuse_if_checked_out_dirty`/`_sync_checkout_if_current`:
   - `_current_checkout(cwd) -> str | None`: runs `git symbolic-ref --short -q HEAD` (not via the
     `check=True` `_git` helper, since a non-zero exit is the expected "already detached" case, not
     an error); returns the stripped branch name, or `None` if detached.
   - `_restore_checkout(original: str | None, cwd) -> None`: no-op if `original is None`; otherwise
     best-effort `git checkout <original>` (log a warning via `logger.warning` and continue, don't
     raise, if it fails — e.g. the branch was deleted meanwhile) so a cosmetic restore can never
     turn a successful split into a failed command.

2. In `update_history_master`, capture `original_checkout = _current_checkout(cwd)` once, at the
   very top of the function, before the `if abort:` / `if continue_:` dispatch (so it's available
   to all three paths uniformly).

3. **Fresh run:** thread `original_checkout` into every `_write_state(...)` call made on a conflict
   (three call sites: the main replay conflict, and the two `MergeConflict` catches around
   `_fold_base`) as a new `"original_checkout"` key, so a later, separate `--continue`/`--abort`
   process can still recover it. On success, call `_restore_checkout(original_checkout, cwd)` as
   the last thing before the final `return`.

4. **`_do_continue`:** read `original_checkout = state.get("original_checkout")` up front. Persist
   it forward in every `_write_state(...)` call that re-persists a still-pending conflict (so a
   third invocation can still find it). On success, call `_restore_checkout(original_checkout, cwd)`
   before the final `return`.

5. **`_do_abort`:** read `original_checkout = state.get("original_checkout")` before `_clear_state`
   wipes it, and call `_restore_checkout(original_checkout, cwd)` after `_cleanup_scratch`.

This is additive (a new key, two new small helpers, one call each at three exit points) — it
doesn't touch `_checkout_scratch`/`_cleanup_scratch`'s per-step detach/reattach dance (still needed
internally), and doesn't replace `_sync_checkout_if_current`/`_refuse_if_checked_out_dirty` (still
run first, unchanged, for the main_branch/history_ref-specific cases).

## Test coverage

In `test_git_split_history_master.py`:
- A test that checks out an unrelated third branch (neither `main_branch` nor `history_ref`)
  before calling `update_history_master` with ≥1 real replay step, and asserts `git branch
  --show-current` equals that branch afterward (not detached).
- A test that starts already detached (no branch checked out at all) before calling
  `update_history_master`, and asserts HEAD is still detached afterward (not surprise-checked-out
  onto some branch) — i.e. `_current_checkout` returning `None` is preserved, not treated as "do
  nothing" in a way that silently leaves detached-forever as the default good outcome without it
  being verified.
- A test exercising the conflict → `--continue` path with a third branch checked out at the start,
  confirming the original checkout survives the round trip through the persisted state file.
- A test exercising `--abort` the same way.

## Verification

- Run the full `scripts/°base` test suite (`uv run --project scripts/°base python -m unittest
  discover -s scripts/°base/tests`) — must stay green, including the existing
  `CheckoutSyncTests` class.
- Manually reproduce the original bug in a throwaway repo (checkout `master`, seed a divergent
  `ai/history/master`, run `update-history-master --yes` so at least one real replay step
  executes) and confirm `git branch --show-current` is `master` afterward, not empty/detached.
