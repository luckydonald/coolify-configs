I now have all the information needed for a thorough report.

## Report: `ai/history/main` missing on fresh repos in `get-base.py` auto-mode → `bootstrap-branch` failure

### 1. Where the error is raised

`/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/bootstrap.py:29-39`, in `bootstrap_branch()`:

```python
history_main_ref = branches.history_name(main_branch)

if git_ops.rev_parse(history_main_ref, cwd) is None:
    return {
        "branch": base_branch,
        "ok": False,
        "error": (
            f"{history_main_ref!r} does not exist yet -- run "
            "update-history-master first."
        ),
    }
```

This is a plain `return` with an error dict, not an exception. It's surfaced by the CLI wrapper `_bootstrap_branch()` in `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/cli.py:254-262`, which prints `f"{args.branch}: {result['error']}"` to stderr and returns exit code 1 — exactly matching the observed error text `feature/PROJ-1234/mr1: 'ai/history/main' does not exist yet -- run update-history-master first.`

The module docstring (`bootstrap.py:1-12`) explicitly documents this as deliberate: "refuse clearly if `ai/history/master` doesn't exist yet (**never auto-run update-history-master as a side effect** -- same 'skip + report' principle as rebase_to_master.py)".

### 2. What `ai/history/main` is, and how it's normally created

It's a git **branch/ref**, computed by `branches.history_name(main_branch)` (in `°split_lib/branches.py`), which for a repo whose main branch is `main` yields the local branch `ai/history/main` (the "history-master" line — full replayed history of `main` plus folded-in `base/base` content).

It is normally created/updated by **`update_history_master()`** in `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/history_master.py`:

- Entry point: `update_history_master(...)` at `history_master.py:741-863`.
- `history_ref_name = branches.history_name(main_branch)` (`history_master.py:755`).
- `old_history_sha = git_ops.rev_parse(history_ref, cwd)`; `first_run = old_history_sha is None` (`history_master.py:791-792`).
- If `first_run` is true (i.e. the ref doesn't exist yet — exactly the fresh-repo case), it plans a "first run" (`_build_plan`, `history_master.py:555-560`: `replay_start_tip = master_tip`, no replay steps needed since there's nothing to rebase onto), runs any steps (base-merge folding of `base/base` if present), and then at `history_master.py:847-848`:
  ```python
  if first_run:
      git_ops.create_branch(history_ref, tip, cwd)
  ```
  — this is the exact code path that **creates `ai/history/main` from scratch**.
- It's exposed as the `update-history-master` subcommand in `°split_lib/cli.py:294-301` (`_update_history_master`, `cli.py:198-235`), and via `split.py --repo-root <path> update-history-master --yes` (or interactively without `--yes`, which prompts for confirmation to pull master/base — `history_master.py:777-785`).

**Is it ever invoked automatically as part of auto/bootstrap flow?** No — not when the current branch is a clean feature branch. It's only auto-invoked when the user happens to be *on* `main` (or on `ai/history/main` itself) when they run `get-base.py` with no args (see section 3). `bootstrap.py` explicitly refuses to auto-run it (see the docstring quote above), so the only supported way to create `ai/history/main` before bootstrapping a feature branch is a **manual, separate step**: run `get-base.py update-history-master --yes` (or `split.py ... update-history-master --yes`) once from the main branch first.

### 3. Full trace of `get-base.py` auto mode

File: `/home/user/git/luckydonald/base/scripts/°base/git/get-base.py`

`main()` (lines 170-187):
1. `repo_root = find_repo_root()` (line 174)
2. `ensure_base_remote(repo_root, username)` (line 176) — adds `base` remote if missing
3. `fetch_base(repo_root)` (line 177) — `git fetch base base`
4. `worktree = ensure_worktree(repo_root)` (line 178) — creates/refreshes `.git/base-tools` worktree checked out to `base/base`
5. If `argv` (script args) is empty → `auto_argv(repo_root, worktree)` (line 181)
6. `delegate(repo_root, worktree, argv)` (line 186) — `os.execvp` into `split.py --repo-root <repo_root> <argv...>` inside the worktree (line 105-109)

