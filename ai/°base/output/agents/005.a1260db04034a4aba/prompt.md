Repo root: /home/user/git/luckydonald/base

I'm planning a new feature: a tool to split a git branch into "clean" (no AI/base mentions), "unclean" (ai/UNCLEAN/{branch}, the actual working branch), and "history" (ai/history/{branch}, holds AI-only content/metadata) variants, plus a history-master branch concept, sync/rebase commands, and push-protection hooks (block pushing unclean/history branches to origin, block AI content on non-unclean branches, block code content on non-history branches).

Please explore and report back (be thorough, this feeds a real implementation plan):

1. `scripts/°base/git/` directory — list all files/subdirs, read the main entry scripts (whatever CLI framework is used — argparse/click/typer?), and summarize what each existing git-related script/subcommand does (e.g. `scripts/°base/git/hooks/`, `scripts/°base/git/remote/`). Include exact file paths.
2. `scripts/°base/git/hooks/commit/` and `scripts/°base/git/hooks/install/` — what do these do? How are git hooks installed/registered in this repo (pre-push, pre-commit, commit-msg)? Is there an existing pre-push hook, and where would a new push-check hook plug in?
3. Any existing CLI dispatch pattern (is there a single `°base` or `base` command with subcommands like `git ...`? look for a main.py / cli.py / __main__.py under scripts/°base). Show how subcommands are registered (e.g. click groups) with file:line references.
4. Any existing code touching "ai:" auto-commits, `ai/query.md`, `ai/plans/`, decisions, memory — i.e. `scripts/°base/ai/hooks/*` — just enough to understand what "AI content" commits look like structurally (commit message patterns, files touched) since the new tool must detect "ai-only" vs "code" commits.
5. Is there already ANY existing concept of branch splitting, "clean"/"unclean"/"history" branches, or similar multi-branch sync tooling anywhere in the repo (grep for "unclean", "history/", "clean branch", "split")? Check `ai/°base/` docs/AGENTS.md too.
6. What test framework and conventions does `scripts/°base/tests/` use (for writing tests for the new tool later) — just a brief note with file paths.

Report concisely with file:line citations. This is research only — do not modify anything.