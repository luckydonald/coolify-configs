# Git branch-split foundation (Phase 1): classification + push protection

## Context

`base` is a reusable template repo merged/rebased into consuming projects. Consuming projects must be able to ship a "clean" branch containing zero AI-assistant/base-tooling mentions, while the actual work happens on a branch that mixes AI artifacts and code freely. The full design (`ai/°base/todo.md:59-163`) introduces three branch variants per feature branch — `clean` (`{branch}`), `unclean` (`ai/UNCLEAN/{branch}`), `history` (`ai/history/{branch}`) — plus commands to generate/sync/rebase between them and an `ai/history/master` branch.

That full design is large and its hardest parts (rebasing `ai/history/master` while recreating `base/base` merges, cherry-pick-based reconstruction of `unclean` from `clean`+`history`) are not yet resolved. Per user direction, **this plan covers only the foundation**: branch/commit classification and a real push-protection hook enforcing the two safety invariants already specified. `sync-splits`, `update-history-master`, `rebase-branches-to-master`, and the commit-correlation mechanism (git trailers, confirmed as the primary approach) are deferred to a follow-up plan.

## Confirmed design decisions

- **AI/base content** = any path under `ai/**`, `.claude/**`, `.codex/**`, exactly `.mcp.json`/`AGENTS.md`/`CLAUDE.md`, or any path with a `°base` directory segment anywhere (catches `scripts/°base/**`, `ai/°base/**`, future ones).
- **Commit classification**:
  - `is_ai_only_commit`: every changed path is AI/base content (requires at least one path — an empty-path commit is not vacuously ai-only).
  - `is_ai_tainted_commit`: `is_ai_only_commit` OR any changed path is AI/base content (mixed) OR subject matches the ai-subject regex.
  - `is_code_containing_commit`: any changed path is NOT AI/base content.
  - ai-subject regex: `^(\[.*\]\s*)?.*\bai:` — matches `ai: updated prompt`, `[base] topic: ai: Run: ...`, `[dumper] init script: ai: Run: ...`; does not match `aisle:`/`said:`.
- **Push content policy** (per branch format):

  | format | AI-tainted commits | code-containing commits |
  |---|---|---|
  | unclean | allowed | allowed |
  | clean | **blocked** | allowed |
  | history | allowed | **blocked** |

- **Push name policy**: pushing a branch classified `unclean` or `history` to a remote literally named `origin` is always blocked, independent of content.
- **Deletions** (`local_sha` all-zero) are exempt from both checks.
- **Pre-push enforcement is hand-rolled**, not routed through `pre-commit`'s `pre-push` stage: `pre_commit`'s own `hook_impl.py` only reads the *first* ref-update line and redirects hook stdin to `/dev/null`, so a `local` hook with `stages: [pre-push]` cannot see multi-ref stdin at all — verified by reading the installed `pre_commit/commands/hook_impl.py`/`util.py`. `.git/hooks/pre-push` will instead be a small generated trampoline (written by the installer) that execs a tracked script, which itself calls `git lfs pre-push` first (preserving existing LFS behavior) and then our checker.

## Files to add

**`scripts/°base/git/°split_lib/`** (new package, mirrors `ai/settings/°settings_lib` shim pattern):
- `__init__.py` — empty.
- `branches.py` — `BranchFormat` enum (`CLEAN`/`UNCLEAN`/`HISTORY`), `BranchClassification` dataclass (`ref`, `format`, `base_name`, `is_history_master`), `classify_branch(ref, *, main_branch="master")`, `unclean_name`/`history_name`, `base_name_from_unclean`/`base_name_from_history`, `strip_refs_heads`, `detect_main_branch(repo_root)` (via `origin/HEAD`, fallback `main`/`master`).
- `classify.py` — `is_ai_base_path(path)`, `CommitClassification` dataclass, `classify_commit(sha, subject, paths)` implementing the three predicates above with the confirmed regex.
- `git_ops.py` — subprocess glue only: `repo_root()`, `rev_exists`, `commits_new_to_remote(local_sha, remote_sha, remote_name, cwd)` (handles deletion / new-branch-via-`--not --remotes=` fallback, same idiom pre-commit itself uses), `changed_paths_for_commit`, `subject_for_commit`.
- `push_checks.py` — pure functions only (no subprocess): `RefUpdate` dataclass, `is_zero_sha`, `check_content_policy(branch, commits)`, `check_name_policy(branch, remote_name)`, `evaluate_ref_update(...)` aggregating both into violation messages.
- `cli.py` — argparse with a `check-push` subcommand (`--remote-name`, `--remote-url`, reads ref-update lines from stdin), `_parse_ref_lines`, `_check_push(...)` wiring `git_ops` + `branches` + `classify` + `push_checks` together and printing one aggregated report before returning 1 on any violation, `main(argv=None)`.