`auto_argv()` (lines 117-157) — the actual decision logic:
- `branch = current_branch(repo_root)` (line 126); refuses (`return None`) if detached HEAD.
- Imports `°split_lib.branches` from the freshly-created worktree (lines 131-132).
- `main_branch = branches.detect_main_branch(repo_root)` (line 134).
- If `branch == main_branch` → `["update-history-master", "--yes"]` (lines 136-139).
- `classification = branches.classify_branch(branch, main_branch=main_branch)` (line 141).
- If `classification.is_history_master` → `["update-history-master", "--yes"]` (lines 143-146).
- If `classification.format is CLEAN` (i.e. a plain feature branch like `feature/PROJ-1234/mr1`) → **`["bootstrap-branch", classification.base_name]`** (lines 148-151) — **this branch never checks whether `ai/history/{main_branch}` exists**.
- Otherwise (UNCLEAN or non-master HISTORY) → `["sync-splits", ..., "--direction=to-clean-history"]` (lines 153-157).

**Conclusion for #3**: Auto mode does **not** call `update-history-master` (or any equivalent bootstrap of the history-master line) before delegating to `bootstrap-branch` for a clean feature branch. It only runs `update-history-master` when the *currently checked-out branch itself* happens to be `main`/`ai/history/main`. On a genuinely fresh consuming repo where the user's current branch is a feature branch (the reported scenario), auto mode goes straight to `bootstrap-branch <branch>`, which then hits the precondition check in `bootstrap.py` and fails.

### 4. `bootstrap-branch` preconditions and reusable creation logic

`bootstrap_branch()` in `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/bootstrap.py:21-70` checks, in order:
1. `ai/history/{main_branch}` must already exist (lines 29-39) — the failing check.
2. The clean branch `{base_branch}` itself must exist (lines 41-46).
3. If `ai/history/{base_branch}` doesn't exist, create it (and its fork-point ref) at `ai/history/{main_branch}`'s tip (lines 48-55).
4. Delegate to `sync_unclean.reconstruct_unclean(...)` to build `ai/UNCLEAN/{base_branch}` (lines 57-62).

Per the design plan (`ai/°base/plans/028_git-branch-split-phase-3-bootstrap-a-branch-from-clean-only.md:56`), precondition #1 exists **on purpose**: "do not auto-run it — consistent with Phase 2's 'skip + report, never auto-synthesize' principle already established in `rebase_to_master.py`." The stated rationale is that creating/updating `ai/history/master` is a heavier, potentially conflict-prone, stateful operation (cherry-picks, base-merge folding, interactive pull prompts, resumable `--continue`/`--abort` state) that shouldn't be silently triggered as a side effect of an unrelated command.

