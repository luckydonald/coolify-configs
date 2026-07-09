## Findings

**No usage of Python's `logging` module exists anywhere in `scripts/°base/` (including `scripts/°base/git/°split_lib/`).**

I searched for all standard indicators of the `logging` module across every `.py` file in `scripts/°base` (excluding `.venv` and `__pycache__`):

- `import logging` — 0 matches
- `logging.getLogger` — 0 matches
- `logging.basicConfig` — 0 matches
- `logging.FileHandler` / `logging.StreamHandler` — 0 matches
- Any custom `Handler`/`Formatter`/`Filter` subclasses — 0 matches

The one incidental hit was a comment string, not code: `scripts/°base/tests/test_ai_hooks_base_routing.py:93` — a docstring/prompt text containing the word "logging" (`"...avoiding duplicate prompt logging."`), which is unrelated to Python's `logging` module.

I also confirmed there's no `pip`/`uv` dependency on `logging`-related third-party packages (no `requirements*.txt`; only `scripts/°base/pyproject.toml` exists, and it doesn't list any logging library).

### No file-handler + console logging setup anywhere

Since there's no `logging` module usage, there's naturally no `FileHandler`/dual console+file setup. I additionally grepped for manual file-based logging patterns (`.log` file writes, `logfile`, `log_file`, `log-file`) across the whole tree and found zero matches. No CLI script in this repo writes to a log file at all — neither via `logging.FileHandler` nor via manual `open(...'.log'...)` writes.

### print() is used for all output instead

`print()` is the sole output mechanism throughout the CLI tooling. In `scripts/°base/git/°split_lib/cli.py` — the entry point for the split tooling — `print()` is used both for normal stdout output and, with `file=sys.stderr`, for error/diagnostic output:

- `scripts/°base/git/°split_lib/cli.py:44` — `print(entry)`
- `scripts/°base/git/°split_lib/cli.py:51` — `print(recovery.format_after_summary(before, after))`
- `scripts/°base/git/°split_lib/cli.py:92` — `print("Push blocked by base branch-split policy:", file=sys.stderr)`
- `scripts/°base/git/°split_lib/cli.py:94` — `print(f"  - {violation}", file=sys.stderr)`
- `scripts/°base/git/°split_lib/cli.py:107` — `print(...)`
- `scripts/°base/git/°split_lib/cli.py:128` — `print(f"{branch}: {exc}", file=sys.stderr)`
- `scripts/°base/git/°split_lib/cli.py:131` — `print(f"{branch}: {result}")`
- `scripts/°base/git/°split_lib/cli.py:151` — `print(f"update-history-master: {exc}", file=sys.stderr)`
- `scripts/°base/git/°split_lib/cli.py:153` — `print(result)`
- `scripts/°base/git/°split_lib/cli.py:166` — `print(f"{branch}: {exc}", file=sys.stderr)`
- `scripts/°base/git/°split_lib/cli.py:169` — `print(f"{branch}: {result}")`
- `scripts/°base/git/°split_lib/cli.py:178` — `print(f"{args.branch}: {result['error']}", file=sys.stderr)`
- `scripts/°base/git/°split_lib/cli.py:180` — `print(f"{args.branch}: {result}")`

Notably, the other modules inside `°split_lib/` (`branches.py`, `classify.py`, `bootstrap.py`, `git_ops.py`, `sync_splits.py`, `sync_unclean.py`, `recovery.py`, `push_checks.py`, `rebase_to_master.py`, `history_master.py`, `identity.py`, `tree_ops.py`, `trailers.py`) contain **zero** `print()` calls themselves — they're pure library/logic modules that return values or raise exceptions, and only `cli.py` (the actual CLI entry point) does the printing. This is a clean separation, just built entirely on `print`/`sys.stderr` rather than `logging`.

Other CLI-style scripts elsewhere in `scripts/°base` follow the same pattern (`print()`-only output, no logging module), including:
- `scripts/°base/git/get-base.py`
- `scripts/°base/git/rebase_strip_claude_authorship.py`
- `scripts/°base/git/remote/fix_username.py`
- `scripts/°base/git/hooks/install/setup.py`
- `scripts/°base/git/hooks/commit/reject_co_authored_by.py`
- `scripts/°base/git/hooks/commit/require_memory_delete_marker.py`
- `scripts/°base/ai/hooks/merge_staged.py`, `save-decision/hook.py`, `save-prompt/hook.py`, `record-memory/hook.py`
- `scripts/°base/ai/memory/delete.py`
- `scripts/°base/ai/settings/°settings_lib/cli.py`, `codex_toml.py`
- `scripts/°base/ai/references/°dllink_lib/cli.py`

### Summary

There is no `logging` module usage (no `import logging`, no `getLogger`, no `basicConfig`, no custom handlers) anywhere in `scripts/°base/git/°split_lib/` or the broader `scripts/°base/` tree, and consequently no CLI script sets up dual console+file-handler logging. All diagnostic/status/error output across the split-tooling and the rest of the scripts directory is done exclusively via `print()`, with `file=sys.stderr` used as the convention for error-path messages (see `cli.py` lines above) instead of log levels.