**`scripts/°base/git/split.py`** — thin shim identical in style to `ai/settings/sync.py`:
```python
main = importlib.import_module("°split_lib.cli").main
raise SystemExit(main())
```

**`scripts/°base/git/hooks/push/pre_push.sh`** (new, tracked, executable) — mirrors `hooks/commit/` naming convention. Buffers stdin to a temp file, runs `git lfs pre-push "$@"` with it (or a stderr warning if `git-lfs` is missing), then runs `python3 split.py check-push --remote-name ... --remote-url ...` with the same buffered stdin, combining exit codes so either failure aborts the push.

**Installer changes** — `scripts/°base/git/hooks/install/__init__.py` and `install/setup.py`: add `_install_pre_push_hook(repo_root)` that writes a small generated `.git/hooks/pre-push` trampoline (`exec .../hooks/push/pre_push.sh "$@"`), backing up any pre-existing non-generated `pre-push` script once (`pre-push.pre-base-backup`) before overwriting, and `chmod +x`. Call this alongside the existing `pre_commit install --hooks-type commit-msg` call. **No changes to `.pre-commit-config.yaml`** — pre-push intentionally bypasses pre-commit's stage mechanism per the finding above.

**Tests** (stdlib `unittest`, dynamic-import loading exactly like `test_ai_settings_sync.py` / `test_git_remote_fix.py`):
- `scripts/°base/tests/test_git_split_branches.py` — classification incl. `refs/heads/` stripping, `ai/history/master` master-flag behavior, malformed `ai/UNCLEAN/` fallthrough, round-trip name helpers, `detect_main_branch` against a real temp repo.
- `scripts/°base/tests/test_git_split_classify.py` — `is_ai_base_path` true/false table (incl. `°base`-anywhere and false-positive guards like `ai-notes.txt`, `claude-thing/x`), `classify_commit` matrix, ai-subject regex cases per the confirmed pattern.
- `scripts/°base/tests/test_git_split_push_checks.py` — pure 3×2 policy-matrix table test, name-policy origin/non-origin cases, and end-to-end cases against a real temp git repo (via `subprocess`+`tempfile.TemporaryDirectory()`) calling `cli._check_push(...)` directly: unclean→non-origin mixed commit (allowed), unclean→origin (blocked by name only), clean→non-origin pure-code (allowed), clean→non-origin ai-tainted (blocked), history→non-origin code-touching (blocked), history→origin ai-only (blocked with both name+content messages), deleted-branch and brand-new-branch ref lines (skipped/handled via the `--not --remotes=` fallback).

## Explicitly out of scope (deferred to a follow-up plan)

`sync-splits` (clean/history generation from unclean), commit correlation via `X-Base-Split-Source`/`X-Base-Split-Kind` trailers (with dedicated metadata commits as a fallback where needed), `update-history-master` (rebase + `base/base` merge recreation), `rebase-branches-to-master`, and unclean reconstruction from clean+history.

## Verification

1. `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v` — new tests pass alongside existing ones.
2. Manually run `python3 scripts/°base/git/hooks/install/__init__.py` (or trigger via the existing installer entry point) in a scratch clone and confirm `.git/hooks/pre-push` is the new trampoline, is executable, and a real `git push` of a small test branch still invokes `git lfs pre-push` (check LFS behavior unaffected) before running the checker.
3. Exercise the checker directly end-to-end in a scratch repo: create an `ai/UNCLEAN/foo` branch and try pushing it to a remote named `origin` (expect block), push it to a differently-named remote (expect allow); create a `clean`-named branch with an AI-tainted commit and confirm push is blocked with a clear message.
