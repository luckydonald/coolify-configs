This confirms `fix_username.py` only ensures the `empty`/`base` remotes are *configured with correct URLs* — it doesn't perform any merge/fold logic itself. That confirms the `empty/init` merge step is purely a manual README instruction, never invoked by `history_master.py`.

I now have the complete picture. Here is the full report.

## Summary of findings

**1. Is there a documented/intended special case for the very first fold (no prior base-merge commits)?**

Partially, and it's inconsistent/incomplete:

- `history_master.py` does distinguish "first run" of `update_history_master` — i.e. `ai/history/master` doesn't exist yet (`old_history_sha is None`, `_build_plan` at `scripts/°base/git/°split_lib/history_master.py:509`, and `update_history_master` at `history_master.py:737`). In that case `ai/history/master` is created fresh as "a literal copy of master" (see test comment `test_git_split_history_master.py:118-119`) and *then* `_fold_base` is called on it (`history_master.py:770-788`).
- But that "first run" branch contains **no special handling for the base-merge itself** — it calls the exact same `_fold_base` → `git_ops.merge_no_commit` path as every subsequent fold (`history_master.py:372-387`, `git_ops.py:250-257`, `git merge --no-commit --no-ff <sha>`, no `--allow-unrelated-histories`).
- The only place the unrelated-histories problem is acknowledged at all is a **test comment**, not code: `scripts/°base/tests/test_git_split_history_master.py:100-105` — "a genuinely unrelated history would make `git merge` refuse outright, which isn't the scenario being tested here (a real base/base fork does share history with this repo)." Both tests that exercise `_fold_base`/base-merge recreation (`test_base_merge_recreation_after_master_advances` at `:100`, `test_master_is_never_mutated_by_a_base_merge` at `:169`) deliberately dodge the real failure mode by `git clone`-ing the *test repo itself* to fabricate the fake `base` remote (`:106-116`, `:173-183`) rather than using a genuinely unrelated repo.
- `ai/°base/todo.md:100-103` (original design doc) only says "merge the most recent `base/base` into `ai/history/master`" and worries about rebase-vs-merge interaction, never about unrelated ancestries.
- `ai/°base/plans/028_git-branch-split-phase-3-bootstrap-a-branch-from-clean-only.md:16-19` documents the invariant that `master`/clean branches are never touched by the base-fold (only `ai/history/master` is), but again says nothing about the unrelated-histories failure on first fold.

Conclusion: there is no working, tested, documented special case in `history_master.py` for "no common ancestor" — it's a known-but-sidestepped gap, evidenced only by the test's evasive setup, not by actual handling code.

**2. Is `base/base` expected to share history with a client's `master`, or is it a separate template repo grafted in?**

By design it is a **separate template repo**, and the top-level `README.md` documents exactly how the unrelated-histories problem is meant to be solved manually — via a shared-ancestor trick, not by ignoring the check:

- `README.md:183-217` ("Setup: c) Merge `base/base`"): initial adoption into "an unrelated existing repo" is:
  ```
  git remote add empty https://luckydonald@github.com/EmptyAAS/empty.git
  git remote add base https://luckydonald@github.com/luckydonald/base.git
  git fetch empty init
  git fetch base base
  git merge --allow-unrelated-histories --no-verify empty/init
  git merge --no-ff --no-verify base/base
  ```
  I.e. the client repo first grafts in a tiny shared "empty/init" commit from a *third*, dedicated `EmptyAAS/empty` repo (using `--allow-unrelated-histories` exactly once, for that graft only), which `base/base` itself is presumably also built on top of — giving the two histories a common ancestor so the subsequent `base/base` merge is a normal, non-unrelated merge (`README.md:190-198,204`: "if your repo already shares `empty/init` as an ancestor, the initial `--allow-unrelated-histories` is not needed").
- `scripts/°base/git/remote/fix_username.py:18-21`'s `REQUIRED_REMOTES` confirms both `empty` (`EmptyAAS/empty.git`) and `base` (`luckydonald/base.git`) are first-class configured remotes in this tooling, matching the README's two-remote scheme.
- However, `history_master.py`'s `_pull_base`/`_fold_base` (`:240-241`, `:372-387`) only ever fetch/merge the `base` remote — there is **no code anywhere referencing the `empty` remote or an `empty/init` graft**. `get-base.py`'s bootstrap plan (`ai/°base/plans/028...md:42-48`) likewise only adds/fetches `base`, never `empty`.
- Plan `026_git-branch-split-foundation-phase-1...md:5` frames `base` generically as "a reusable template repo merged/rebased into consuming projects" — consistent with "by design a separate repo," not one the client repo is expected to have forked from.

So: the intended/documented answer is "by design, separate template repo, unrelated histories, bridged via a manual `empty/init` graft using `--allow-unrelated-histories`" — but the automated `update-history-master`/`_fold_base` code path never performs that graft and never passes the flag, so it will hit exactly the `fatal: refusing to merge unrelated histories` error the manual README workflow is built to avoid.

**3. Mentions of `--allow-unrelated-histories` in the codebase/docs:**

Only in `README.md` — `README.md:190` and `README.md:213` (plus a note at `README.md:204`). Zero occurrences anywhere in `scripts/°base/` (confirmed via `grep -rn "allow-unrelated-histories\|allow_unrelated_histories" .` finding only the two `README.md` lines).

**4. Is `git_ops.merge_no_commit` the only merge primitive, or is there an alternate for unrelated histories?**

`git_ops.merge_no_commit` (`scripts/°base/git/°split_lib/git_ops.py:250-257`) is the only merge-invoking function in `git_ops.py` — confirmed by `grep -n "def merge"` over that file, which returns only `merge_base` (`:123`, a read-only `git merge-base` lookup, not a real merge), `merge_no_commit` (`:250`), and `merge_abort` (`:260`). Its docstring is a fixed argv (`["git", "merge", "--no-commit", "--no-ff", sha]`) with no parameter for extra flags. There is no alternate `merge_no_commit_allow_unrelated` or similar anywhere in `°split_lib`, and per the module docstring of `history_master.py:1-13` ("git_ops.py is shared/frozen for this task; anything genuinely missing is implemented here instead"), any unrelated-histories support would have to be added either as a new frozen `git_ops.py` primitive or as a local `_git([...])` call inside `history_master.py` (the pattern already used elsewhere in that file, e.g. `_git(["commit", "--allow-empty", "--no-edit"], ...)` at `:301`) — neither currently exists.