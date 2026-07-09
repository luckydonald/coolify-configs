Repo root: /home/user/git/luckydonald/base

I'm designing Phase 2 of a git branch-splitting tool (Phase 1 — branch/commit classification + push-protection hooks — is already implemented under `scripts/°base/git/°split_lib/` and `scripts/°base/git/hooks/push/`). Phase 2 needs to actually generate/sync three branch variants (`clean`, `unclean` = `ai/UNCLEAN/{branch}`, `history` = `ai/history/{branch}`) from each other by splitting each commit's changed files into an "AI/base content" tree and a "code" tree, and reassembling them later. This requires building new commits with partial trees (not full checkouts), embedding correlation metadata (likely git trailers) so commits can be matched back up across the three branches, and rebasing an `ai/history/master` branch while recreating merge commits that can't cleanly rebase.

Please research and report back (read-only, no modifications):

1. **Git trailer usage**: grep the whole repo (excluding `.venv`, `node_modules`, `__pycache__`) for `interpret-trailers`, `Trailer`, `trailer`, or any existing code that reads/writes structured key-value lines in commit message bodies (e.g. `Co-Authored-By:` handling in `scripts/°base/git/hooks/commit/reject_co_authored_by.py` — that's a trailer-like line already checked for). Is there any existing helper for parsing/writing commit trailers I should reuse or mirror?
2. **Partial-tree / commit-tree construction precedent**: grep for `commit-tree`, `read-tree`, `mktree`, `update-index`, `git diff-tree`, `git apply`, `checkout-index`, or any code that builds a commit from a subset of files rather than a full working-tree commit. Check `scripts/°base/git/rebase_strip_claude_authorship.py` and anything else under `scripts/°base/git/`.
3. **Empty commit handling**: grep for `--allow-empty` anywhere in the repo (scripts, hooks, docs) — any existing precedent/reasoning for keeping deliberately-empty commits.
4. **Any git filter-branch / git-filter-repo / BFG usage**: grep for `filter-branch`, `filter-repo`, `bfg` — is there any existing history-rewriting tooling I should be aware of or reuse?
5. **Read the currently-implemented Phase 1 files in full** to report their exact public API surface (function signatures only, not full bodies) so Phase 2 code can build on them without re-deriving:
   - `scripts/°base/git/°split_lib/branches.py`
   - `scripts/°base/git/°split_lib/classify.py`
   - `scripts/°base/git/°split_lib/push_checks.py`
   - `scripts/°base/git/°split_lib/git_ops.py`
   - `scripts/°base/git/°split_lib/cli.py`
6. **Re-confirm nothing else in the repo already implements sync-splits/history-master/rebase-to-master logic**: grep for `sync-splits`, `update-history-master`, `rebase-branches-to-master`, `history/master`, `force-merge` outside of `ai/°base/todo.md`, `ai/°base/query.md`, and `ai/°base/plans/026_*.md` (the already-known planning docs).
7. Read `ai/°base/plans/026_git-branch-split-foundation-phase-1-classification-push-prot.md` in full and summarize it in 5 sentences — this is the already-approved-and-implemented Phase 1 plan, useful for Phase 2 to stay consistent with its stated scope boundary.

Report concisely with file:line citations. This is research only.