**Reusable creation logic already exists**: `update_history_master()` in `history_master.py` (see section 2) already fully implements "create `ai/history/{main_branch}` from scratch if it doesn't exist" via its `first_run` branch (`history_master.py:791-792`, `847-848`, plus the whole `_build_plan`/`_run_steps`/base-fold machinery for the first-run case). This is exactly the function that `bootstrap_branch` (or, more appropriately, `get-base.py`'s `auto_argv`/`main`) could call automatically before/instead of failing — either:
- have `get-base.py`'s auto mode call `update-history-master --yes` first when `ai/history/{main_branch}` doesn't exist and the target is `bootstrap-branch`, or
- have `bootstrap_branch()` itself call `history_master_lib.update_history_master(repo_root=repo_root, main_branch=main_branch, yes=True, ...)` when the ref is missing, before proceeding — though this would need to reconcile with the "never auto-synthesize" principle documented in the plan (see below, this looks like a deliberate design tension, not an accident, at the `bootstrap.py` layer specifically — but there is no equivalent guard/rationale for `get-base.py`'s *auto mode* skipping this).

### 5. Git history / rationale

- `bootstrap.py`'s precondition was introduced in a single commit: `d5a8e68` ("[base] [ssp] git split: ai: Run: Added Phase 3 of the clean/unclean/history branch-split feature"), Jul 7 2026. Commit message explicitly states: "...this just refuses clearly if `ai/history/master` doesn't exist yet (never auto-runs `update-history-master`)..." — **intentional safety guard** at that layer, matching the plan (`ai/°base/plans/028_...md`) verbatim.
- `get-base.py`'s **auto mode** was added later, in commit `944d438` ("Added parameterless auto-mode to `get-base.py`, fixed its raw URL, documented it"), per plan `ai/°base/plans/029_get-base-py-follow-up-parameterless-auto-mode-fixed-raw-url.md`. That plan's decision table (lines 15-24) maps "clean-format branch" → `["bootstrap-branch", branch_name]` with **no mention at all** of the case where `ai/history/{main_branch}` doesn't exist yet — it implicitly assumes history-master is already bootstrapped by the time someone is working on a feature branch. This looks like an **overlooked wiring gap** introduced when auto-mode was layered on top of the Phase 3 "skip + report" design, rather than a considered decision to require a manual step in this specific case. Nothing in plan 029, its commit message, or `get-base.py`'s own docstring/comments discusses or defends the "fresh repo + feature branch" scenario at all.
- No later commit (`e9974e5`, `7712b5a`, `47a7283`, `e629d46`, `9c3c758`, per `git log --oneline` on the touched files) revisits or discusses this interaction either.

### 6. Existing test coverage

- `/home/user/git/luckydonald/base/scripts/°base/tests/test_git_split_bootstrap.py`:
  - `NoHistoryMasterTests.test_errors_clearly_without_history_master` (lines 36-42): confirms `bootstrap.bootstrap_branch(...)` returns `ok=False` and an error mentioning `"update-history-master"` when `ai/history/master` doesn't exist — i.e. it **locks in the current failing behavior** as correct, at the unit level.
  - `NoCleanBranchTests`, `BootstrapFromCleanOnlyTests` (creates-history/dry-run/idempotent-rerun) all pre-create `ai/history/master` in `setUp` before testing bootstrap, so they don't exercise the missing-history-master path further.
- `/home/user/git/luckydonald/base/scripts/°base/tests/test_get_base.py`:
  - `AutoArgvTests.test_on_clean_feature_branch_runs_bootstrap_branch` (lines 192-197): only asserts that `auto_argv()` **decides** to return `["bootstrap-branch", "feature"]` for a clean branch — it does not exercise an actual `split.py bootstrap-branch` run nor check whether `ai/history/{main_branch}` exists at that point, so it does not catch the end-to-end failure from the bug report.
  - No test in either file exercises the full `get-base.py` → `split.py bootstrap-branch` pipeline against a repo where `ai/history/{main_branch}` was never created (i.e. the true "fresh repo, auto mode, feature branch checked out" scenario from `ai/°base/errors/19.md`). This is a **coverage gap** — a regression test for this exact scenario doesn't exist yet.
- `test_git_split_history_master.py` extensively tests `update_history_master`'s first-run/create-from-scratch behavior (e.g. lines 41-330+), confirming that logic itself is solid and reusable — it's just never invoked from the `bootstrap-branch`/auto-mode path.

### Summary for planning a fix

The failure is a genuine wiring gap between two independently-designed, individually-correct pieces:
- `bootstrap.py` (Phase 3) deliberately refuses rather than auto-creating `ai/history/{main_branch}`.
- `get-base.py`'s auto mode (later addition) never considered/handled the case where the current branch is a feature branch on a repo that has never had `update-history-master` run.

A fix likely needs to either (a) teach `get-base.py`'s `auto_argv`/`main` to detect a missing `ai/history/{main_branch}` and run `update-history-master --yes` automatically first when about to delegate to `bootstrap-branch` (most consistent with "auto mode should just work"), or (b) revisit whether `bootstrap_branch()` itself should call `history_master_lib.update_history_master(..., yes=True)` when the ref is missing — but note this would be a deliberate reversal of the documented "skip + report, never auto-synthesize" design principle from plan 028/commit `d5a8e68`, so worth flagging explicitly rather than silently overriding it. Either way, a new end-to-end test (real temp repo, no `ai/history/{main}` ever created, current branch = clean feature branch, invoke the equivalent of `get-base.py` with no args) should be added to `test_get_base.py` to lock in the fix, since no such test exists today.