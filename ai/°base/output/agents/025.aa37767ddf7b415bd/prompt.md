In the repo /home/user/git/luckydonald/base, I'm investigating a bug reported in ai/°base/errors/19.md:

When running `get-base.py | python3 -` in "auto mode" on a fresh consuming repo (no `ai/history/main` ref exists yet), it delegates to `scripts/°base/git/split.py --repo-root <path> bootstrap-branch <branch>`, which fails with:

    feature/PROJ-1234/mr1: 'ai/history/main' does not exist yet -- run update-history-master first.

I need a thorough report on:
1. Where is this error message ("does not exist yet -- run update-history-master first") raised? Find the exact file and function/line.
2. What is `ai/history/main` — a git ref/branch? Where/how is it normally created? Search for "update-history-master" — what script or function is that, what does it do, and is it ever invoked automatically as part of the auto/bootstrap flow, or does it require a manual separate step?
3. Trace the full flow of `scripts/°base/git/get-base.py` in "auto mode" — what does auto mode do, and does it call update-history-master (or equivalent) before calling split.py bootstrap-branch?
4. Look at `scripts/°base/git/split.py`'s `bootstrap-branch` subcommand implementation in detail — what preconditions does it check, and why does it require `ai/history/main` to already exist? Is there existing logic elsewhere in split.py (or a related module) that already knows how to create/update `ai/history/main` from scratch, that could be reused/called automatically here?
5. Check git history / recent commits touching `split.py`, `get-base.py`, or `update-history-master` for context on why this precondition exists (is it intentional safety guard, or just an overlooked wiring gap?).
6. List any existing tests for split.py bootstrap-branch or update-history-master (in scripts/°base/tests), and what they currently cover.

Report file paths with line numbers for everything found. Be thorough — this is for planning a fix, not just a quick pointer.