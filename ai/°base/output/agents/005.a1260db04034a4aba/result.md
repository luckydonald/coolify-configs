## Report

### 1. `scripts/°base/git/` contents

```
scripts/°base/git/__init__.py               (empty package marker)
scripts/°base/git/rebase_strip_claude_authorship.py
scripts/°base/git/hooks/__init__.py
scripts/°base/git/hooks/tool_path.sh
scripts/°base/git/hooks/commit/reject_co_authored_by.py
scripts/°base/git/hooks/commit/require_memory_delete_marker.py
scripts/°base/git/hooks/install/__init__.py
scripts/°base/git/hooks/install/__main__.py
scripts/°base/git/hooks/install/setup.py
scripts/°base/git/remote/__init__.py
scripts/°base/git/remote/fix_username.py
```

There is **no unified CLI framework** here (no click/typer). Each script is a standalone, argparse-based (or no-arg) Python module invoked directly via `uv run`/`python3 <path>`. There is no `°base`/`base` CLI entry point, no `main.py`/`cli.py`/`__main__.py` dispatcher at the `scripts/°base/git` level (a `__main__.py` exists only for `git/hooks/install`, see below). The only `argparse`-based CLIs found in the repo (`grep -rn "import argparse"`) are `scripts/°base/ai/settings/°settings_lib/cli.py`, `scripts/°base/ai/references/°dllink_lib/cli.py`, `scripts/°base/ai/hooks/save-decision/hook.py`, and `scripts/°base/git/remote/fix_username.py`. A new "git split" tool would need its own new argparse entry point, following this repo's convention (thin `.py` shim delegating into a `°<name>_lib` package, mirroring `ai/settings/sync.py` → `°settings_lib/cli.py`, or `ai/references/download-link.py` → `°dllink_lib/cli.py`).

- `git/rebase_strip_claude_authorship.py:1-60+` — standalone script: rebases current branch onto merge-base with `origin/mane`, and via `--exec`/`--amend-step` rewrites any `claude[bot]` author/committer to a human identity (`CLAUDE_EMAIL`, `NEW_AUTHOR` constants at lines 21-24). Useful precedent for rebase-with-callback machinery relevant to the planned `rebase-branches-to-master`/`sync-splits` commands.
- `git/remote/fix_username.py` — large (~1900 line) standalone interactive TUI (prompt_toolkit) + argparse CLI to fix GitHub remote URLs (add username/`.git` suffix) and required remotes (`empty`, `base`) and LFS lock-verify config. Entry: `main(argv)` at line 1823, `parse_args` at 1765. Good precedent for a `--yes`/non-interactive vs TUI dual-mode CLI.
- `git/hooks/tool_path.sh` — thin wrapper: normalizes `PATH` then `exec "$@"`; used as the `entry:` prefix for every local pre-commit hook in `.pre-commit-config.yaml`.

### 2. `git/hooks/commit/` and `git/hooks/install/`

**`git/hooks/commit/`** — commit-msg-stage hook scripts (each takes exactly one CLI arg: the path to a temp file containing the commit message; returns 0/1/2):
- `reject_co_authored_by.py:10-21` — `main(argv)` reads the commit-msg file, returns 1 if it contains `"Co-Authored-By"`.
- `require_memory_delete_marker.py:32-53` — greps `git diff --cached --name-only --diff-filter=D` for deleted `.md` files under `ai/memory/`/`ai/°base/memory/` (`MEMORY_DIRS` at line 11), and requires a `Deleted Memory: <name>` line in the commit message for each.

**`git/hooks/install/`** — auto-installer for `pre-commit`:
- `install/__init__.py:1-71` — runs on `import` (line 71 calls `_install_hooks()` at module scope). Lists currently-installed hooks (`_list_hooks`), then clears `core.hooksPath`, and runs `python -m pre_commit install --hooks-type commit-msg` (line 63). **Only installs the `commit-msg` hook type — there is no pre-push installation anywhere in this repo.**
- `install/setup.py:1-46` — same logic, but as a `setuptools` `setup.py` "install-time" hook (`_install_hooks` at line 15, called at module scope line 41, same `--hooks-type commit-msg` at line 29).
- `install/__main__.py:1-5` — `from ai.scripts.git.hook.commit import *` (note: this import path looks stale/broken relative to the actual `°base` layout — likely leftover/dead code, worth flagging).

