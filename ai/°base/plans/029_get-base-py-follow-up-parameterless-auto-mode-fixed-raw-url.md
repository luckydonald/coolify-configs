# get-base.py follow-up: parameterless auto-mode, fixed raw URL, README docs

## Context

Phase 3 (implemented, merged as `cb3775a`) added `get-base.py`, a standalone curl-pipeable launcher. Three follow-ups from real usage:

1. **The raw URL is wrong.** The doc/plan used `.../luckydonald/base/master/...`, but this repo's own default branch (what `base/base` actually points at) is literally named `base`, not `master` — confirmed via `git branch -a` (`remotes/base/HEAD -> base/base`). Must use the explicit `refs/heads/base` form per the user's request (more robust than a bare branch name in the path, which GitHub's raw-content service treats ambiguously against tags/paths in some edge cases).
2. **Needs a parameterless mode.** Today every invocation requires remembering the exact subcommand (`bootstrap-branch feature`, `update-history-master --yes`). The user wants `curl ... | python3 -` with zero extra args to just do the right thing based on where they currently are.
3. **Needs documenting in `README.md`.** Nothing about this tooling is mentioned there yet.

## Confirmed auto-mode behavior (per user's answers)

By the time argv is decided, the worktree already exists (steps 1-4 of `get-base.py` already run), so this can safely reuse `°split_lib.branches` from the worktree rather than duplicating classification regexes.

Given `current_branch = git branch --show-current` (in the **original** repo, not the worktree) and `main_branch = branches.detect_main_branch(repo_root)`:

| current branch | delegated argv |
|---|---|
| `main_branch` itself, or `ai/history/{main_branch}` (history-master) | `["update-history-master", "--yes"]` |
| clean-format (anything else not matching the patterns below) | `["bootstrap-branch", branch_name]` |
| `ai/UNCLEAN/{branch}` | `["sync-splits", branch_name, "--direction=to-clean-history"]` |
| `ai/history/{branch}` (not history-master) | `["sync-splits", branch_name, "--direction=to-clean-history"]` — same as unclean, per user's answer |
| detached HEAD / branch name can't be determined | refuse: print available subcommands, exit 1 (never guess) |

If the user *does* pass explicit args (`... | python3 - bootstrap-branch feature`), behavior is unchanged — auto-detection only kicks in when `sys.argv[1:]` is empty.

## Design

- **`scripts/°base/git/get-base.py`**:
  - Fix the docstring's example URL to `https://raw.githubusercontent.com/luckydonald/base/refs/heads/base/scripts/%C2%B0base/git/get-base.py` (note: `%C2%B0` is `°` percent-encoded).
  - New function `auto_argv(repo_root: Path, worktree: Path) -> list[str] | None` (returns `None` when it can't confidently decide, so `main()` can print usage and exit 1 rather than delegate with nothing): adds `worktree / "scripts" / "°base" / "git"` to `sys.path`, `importlib.import_module("°split_lib.branches")`, reads `current_branch` via `git branch --show-current`, applies the table above.
  - `main()`: if `argv` (after the script) is empty, call `auto_argv(...)` after `ensure_worktree` and before `delegate`; if it returns `None`, print a short usage message (mirroring `cli.py`'s subcommand list) to stderr and return 1 without delegating.
- **`README.md`**: add a new subsection under "After Adopting The Base" (alongside "Git LFS", "Claude GitHub issue agent", etc.), e.g. "### Branch splitting (clean/unclean/history)":
  - The parameterless one-liner (`python3 -`, not `uv run -` — considered and rejected: no dependency-resolution benefit since the script is stdlib-only, and `python3` is more likely to already be present than `uv`) as the headline example, explained as "figures out what to do from your current branch."
  - The explicit-subcommand form as a secondary example for when you want to be specific.
  - `BASE_GIT_USERNAME` env var mention.
  - Add the new heading to the existing `<!-- TOC -->` block, matching its existing anchor-link style.
- **Tests** (`test_get_base.py`): add cases for `auto_argv` covering all five rows of the table above (using real temp repos with the worktree already set up, checking out each branch shape before calling it), plus a `main()`-level test confirming empty argv triggers the auto path and non-empty argv bypasses it (both via the existing `os.execvp` mock).

## Verification

1. `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v` — all pass.
2. Scratch-repo walkthrough: on `master`, run `get-base.py` with no args, confirm it runs `update-history-master --yes`; on a clean `feature` branch, confirm it runs `bootstrap-branch feature`; on `ai/UNCLEAN/feature`, confirm it runs the forward sync-splits.
3. Manually sanity-check the fixed raw URL resolves (`curl -fsSL https://raw.githubusercontent.com/luckydonald/base/refs/heads/base/scripts/%C2%B0base/git/get-base.py` returns the file) once this is pushed.
