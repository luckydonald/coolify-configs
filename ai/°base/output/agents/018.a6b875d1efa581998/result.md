Now I have a complete picture. Let me report findings.

## Findings

**1. Tests asserting on `update_history_master()`'s conflict-shape dict**

None exist. `/home/user/git/luckydonald/base/scripts/°base/tests/test_git_split_history_master.py` (405 lines, 10 test methods, all listed below) only exercises the happy path — every assertion is `self.assertEqual(result["status"], "ok")`. There is no test anywhere in the repo that triggers a real conflict and inspects `result["pending"]`, `result["detail"]`, or a `"message"` key. Test names: `test_first_run_creates_history_master_at_master_tip`, `test_idempotent_rerun_is_a_no_op`, `test_subsequent_run_replays_and_preserves_a_prior_merge_marker`, `test_base_merge_recreation_after_master_advances`, `test_master_is_never_mutated_by_a_base_merge`, `test_force_merge_recovery_widens_search_window`, `test_pull_master_keeps_current_checkout_in_sync`, `test_pull_master_refuses_with_dirty_checkout`, `test_history_ref_checkout_stays_in_sync_after_replay`, `test_full_yes_run_pulls_master_replays_and_folds_base_in_order`.

**Conclusion: there is no existing test locking in the current (message-losing) conflict dict shape** — you're free to add a `"message"` (or similar) key to the `{"status": "conflict", "pending": {...}}` dicts returned from `history_master.py` lines 560, 566, 580, 596, 681, 700 without breaking anything in this file.

**2. CLI `_update_history_master` / `cli.main()` stdout-format tests**

None exist either. `test_get_base.py` calls `main(["update-history-master", "--yes"])` (lines 144, 182, 189, 276-278) but only checks the invoked subprocess args (`captured["args"][-2:]`), never captures/asserts on stdout content. No `capsys`/`capfd` usage tied to `update-history-master` anywhere in `scripts/°base/tests/`. So `cli.py`'s `print(result)` at line 153 of `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/cli.py` (the bare `str({...})` dump) is not pinned by any test — safe to change to something more helpful.

**3. `.expected.md` fixtures in `ai/°base/errors/`**

- `/home/user/git/luckydonald/base/ai/°base/errors/18.md` is a **real, unfixed example of exactly the problem** you're about to solve — it's a raw transcript ending with the unhelpful line:
  ```
  {'status': 'conflict', 'pending': {'kind': 'cherry-pick', 'step': {'kind': 'commit', 'sha': '7afd08be7fc178abd1744706c781b583ce6f69d9'}}}
  ```
  There is **no paired `18.expected.md`** — no precedent yet for what the "fixed" output should look like. You'd be establishing the precedent yourself.
- `/home/user/git/luckydonald/base/ai/°base/errors/13.md` + `13.expected.md` is an unrelated fixture (AskUserQuestion/Codex hook-normalization docs) — its "conflict" hits are just the word appearing inside quoted JSON option text, not a git-merge conflict scenario.
- `/home/user/git/luckydonald/base/ai/°base/errors/17.txt` documents an older, unrelated failure (`rebase --exec` + real git `CONFLICT (content)` in `rebase_strip_claude_authorship.py`, not `history_master.py`) — referenced only in `history_master.py`'s module docstring (lines 3-9) as the *reason* that script avoids `git rebase --exec`. No expected-fix fixture pairs with it either.

**4. `CherryPickConflict` / `MergeConflict` exception message tests**

None exist. Both classes are defined in `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/history_master.py` lines 41-72, with message text built from an f-string in `__init__`. A repo-wide grep for `CherryPickConflict`/`MergeConflict` outside `history_master.py` itself found no other references — no test module imports or raises/catches them, and none checks `str(exc)`.

**Root cause you're about to fix**, confirmed in code: `_run_steps()` at history_master.py lines 426-437 does:
```python
except CherryPickConflict:
    return tip, remaining, {"kind": "cherry-pick", "step": step}
except MergeConflict:
    return tip, remaining, {"kind": "merge", "step": step}
```
— the exception is caught without binding (`as exc`), so its carefully-built message (sha, onto, stderr/stdout, recovery instructions) is discarded entirely. Only `kind`/`step` survive into the `"pending"` dict that flows all the way to `cli.py`'s `print(result)`, producing the bare dict repr seen in `ai/°base/errors/18.md`.

**Bottom line for your planned change:** no test in the repo asserts on the current conflict-dict shape, the CLI's stdout format, or exception message text for these paths, so you can safely (a) bind the exception in `_run_steps` and thread its message into the returned `pending`/`conflict` dict, and (b) make `cli.py`'s conflict-branch printing surface that message, without needing to update any existing assertions. Consider adding a fresh `ai/°base/errors/18.expected.md` as the new precedent for "fixed" output, since none currently exists.