**How hooks are actually registered:** `.pre-commit-config.yaml` (repo root) defines 3 local hooks — `no-co-authored-by` and `require-memory-delete-marker` at `stages: [commit-msg]`, plus `ai-settings-sync` (stages unset → defaults to running at `pre-commit` stage) calling `scripts/°base/ai/settings/sync.py --check`. `.git/hooks/commit-msg` and `.git/hooks/pre-commit` are pre-commit-framework-generated dispatcher scripts (`pre_commit hook-impl --config=.pre-commit-config.yaml --hook-type=...`).

**Existing pre-push hook:** `.git/hooks/pre-push` currently only runs `git lfs pre-push "$@"` (same for `post-checkout`/`post-commit`/`post-merge` — all pure git-lfs passthroughs, not pre-commit-managed). **There is no pre-commit-managed pre-push stage at all today.**

**Where a new push-check hook plugs in:**
1. Add a new hook id to `.pre-commit-config.yaml` with `stages: [pre-push]`, entry pointing at a new script under `scripts/°base/git/hooks/push/` (new dir, following the `commit/` naming convention).
2. Update `git/hooks/install/__init__.py:63` and `git/hooks/install/setup.py:29` to also `pre_commit install --hooks-type pre-push` (currently they only install `commit-msg`), otherwise `.git/hooks/pre-push` will remain the git-lfs-only script and never invoke pre-commit/the new check.
3. A pre-push hook script receives `remote name` and `remote url` as argv and ref update lines on stdin (`<local ref> <local sha> <remote ref> <remote sha>`) — the new script needs to parse stdin to know local branch name + commits being pushed, matching this repo's pattern of reading structured input then returning 0/1.

### 3. CLI dispatch pattern

No single `°base`/`base` command exists. Pattern instead is: thin `.py` shim → delegates to a `°<name>_lib` package's `cli.py:main()`.

- `scripts/°base/ai/settings/sync.py:1-12` — `main = importlib.import_module("°settings_lib.cli").main` (line 10), invoked at line 12 `raise SystemExit(main())`.
- `scripts/°base/ai/references/°dllink_lib/cli.py:1-` uses `argparse`.
- No `click` group anywhere in the repo (`grep -rn "click.group\|@click"` → zero hits outside `.venv`).

A new git-split tool should likely follow this same shim pattern: e.g. `scripts/°base/git/split.py` (thin) → `scripts/°base/git/°split_lib/cli.py` (argparse, subcommands `sync-splits`, `update-history-master`, `rebase-branches-to-master`) — consistent with `ai/settings/sync.py` / `°settings_lib` and `ai/references/download-link.py` / `°dllink_lib`.

### 4. AI-content commit structure (`scripts/°base/ai/hooks/`)

Directory: `ai/hooks/_lib.py` (shared helpers), `ai/hooks/merge_staged.py`, `ai/hooks/record-memory/hook.py`, `ai/hooks/save-decision/hook.py`, `ai/hooks/save-plan/hook.py`, `ai/hooks/save-prompt/hook.py`.

Key mechanics for detecting "AI-only" commits, all centralized in `_lib.py`:
- `append_and_commit()` (`_lib.py:220-250` approx) does `git add -- <relpath> <extra_relpaths>` then `git commit --no-verify --only <relpath> <extra_relpaths> -m <msg>` — i.e. **every AI auto-commit uses `git commit --only <specific-ai-file-paths>`**, so AI commits touch *only* files under `ai/query.md`, `ai/plans/NNN_*.md`, `ai/decisions/`, `ai/output/{agents,explore,compact,debug}/`, `ai/memory/` etc. (or `ai/°base/...` inside the base meta-repo — routing logic in `resolve_log_path()` / `_is_inside_base_repo()`, `_lib.py:80-95`).
- Commit message convention: subjects start with `ai: ` (e.g. `f"ai: save decision {slug}"` in `save-decision/hook.py:515`, `f"ai: save plan {prefix}_{new_slug}"` in `save-plan/hook.py:291/295/307`, `"ai: updated prompt"` in `save-prompt/hook.py:716/729/736/745`, `f"ai: compact {dir_name} autoloads"` in `save-prompt/hook.py:531`, `f"ai: explore {dir_name} result"` (line 596), `f"ai: agent {dir_name} results"` (line 652)). `base_ai_commit_subject()` (`_lib.py`) additionally prefixes `[base] ` when inside the base meta-repo, per AGENTS.md's documented format `[base] topic: ai: Run: Short summary.` (`ai/°base/AGENTS.md:114-117`).
- All auto-commits use `--no-verify` (bypassing hooks) and `--only <paths>` (touching nothing else) — this is the structural signature a future "is this commit AI-only?" detector should rely on: (a) message starts with `ai:` (optionally after `[base] `), and/or (b) every changed path in the commit is under the AI-artifact tree (`ai/**`, `ai/°base/**`).
- `save-plan/hook.py:_commit()` (lines 214-220) is a second, independent commit helper (used for plan file renames) with the same `git commit --no-verify --only ... -m ...` shape.

