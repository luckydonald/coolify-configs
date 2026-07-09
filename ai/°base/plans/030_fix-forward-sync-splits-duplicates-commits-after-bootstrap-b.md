# Fix: forward sync-splits duplicates commits after bootstrap-branch

## Context

Confirmed during end-to-end testing of the `get-base.py` auto-mode: running `bootstrap-branch feature` (builds `ai/UNCLEAN/feature` from an existing clean-only `feature` branch) and then a forward `sync-splits feature --direction=to-clean-history` on the same branch duplicates `feature`'s pre-existing commits onto `clean` instead of recognizing they're already there.

**Root cause** (in `scripts/°base/git/°split_lib/`):
- Forward direction (`sync_splits.py`) tags every commit it creates on `clean`/`history` with `X-Base-Split-Source: <unclean-sha>`, and finds its resume point (`find_last_synced_source`) by reading that trailer off `clean`'s/`history`'s current tip.
- Reverse direction (`sync_unclean.py`, used by `bootstrap.py`/`bootstrap-branch`) tags every commit it creates on `ai/UNCLEAN/{branch}` with a *different* trailer, `X-Base-Unclean-Reconstructed-From` (`sync_unclean.py:41`, `RECON_TRAILER`), whose value is either a genuine unclean-source sha (normal round-trip) or, in the bootstrap case, the sha of the **clean commit it was rebuilt from** (since every clean commit lands in the "unmatched" bucket when no `history` exists yet).
- After bootstrap, `clean`'s own tip is the original, untouched human commit — it has no `X-Base-Split-Source` trailer at all (it was never touched by the forward direction). So `find_last_synced_source(clean_ref)` returns `None`, and `commits_to_replay` falls back to replaying *all* of unclean's commits bounded only by `merge-base(unclean, main_branch)` — which includes the commits that `reconstruct_unclean` just mirrored from `clean` itself, producing duplicates.

This only affects the *first* forward sync after a bootstrap (or after any reconstruction that had to fall back to the "unmatched" bucket). Once the forward direction has run once and produced properly-tagged `X-Base-Split-Source` commits, normal round-trips are unaffected — confirmed via existing test coverage (`test_git_split_sync_unclean.py`'s matched-pair cases already correlate correctly through `X-Base-Split-Source`, since `bucket_commits`/`_key_for_info` in `sync_unclean.py` try that trailer *first* before falling back to unmatched).

## Fix

Make `sync_splits.py`'s cursor-finding fall back to recognizing the reverse direction's correlation trailer, so it can resume from the right point on the very first forward run after a bootstrap/reconstruction, instead of only ever trusting `clean`'s/`history`'s own tip.

- **`sync_splits.py`**: add `find_reconstruction_correlated_cursor(unclean_ref, target_ref, cwd) -> str | None`: walk `unclean_ref`'s commits newest-first (`git log --format=%H`, or reuse `git_ops.rev_list_reverse` + iterate reversed), and for each read its `X-Base-Unclean-Reconstructed-From` trailer (import the trailer name from `sync_unclean.RECON_TRAILER` rather than duplicating the string literal — confirmed no circular import risk, `sync_unclean.py` doesn't import `sync_splits.py`). Return the **first (newest) unclean sha** whose trailer value resolves to a real local commit (`git_ops.rev_exists`) that is `target_ref`'s current tip **or an ancestor of it** (`git_ops.is_ancestor`) — ancestor-or-equal, not exact-tip-only, so the fix also holds if `target_ref` has advanced with further legitimately-already-covered commits since. Stop at the first match (newest first) since that's the furthest-forward valid resume point.
- **`sync_branch`**: for each of the clean/history passes, only invoke this fallback when the primary `find_last_synced_source` returns `None` *and* `target_ref` already has commits beyond its own base (i.e. there's something to potentially skip) — call `find_reconstruction_correlated_cursor(unclean_ref, clean_ref, cwd)` / `(unclean_ref, history_ref, cwd)` and use its result as `last_synced_source` if found, otherwise keep the existing `None` behavior unchanged (full replay bounded by merge-base, as today — correct for a genuinely fresh branch with no reconstruction history at all).
- This is self-healing: once one forward run succeeds past the bootstrap point, every subsequent run's commits carry proper `X-Base-Split-Source` trailers and the fallback is never needed again for that branch.
- No changes needed in `sync_unclean.py`/`bootstrap.py` — the reverse direction's tagging is already correct and sufficient; this fix only teaches the forward direction to read it.

## Tests

Add to `scripts/°base/tests/test_git_split_sync_splits.py`:
- `test_forward_sync_after_bootstrap_reconstruction_does_not_duplicate`: real temp repo, a clean `feature` branch with 2 real commits (no `ai/UNCLEAN`/`ai/history` yet), hand-construct the same reconstruction a `bootstrap_branch` call would produce (either by importing and calling `°split_lib.bootstrap.bootstrap_branch` directly, matching how `test_git_split_bootstrap.py` already does it, or by calling `sync_unclean.reconstruct_unclean` directly — prefer the former since it's the real end-to-end path that triggered the bug). Then run `sync_splits.sync_branch(...)` and assert `clean`'s commit count is unchanged (no duplicates), i.e. `git rev-list --count master..feature` before and after are equal.
- `test_find_reconstruction_correlated_cursor_finds_newest_match`: more targeted unit-level test directly hand-crafting an unclean commit with a `RECON_TRAILER` pointing at `clean`'s tip, asserting the helper returns that sha.
- `test_find_reconstruction_correlated_cursor_returns_none_when_no_match`: sanity check for the fresh-branch case (no regression to existing behavior).

## Verification

1. `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v` — all pass.
2. Re-run the exact repro from today's session: scratch repo, clean `feature` branch with commits, `bootstrap-branch feature`, then `sync-splits feature --direction=to-clean-history` — assert `git log --oneline feature` has no duplicated "add x"-style commits, i.e. its commit count matches `feature`'s commit count before bootstrap plus only genuinely new unclean commits (if any were added in between).
