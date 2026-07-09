# Recovery log: snapshot + undo commands for every mutating split.py run

## Context

Every mutating subcommand (`sync-splits`, `update-history-master`, `rebase-branches-to-master`, `bootstrap-branch`) moves branch refs, sometimes via real rebase/cherry-pick, sometimes via plumbing. If something goes wrong (a bad merge resolution, an unexpected duplicate, a conflict left mid-flight), there's currently no durable record of what the refs looked like beforehand or how to put them back. The user wants every run to record that up front, in both the terminal output and an append-only file, before touching anything — so recovery information survives even a crash mid-operation.

## Design

**New module `scripts/°base/git/°split_lib/recovery.py`**:

- `resolve_watched_refs(branch: str | None, main_branch: str, cwd: Path) -> list[str]` — the set of ref names a given invocation could plausibly touch: always `main_branch` and `branches.history_name(main_branch)`; if `branch` is given, also `branch`, `branches.unclean_name(branch)`, `branches.history_name(branch)`, `branches.history_fork_point_ref(branch)`, `sync_unclean.clean_cursor_ref(branch)`, `sync_unclean.history_cursor_ref(branch)`; if `branch` is `None` (bulk mode — subcommand omitted a branch and will process every `discover_unclean_branches()` result), union the above across all of them. Dedup, preserve order.
- `snapshot(refs: list[str], cwd: Path) -> dict[str, str | None]` — `git_ops.rev_parse` each (`None` if the ref doesn't exist yet).
- `format_recovery_entry(invocation: str, before: dict[str, str | None], timestamp: str) -> str` — builds the markdown block: a `#### Run _<timestamp>_ \`<invocation>\`` headline, a `Branch | Commit before` table, then a ```` ```shell ```` block with defensive abort lines (`git rebase --abort || true`, `git cherry-pick --abort || true`, `git merge --abort || true`) followed by one `git update-ref refs/heads/'<ref>' '<sha>'` per watched ref (or `git update-ref -d refs/heads/'<ref>' || true` for refs that don't exist yet, undoing their creation) — using the exact quoting style from the user's example.
- `format_after_summary(before: dict[str, str | None], after: dict[str, str | None]) -> str` — a `Branch | Commit before | Commit now` table (the 3-column shape from the user's example), stdout-only, printed once the operation finishes.
- `write_recovery_log(repo_root: Path, entry_markdown: str) -> None` — appends `entry_markdown` (plus a trailing blank line separator) to `repo_root / ".rebase-recovery.tmp"`. Already covered by the repo's existing `*.tmp` gitignore pattern, no gitignore change needed.

**`cli.py`**: a generic wrapper, `_run_with_recovery(*, repo_root, main_branch, branch, dry_run, invocation, run_fn)`:
1. If `dry_run`: just call `run_fn()` directly — nothing is mutated, nothing to record.
2. Otherwise: resolve watched refs, snapshot ("before"), build the recovery entry with a real timestamp, print it, append it to `.rebase-recovery.tmp` — all *before* calling `run_fn()`, so this is the crash-safe point.
3. Call `run_fn()` inside a `try`/`finally` so the after-summary prints even if it raises.
4. In the `finally`, snapshot again ("now") and print `format_after_summary`.

Wire this into `main()`'s four mutating dispatch branches (`sync-splits`, `update-history-master`, `rebase-branches-to-master`, `bootstrap-branch`), passing `branch=getattr(args, "branch", None)` and `invocation="scripts/°base/git/split.py " + " ".join(argv)`. `check-push` is untouched (it never mutates any ref).

## Tests

New `scripts/°base/tests/test_git_split_recovery.py`:
- `resolve_watched_refs` covers the single-branch case, the `branch=None` bulk case (union across multiple discovered unclean branches), and dedup.
- `snapshot` returns `None` for a nonexistent ref and the real sha for an existing one.
- `format_recovery_entry`/`format_after_summary` — pure string-shape tests: headline contains the timestamp and invocation string verbatim, the before-table lists every watched ref, the undo block contains the three abort lines plus one `update-ref` per ref with the correct old sha (or the delete form for refs that didn't exist).
- `write_recovery_log` appends across multiple calls (doesn't overwrite), each under its own headline.
- One CLI-level integration test: run `sync-splits` (or `bootstrap-branch`) end-to-end in a real temp repo, assert `.rebase-recovery.tmp` was created with the expected headline/table/undo commands *matching the pre-operation state*, and that manually executing the undo commands actually restores every touched ref to its original sha.

## Verification

1. `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v` — all pass.
2. Manual scratch-repo run: `sync-splits feature --direction=to-clean-history`, inspect `.rebase-recovery.tmp` and stdout, then literally run the printed undo commands and confirm every touched ref is back at its original sha.
