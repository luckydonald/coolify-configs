This confirms exactly what happened. Now I have everything needed to answer comprehensively.

## Findings

### 1. cli.py: where `update_history_master()`'s result gets handled

File: `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/cli.py:137-154`

```python
def _update_history_master(args: argparse.Namespace, *, repo_root: Path, main_branch: str) -> int:
    try:
        result = history_master_lib.update_history_master(
            repo_root=repo_root,
            main_branch=main_branch,
            force_merge=args.force_merge,
            pull_master=args.pull_master,
            pull_base=args.pull_base,
            yes=args.yes,
            continue_=args.continue_,
            abort=args.abort,
            dry_run=args.dry_run,
        )
    except history_master_lib.HistoryMasterError as exc:
        print(f"update-history-master: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0 if result.get("status") != "conflict" else 1
```

**It just `print(result)`** — the raw Python dict repr. There is no branch that inspects `result["status"] == "conflict"` and formats a helpful message; that check is only used to pick the exit code (line 154). The only place a nicely-formatted message would appear is the `except HistoryMasterError` branch (line 150-152) — but that branch is never hit for conflicts, because (as shown below) conflicts are returned as dicts, not raised as exceptions.

This exactly matches the errors/18.md output: line 109 shows the literal printed dict:
```
{'status': 'conflict', 'pending': {'kind': 'cherry-pick', 'step': {'kind': 'commit', 'sha': '7afd08be7fc178abd1744706c781b583ce6f69d9'}}}
```
with zero instructions on what to do next.

### 2. `CherryPickConflict` and `MergeConflict` in history_master.py — defined but never raised in the live code path

File: `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/history_master.py:41-72`

```python
class CherryPickConflict(HistoryMasterError):
    """An ordinary commit replay conflicted and needs manual resolution.

    Carries enough info for a CLI layer to print recovery instructions and
    for update_history_master to persist resumable state.
    """

    def __init__(self, sha: str, onto: str, stdout: str, stderr: str) -> None:
        super().__init__(
            f"Cherry-pick of {sha} onto {onto} conflicted.\n"
            "Resolve the conflict in the working tree (currently checked out "
            f"on {SCRATCH_BRANCH!r}), `git add` the resolved paths, then rerun "
            "update-history-master with --continue (or --abort to cancel).\n"
            f"{stderr or stdout}"
        )
        self.sha = sha
        self.onto = onto


class MergeConflict(HistoryMasterError):
    """A fresh (non-recreation) merge conflicted and needs manual resolution."""

    def __init__(self, sha: str, onto: str, stderr: str) -> None:
        super().__init__(
            f"Merge of {sha} onto {onto} conflicted and could not be "
            "auto-resolved (only base-merge *recreation* resolves "
            "automatically). Resolve conflicts, `git add` the resolved paths, "
            "then rerun update-history-master with --continue (or --abort).\n"
            f"{stderr}"
        )
        self.sha = sha
        self.onto = onto
```

Both subclass `HistoryMasterError`, and both **are raised** — inside the low-level primitives:
- `CherryPickConflict` raised at `history_master.py:273` in `replay_commit()`
- `MergeConflict` raised at `history_master.py:354` in `_fold_base()`

But they are immediately **caught locally and swallowed** by `_run_steps()`:

```python
def _run_steps(steps: list[dict], tip: str, cwd: Path) -> tuple[str, list[dict], dict | None]:
    remaining = list(steps)
    while remaining:
        step = remaining[0]
        try:
            tip = _execute_step(step, tip, cwd)
        except CherryPickConflict:
            return tip, remaining, {"kind": "cherry-pick", "step": step}
        except MergeConflict:
            return tip, remaining, {"kind": "merge", "step": step}
        remaining.pop(0)
    return tip, [], None
```
(`history_master.py:426-437`)

The `except CherryPickConflict:` and `except MergeConflict:` clauses discard the exception object entirely (no `as exc`), throwing away its carefully-crafted `.args[0]` message string, and return a bare `{"kind": ..., "step": step}` dict instead. This propagates up through `update_history_master()` (`history_master.py:669-681`) as `{"status": "conflict", "pending": conflict}` — the exception's helpful message text never reaches the CLI or the user. So the answer is: **yes, they're raised, but caught-and-discarded internally; the code returns the conflict dict directly without ever printing that message.**

### 3. `_run_with_recovery` in cli.py — recovery block is unconditional, before/after unconditional too

File: `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/cli.py:24-51`

```python
def _run_with_recovery(
    *,
    repo_root: Path,
    main_branch: str,
    branch: str | None,
    dry_run: bool,
    invocation: str,
    run_fn: Callable[[], int],
) -> int:
    if dry_run:
        return run_fn()

    watched = recovery.resolve_watched_refs(branch, main_branch, repo_root)
    before = recovery.snapshot(watched, repo_root)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = recovery.format_recovery_entry(invocation, before, timestamp)
    print(entry)
    recovery.write_recovery_log(repo_root, entry)

    try:
        return run_fn()
    finally:
        after = recovery.snapshot(watched, repo_root)
        print(recovery.format_after_summary(before, after))
```

