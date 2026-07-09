In /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/, find where the result of `update_history_master()` (which returns dicts like `{"status": "conflict", "pending": {...}}` or `{"status": "ok", ...}`) gets printed/handled in cli.py's `update-history-master` command handler.

Specifically:
1. Show the exact code in cli.py that calls update_history_master and what it does with the returned dict — does it just `print(result)` (raw Python dict repr) or does it format a nice message?
2. Show the `CherryPickConflict` and `MergeConflict` exception classes in history_master.py in full (message text and where raised) — are these exceptions actually raised/caught anywhere, or does the code just return the conflict dict directly without raising/printing that helpful message?
3. Check recovery.py's `_run_with_recovery` wrapper in cli.py (or wherever it lives) — does it catch exceptions and print the recovery block, or is the recovery block printed unconditionally before running, with the "before/after" summary after?
4. Look specifically for how `_do_continue`, guard-if-state-file-exists logic, and the initial state-file-write-on-conflict logic works — trace the exact code path that ran to produce the errors/18.md output (a `--yes` run to `update-history-master` that hit a cherry-pick conflict on commit 7afd08be). Does it currently:
   a. Write the state file for later `--continue`?
   b. Print the CherryPickConflict message (with instructions)?
   c. Or just print the raw `{'status': 'conflict', ...}` dict with NO instructions on what to actually do (which would explain why the user is confused and asking "how do I recover")?

Report exact file:line citations and relevant code snippets (don't paraphrase the actual conflict-handling code — quote it).