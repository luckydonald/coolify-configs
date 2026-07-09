In /home/user/git/luckydonald/base, search ai/°base/todo.md, ai/°base/plans/*.md (especially anything about "update-history-master", "base-merge", "fold", "history-master"), and any comments in scripts/°base/git/°split_lib/history_master.py for how the FIRST-EVER fold of the `base/base` remote into a client repo's `ai/history/<branch>` line is supposed to work.

Specifically I need to know:
1. Is there a documented/intended special case for when `ai/history/master` has NO prior base-merge commits (i.e. this is the very first time base/base gets folded in) vs. subsequent folds? The current code (`_fold_base` in history_master.py, calls `git_ops.merge_no_commit(base_sha, cwd)` which runs `git merge --no-commit --no-ff <sha>`) has no `--allow-unrelated-histories` flag, and git refuses with "fatal: refusing to merge unrelated histories" when there's no common ancestor between the current tip and base_sha.
2. Is `base/base` (the remote pointing at github.com/luckydonald/base, i.e. this very repo) expected to share history with a client's `master`/`ai/history/master`, or is it BY DESIGN a totally separate/unrelated template repo that gets grafted in via an unrelated-histories merge?
3. Any mention of `--allow-unrelated-histories` anywhere in the codebase or docs.
4. Whether `git_ops.merge_no_commit` (frozen/shared plumbing per the module docstring) is the only merge primitive available, or if there's an existing alternate function for allowing unrelated histories.

Report findings with file:line citations. This is research only - do not modify any files.