- The "before" recovery block (with undo `git update-ref` commands) is printed and logged **unconditionally, before** `run_fn()` executes — matches the "before" table + abort/undo shell block at the top of errors/18.md.
- `run_fn()` (which is `_update_history_master`, containing the raw `print(result)`) executes in between — that's where line 109's raw dict is printed.
- The "after" summary is printed **unconditionally in a `finally`** block regardless of success/conflict — matches the second table at the bottom of errors/18.md.
- There is no exception-based dispatch here that would catch a conflict and print something different; `_run_with_recovery` doesn't even look at the return value's contents, only passes through the int exit code.

### 4. Tracing `_do_continue`, guard/state-file logic, and what literally happened for commit `7afd08be`

State-file mechanics, `history_master.py:219-236`:
```python
def _state_path(repo_root: Path) -> Path:
    return repo_root / ".git" / STATE_FILENAME

def _read_state(repo_root: Path) -> dict | None: ...
def _write_state(repo_root: Path, state: dict) -> None:
    _state_path(repo_root).write_text(json.dumps(state, indent=2))
def _clear_state(repo_root: Path) -> None:
    _state_path(repo_root).unlink(missing_ok=True)
```

Guard against a second concurrent run, `update_history_master()` (`history_master.py:630-634`):
```python
if _read_state(repo_root) is not None:
    raise HistoryMasterError(
        "A previous update-history-master run is mid-conflict. "
        "Run with --continue to resume, or --abort to cancel."
    )
```
This *is* a proper raise → caught by cli.py's `except history_master_lib.HistoryMasterError as exc: print(f"update-history-master: {exc}", ...)`. So a second bare run after a conflict *would* get a nice stderr message telling the user to `--continue`/`--abort` — but that's a different code path than the original conflict itself.

Initial conflict write-on-first-run, `update_history_master()` (`history_master.py:669-681`):
```python
tip, remaining, conflict = _run_steps(steps, replay_start_tip, cwd)
if conflict is not None:
    _write_state(
        repo_root,
        {
            "remaining": remaining,
            "tip": tip,
            "force_merge": force_merge,
            "original_sha": old_history_sha,
            "pending": conflict,
        },
    )
    return {"status": "conflict", "pending": conflict}
```

`_do_continue` (`history_master.py:544-604`) — used only on a subsequent `--continue` invocation, reads back that same state file, resumes the cherry-pick/merge, re-raises the same "conflict" dict pattern if it's still conflicted, and only clears the state file (`_clear_state`) once everything finishes cleanly.

**Exact path that produced errors/18.md** for the `--yes` run hitting `7afd08be`:

1. `cli.main()` → `update-history-master` branch (cli.py:257-267) calls `_run_with_recovery(..., run_fn=lambda: _update_history_master(...))`.
2. `_run_with_recovery` prints/logs the "before" recovery block (the top table + undo commands in errors/18.md) — unconditional, done before anything happens.
3. `run_fn()` → `_update_history_master(args, ...)` → `history_master_lib.update_history_master(..., yes=True, ...)`.
4. Inside `update_history_master`: no pre-existing state file, so it proceeds; `_build_plan` produces the step list including `{"kind": "commit", "sha": "7afd08be..."}`; `_run_steps` calls `_execute_step` → `replay_commit("7afd08be...", tip, cwd)`.
5. `replay_commit` (`history_master.py:258-276`) does `git_ops.cherry_pick(sha, cwd)`; it fails with a real conflict, so it raises `CherryPickConflict(sha, onto, result.stdout, result.stderr)` **(line 273)** — this exception object carries the full "Resolve the conflict... rerun update-history-master with --continue..." message.
6. `_run_steps` (line 432-433) catches that exception via `except CherryPickConflict:` and **discards it**, returning only `{"kind": "cherry-pick", "step": step}` — the helpful `.args[0]` message text is thrown away here.
7. `update_history_master` (line 670-681) sees `conflict is not None`, calls `_write_state(...)` to persist `.git/BASE_SPLIT_HISTORY_MASTER_STATE` (**so yes, item (a) — the state file for `--continue` IS written**), and returns `{"status": "conflict", "pending": conflict}`.
8. Back in `_update_history_master` (cli.py:137-154), `result` is that dict; no `HistoryMasterError` was raised (it was caught deep inside `_run_steps`), so the `except` block is never entered. Code falls through to `print(result)` (cli.py:153) — printing the raw dict shown at errors/18.md line 109 — and returns exit code `1` (line 154).
9. `_run_with_recovery`'s `finally` block then prints the "after" summary table (bottom of errors/18.md).

**Answer to 4(a)/(b)/(c):**
- (a) **Yes** — the state file is written for later `--continue` (`_write_state` at history_master.py:671-680).
- (b) **No** — the `CherryPickConflict` message (with the actual instructions: "Resolve the conflict in the working tree..., then rerun update-history-master with --continue") is constructed and raised, but is caught-and-discarded by `_run_steps`'s bare `except CherryPickConflict:` clause and never surfaces anywhere.
- (c) **Yes, this is exactly what happens** — cli.py's `print(result)` just dumps the raw `{'status': 'conflict', 'pending': {'kind': 'cherry-pick', 'step': {'kind': 'commit', 'sha': '7afd08be...'}}}` dict to stdout with no explanation of what a "pending cherry-pick step" even means or what command to run next. That's precisely why the user was confused about how to recover — the underlying exception classes already contain the right instructions, but that text is discarded in `_run_steps` (history_master.py:432-435) instead of being threaded through into the returned dict (or printed by the cli.py layer).