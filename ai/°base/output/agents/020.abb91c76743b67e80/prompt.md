In /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/, read `git_ops.py`'s `cherry_pick_abort`, `merge_abort`, and `cherry_pick_continue` functions in full (exact code), and read `history_master.py`'s `_cleanup_scratch` function in full.

Specifically answer:
1. Does `cherry_pick_abort(cwd)` raise/error if there is NO cherry-pick actually in progress (no `.git/CHERRY_PICK_HEAD`)? Show exactly what `git cherry-pick --abort` returncode handling looks like in that function (does it check returncode and raise, or ignore failures like the `|| true` pattern used in recovery.py's ABORT_COMMANDS?).
2. What does `_cleanup_scratch` do exactly (which branch does it check out, does it delete `_base_split_scratch`, does it handle the case where the current checkout IS `_base_split_scratch`)?
3. Read `_read_state`/`_write_state`/`_clear_state` in history_master.py once more and confirm exactly what path the state file lives at.

Report exact code snippets with file:line citations for all of the above — no paraphrasing of the actual logic/conditionals, quote it.