# Fix: `get-base.py` auto mode fails on a fresh repo with "`ai/history/main` does not exist yet"

## Context

`ai/°base/errors/19.md` reports that running the standalone bootstrap
(`curl ... get-base.py | python3 -`) with no arguments on a brand-new
consuming repo — current branch a plain feature branch, `update-history-master`
never run before — fails:

```
feature/PROJ-1234/mr1: 'ai/history/main' does not exist yet -- run update-history-master first.
```

Root cause (confirmed by reading `°split_lib/bootstrap.py`, `get-base.py`,
`°split_lib/history_master.py`, and the two design plans that introduced these
pieces, 028 and 029):

- `bootstrap.py`'s `bootstrap_branch()` deliberately **refuses** rather than
  auto-running `update-history-master` when `ai/history/{main_branch}` is
  missing (`°split_lib/bootstrap.py:29-39`) — an intentional "skip + report"
  guard from plan 028, matching `rebase_to_master.py`'s existing convention.
- `get-base.py`'s parameterless **auto mode** (`auto_argv`, `get-base.py:117-157`,
  from plan 029) picks `["bootstrap-branch", <name>]` for any clean-format
  branch, but never checks whether `ai/history/{main_branch}` exists first —
  plan 029's decision table never considered "repo has never been bootstrapped
  at all". This is a wiring gap between two individually-correct pieces, not a
  considered decision — nothing discusses or defends this case.

The fix belongs in `get-base.py`'s auto-mode orchestration layer, not in
`bootstrap.py`: auto mode is explicitly meant to make the parameterless
invocation "just work" from any branch, so it should do what a user would
otherwise have to do manually in two steps (`update-history-master --yes` then
`bootstrap-branch <name>`). `bootstrap.py`'s own refusal stays untouched — it
still guards the case where someone runs `bootstrap-branch` directly/manually
with `ai/history/{main}` missing.

## Approach

In `scripts/°base/git/get-base.py`:

1. Factor the command-building line in `delegate()` (`get-base.py:105-109`)
   into a small `_split_command(repo_root, worktree, argv) -> list[str]`
   helper, and add `run_split(repo_root, worktree, argv) -> int` that builds
   the same command but runs it with `subprocess.run(...)` (inheriting
   stdout/stderr, not `capture_output`) and returns the exit code, instead of
   `os.execvp`-replacing the process. `delegate()` keeps using `execvp` for
   the final command (unchanged behavior/signature).

2. In `auto_argv()`, right where it currently decides
   `classification.format is branches.BranchFormat.CLEAN` (`get-base.py:148-151`):
   - Dynamically import `°split_lib.git_ops` the same way `branches` is
     already imported (`get-base.py:131-132`).
   - Compute `history_main_ref = branches.history_name(main_branch)` and check
     `git_ops.rev_parse(history_main_ref, repo_root) is None`.
   - If missing: log a status line, then call
     `run_split(repo_root, worktree, ["update-history-master", "--yes"])`.
     If that returns nonzero, log a failure status and `return None` (auto
     mode refuses, same contract as detached HEAD — `main()` already prints
     `USAGE` and exits 1 for `None`).
   - If it succeeds (or the ref already existed), fall through to the
     existing `["bootstrap-branch", classification.base_name]` decision.

   This reuses `split.py`'s real CLI (same code path as a manual two-step
   invocation) rather than reimplementing `update_history_master`'s
   conflict/logging handling inline in `get-base.py`.

No changes needed to `°split_lib/bootstrap.py`, `history_master.py`, or
`cli.py` — the existing `update-history-master` machinery (including its
`first_run` create-from-scratch path, `history_master.py:791-792`, `847-848`)
already does exactly what's needed once invoked.

## Tests

`scripts/°base/tests/test_get_base.py`:

- `AutoArgvTests.setUp` currently seeds only `__init__.py` + `branches.py`
  into the fake `base` remote. Extend it to copy the **entire real
  `°split_lib` package** plus the **real `split.py`** into the fake base repo
  (not a `# stand-in`), so `run_split` can genuinely execute
  `update-history-master` end-to-end against the test repo. This is the
  regression coverage gap the investigation flagged as missing.
- Update `test_on_clean_feature_branch_runs_bootstrap_branch`: with `base`
  fetched but `update-history-master` never run, `ai/history/master` doesn't
  exist yet in that fixture already — assert `auto_argv()` still returns
  `["bootstrap-branch", "feature"]`, and additionally assert
  `ai/history/master` now exists in `self.repo` afterwards (i.e. auto mode
  transparently created it).
- Add a new test where `ai/history/master` already exists beforehand (e.g.
  call `update-history-master` once first, or pre-create the branch) and
  assert `run_split`/subprocess is *not* invoked again (no redundant work) —
  e.g. via `mock.patch.object(self.module, "run_split")` asserting
  `assert_not_called()`.
- Add a failure-path test: make the prerequisite `update-history-master` run
  fail (e.g. mock `run_split` to return `1`) and assert `auto_argv()` returns
  `None` rather than proceeding to a doomed `bootstrap-branch`.
- `MainAutoModeTests` currently seeds a `# stand-in` `split.py` — since
  `main()`'s `test_empty_argv_triggers_auto_detection` mocks `os.execvp`
  before the final delegate but `auto_argv()` itself would now try to
  actually run `split.py update-history-master --yes` as a real subprocess
  first, this fixture needs the same real-`°split_lib`-plus-real-`split.py`
  treatment as `AutoArgvTests`, or `run_split` must be mocked in that test
  too. Prefer making the fixture realistic (matches `AutoArgvTests`) so the
  test exercises the true end-to-end fresh-repo scenario from the bug report.

Run:
```bash
uv run --project scripts/°base python -m unittest ai.scripts.tests.test_get_base -v
```
(or whatever the correct module path resolves to per this repo's test
discovery — confirm via the existing `unittest discover` command in
`CLAUDE.md`).

## Manual verification

Reproduce the exact bug-report scenario: a fresh temp repo with a feature
branch and no `ai/history/master`, run `get-base.py` with no args (using a
local worktree pointing at the in-progress `base` branch rather than the
published `base/base` remote branch, e.g. via `BASE_GIT_USERNAME`/a local
remote override, or just run the existing test suite above which reproduces
it directly) and confirm it now: prints the `update-history-master --yes`
progress output, creates `ai/history/master`, then proceeds to
`bootstrap-branch` successfully instead of erroring out.