### 5. Existing branch-splitting concept

**Already fully speced in `ai/°base/todo.md:59-163`** (identical text duplicated in `ai/°base/query.md:2348-2447`, the auto-logged prompt history) — this is the user's own prior `/plan` prompt describing exactly this feature. Highlights:
- Branch types table (line 64-68): `clean` = `{branch}`, `unclean` = `ai/UNCLEAN/{branch}`, `history` = `ai/history/{branch}`.
- `ai/history/master` (or main-branch-name) concept (lines 71-72).
- `update-history-master` command spec (lines 74-103): rebuild history master by rebasing `ai/history/master` onto `master`, merging latest `base/base`, with a `--force-merge=<branch>` option; explicit note about the merge-vs-rebase conflict-recreation problem.
- `sync-splits` command spec (lines 105-138): generate **clean** (strip AI content + drop AI-only commits, from `unclean`, based on `master`), **history** (strip code content but keep commits, from `unclean`, based on `ai/history/master`), **unclean** (cherry-pick clean+history commits interleaved, three cases: code-only / history-only / code+history, based on `ai/history/master`).
- `rebase-branches-to-master` command spec (lines 140-150): rebase clean→master, history→`ai/history/master`, unclean→history.
- Push protections spec (lines 155-161): (1) block pushing `unclean`/`history`-named branches to `origin`; (2) block AI-containing commits from pushing unless branch is `unclean`-format; (3) block code-containing commits from pushing unless branch is `history`-format.

No corresponding implementation exists yet anywhere (`grep -rniE "unclean|history-master|clean branch|split.*branch"` across non-`.venv` files returns only these two log/doc files — `todo.md` and `query.md`). This confirms the feature is purely at the planning-doc stage; nothing under `scripts/°base/git/` implements any of it today.

### 6. Test framework/conventions (`scripts/°base/tests/`)

- Framework: stdlib `unittest`. Run via `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v` (README: `scripts/°base/tests/README.md:11-13`, also documented in `ai/°base/AGENTS.md:11-19`).
- Because `°base`/hyphenated dirs aren't importable as normal packages, tests load target modules dynamically via `importlib.util.spec_from_file_location` (e.g. `scripts/°base/tests/test_git_remote_fix.py:1-17`: builds `MODULE_PATH = ROOT/"fix_username.py"`, `spec_from_file_location(...)`, `exec_module`).
- Env/dependencies declared in `scripts/°base/pyproject.toml` (`prompt_toolkit`, `pydantic`, `pyte`) with `[tool.uv] package = false`; a `uv.lock` exists.
- Existing test files relevant as style precedent for a future git-split test: `test_git_remote_fix.py`, `test_git_remote_fix_tui_tdd.py` / `test_git_remote_fix_tui_terminal.py` (uses `pyte` VT100 emulation for TUI testing — see `git_remote_fix_tui_test_support.py`), `test_rebase_strip_claude_authorship.py`, `test_ai_hooks_base_routing.py`, `test_permission_check.py`. A new git-split tool's tests would likely go in `scripts/°base/tests/test_git_split*.py` using the same dynamic-import pattern, probably with real throwaway git repos created via `subprocess` + `tempfile.TemporaryDirectory()` (this pattern is used in `test_git_remote_fix.py`'s `git()` helper and elsewhere).