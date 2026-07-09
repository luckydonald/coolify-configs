Repo root: /home/user/git/luckydonald/base

I'm designing a new git branch-splitting tool (clean/unclean/history branches for separating AI-assistant content from shipped code) for this "base" monorepo template project. Before designing it, I need full context on how this repo works and documents itself.

Please read and summarize (with file:line citations), specifically:

1. `ai/°base/AGENTS.md` — full contents, summarized. This is supposed to be the main guidance doc for Claude Code in this repo per CLAUDE.md.
2. `ai/°base/todo.md` — current contents (there's a pending uncommitted modification to it per git status, so read the current file state).
3. `ai/°base/decisions/` — list files, read `001_reverse_scope.json` and `002_reverse_scope.json` in full (these are freshly created, untracked, so likely relevant/recent context — maybe about a related "reverse scope" or branch-splitting decision already made).
4. `ai/°base/errors/16.txt` and `17.txt` — already partially known to be about a `rebase_strip_claude_authorship.py` script failing in a project called AllMyStorage that uses this base. Read them fully if not already summarized, and importantly find `scripts/°base/git/rebase_strip_claude_authorship.py` (or similar path) in this repo, read it, and summarize what it does — this seems highly relevant since it already does claude-authorship-stripping rebasing, which overlaps heavily with the new clean/unclean/history split tool.
5. Any other docs under `ai/°base/` (list the directory tree) that mention "ai:", commit conventions, subproject linking, or branch strategy — especially anything about how this base repo is itself included as a subproject/subtree in other repos (dumper/init scripts were mentioned in recent commits like "Extended subproject linking to .codex, ai/tool-settings, .mcp.json, AGENTS.md").

Report concisely but completely — this directly feeds a plan, so don't omit details from the AGENTS.md or the decisions JSON files.