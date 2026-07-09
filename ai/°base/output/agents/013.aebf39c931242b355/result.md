All 9 tests pass, confirmed stable on a re-run.

## Summary

Implemented subsystem (B) — reverse "unclean reconstruction" — in exactly two new files, as scoped:

- `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/sync_unclean.py`
- `/home/user/git/luckydonald/base/scripts/°base/tests/test_git_split_sync_unclean.py`

No existing/concurrently-created files were touched (verified via `git status`/`git diff` — the pending `git_ops.py` diff belongs to the parallel agent, not this work; `merge_base` was already present there so no local fallback was needed).

**What's implemented**: cursor refs (`clean_cursor_ref`/`history_cursor_ref`), `CommitInfo`/`read_commit_info`, `bucket_commits` (with the required duplicate-key `ValueError`), `order_key`/`_order_buckets`, `check_order_consistency` (the `force` refusal path), `read_unclean_source_order`, `detect_divergences` (tree-comparison, read-only), `build_merged_commit`, `_rewrite_from` (amend-in-place + replay descendants), and the top-level `reconstruct_unclean` orchestrator.

**Deviations from the plan I had to make (documented in code comments too), since this genuinely needed design decisions the plan left open**:

1. **New internal trailer `X-Base-Unclean-Reconstructed-From`**, stamped by this module on every commit it creates on `ai/UNCLEAN/{branch}`. The plan's fixed schema only covers what the *forward* direction writes on clean/history commits — it gives no way to answer "which current unclean commit did we already build for source-key K" or to derive `unclean_source_order` (the "ground truth" ordering the plan calls for), since a reconstructed `unclean` branch's own commit shas aren't the original source shas. This trailer is exactly the missing side-channel, and it's entirely internal/consumed only by this module.
2. **`build_merged_commit`'s `item` dict** carries an extra `"key"` field beyond the plan's literal `{"clean", "history"}` bucket shape, since the function needs the bucket key to stamp the trailer above.
3. **Divergence detection re-derives from a full scan** of `clean`/`history` (not just cursor-bounded "new" commits), because an already-reconciled pair can diverge with only *one* side amended in place — the other side's info wouldn't appear in a cursor-bounded "new since cursor" fetch at all.
4. **`order_key`'s "matched vs. inf" contrast** is implemented as a `(0, …)` / `(1, …)` leading discriminant tuple rather than comparing `int` against literal `float('inf')`, to keep tuple comparison type-safe; functionally identical.
5. Divergence/tree comparisons compare **whole tree shas** rather than diffing `PathChange` lists, since that's simpler and more robust (avoids blob-sha-vs-content edge cases).

Ran `uv run --project scripts/°base python -m unittest scripts.°base.tests.test_git_split_sync_unclean -v` — all 9 tests pass, covering every scenario from the plan's (B) test list. Also ran the full suite via `unittest discover`: the only failures are pre-existing import errors in two other in-flight files (`test_git_split_history_master.py`, `test_git_split_rebase_to_master.py`) from the concurrent subsystem-(C) work — unrelated to this task, not touched or caused by it.