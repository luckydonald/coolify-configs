In the repo /home/user/git/luckydonald/base, I need to understand a git-branch-splitting tool and its error-recovery workflow.

Please investigate and report back (concisely, with file paths):

1. Read /home/user/git/luckydonald/base/ai/°base/AGENTS.md in full and summarize the guidance relevant to: (a) the "errors" directory convention (e.g. ai/°base/errors/*.md) — what these files are, when/how they get created, and what "fixing" one means (is there a documented process, e.g. archiving it, moving it elsewhere, marking it resolved?); (b) any documented recovery procedure for conflicts during `split.py ... update-history-master`.

2. Look at other files in ai/°base/errors/ (if any exist besides 18.md) to see if there's a pattern for how past errors were "fixed" — e.g. check git log for commits that touched files in that directory, especially the recent commit "15b9685 [base] ai: Unused error files." — show what that commit actually did (git show 15b9685).

3. Find and read the script referenced in the error log: scripts/°base/git/split.py (or scripts/°base/git/get-base.py). Focus specifically on:
   - The `update-history-master` subcommand/command logic
   - How it handles a cherry-pick conflict (the error shows `{'status': 'conflict', 'pending': {'kind': 'cherry-pick', 'step': {'kind': 'commit', 'sha': '7afd08be7fc178abd1744706c781b583ce6f69d9'}}}`)
   - Whether there's a recovery/resume mechanism, a `--continue` flag, or documented manual recovery steps (e.g. resolve conflict then re-run, or run the printed `git update-ref` shell commands to reset state)
   - Note: the shell block of `git update-ref` commands printed in the error log — is this meant to be run to roll back to "before" state, or is it informational/for reference for recovering old branches?

4. Check if there's any README or docs describing what the `refs/base-split/...` namespaced refs are for (history-master-fork-point, unclean-cursor/clean, unclean-cursor/history) — this matters because the user wants instructions on "how to recover the old branches" which I believe refers to recovering the original branch state that existed before the split.py tool ran and modified branches like `master`, `ai/history/master`, `feature/...`, etc. via git update-ref.

Report back all relevant file contents/excerpts and your findings, organized by these 4 numbered points. Include full paths and line numbers where useful.