I have read all four functions in full. Here are the exact answers.

## 1. `cherry_pick_abort` — does it raise if no cherry-pick is in progress?

`/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/git_ops.py:246-247`

```python
def cherry_pick_abort(cwd: Path) -> None:
    subprocess.run(["git", "cherry-pick", "--abort"], cwd=cwd, capture_output=True)
```

No, it does not raise. There is no `check=True`, and there is no returncode inspection whatsoever — the `CompletedProcess` return value from `subprocess.run` isn't even captured (no variable assignment), it's just discarded. So if `git cherry-pick --abort` fails (e.g. exit code 128 because there's no `.git/CHERRY_PICK_HEAD` and git prints `fatal: no cherry-pick in progress`), the function silently swallows that — functionally equivalent to the `|| true` pattern in `recovery.py`'s `ABORT_COMMANDS`, just implemented via omission of `check=True` rather than a shell `||`.

`merge_abort` is written identically, same pattern, same conclusion:

`/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/git_ops.py:260-261`

```python
def merge_abort(cwd: Path) -> None:
    subprocess.run(["git", "merge", "--abort"], cwd=cwd, capture_output=True)
```

By contrast, `cherry_pick_continue` *does* return the `CompletedProcess` to the caller (so the caller can inspect `.returncode`), but it itself doesn't raise either:

`/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/git_ops.py:234-243`

```python
def cherry_pick_continue(cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_EDITOR"] = "true"
    return subprocess.run(
        ["git", "cherry-pick", "--continue"],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )
```

No `check=True` here either — it returns the `CompletedProcess` object itself, leaving returncode inspection entirely up to the caller.

## 2. `_cleanup_scratch`

`/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/history_master.py:135-143`

```python
def _cleanup_scratch(cwd: Path) -> None:
    """Detach off the scratch branch and delete it. Only call this once a
    step has *cleanly* finished -- never while a conflict is still open,
    since resuming (--continue) needs the scratch branch's mid-operation
    state (CHERRY_PICK_HEAD/MERGE_HEAD) to still be there.
    """
    tip = _head_sha(cwd)
    _git(["checkout", "--detach", tip], cwd, check=True)
    _delete_ref(SCRATCH_REF, cwd)
```

- It does **not** check out a fixed named branch — it checks out (detached) whatever `_head_sha(cwd)` currently resolves to, i.e. it detaches HEAD at the current tip commit (`git checkout --detach <tip>`), rather than checking out `_base_split_scratch` or `main`/`master` by name.
- It **does** delete the scratch ref/branch, via `_delete_ref(SCRATCH_REF, cwd)` (where `SCRATCH_REF`/`SCRATCH_BRANCH` refers to `_base_split_scratch`, per the earlier `_delete_ref(SCRATCH_REF, cwd)` / `git_ops.checkout_branch(SCRATCH_BRANCH, cwd)` calls at lines 130-132).
- It **does** handle the case where the current checkout is `_base_split_scratch`: since `tip = _head_sha(cwd)` reads HEAD *before* detaching, and (per the docstring) this is only meant to be called once the scratch branch's step "has cleanly finished," the detach happens off whatever commit the scratch branch currently points at — moving HEAD off the branch and into detached state at that same SHA — after which the branch ref itself is safe to delete without losing the commit (it's preserved by the detached HEAD, at least until something else moves HEAD or GC runs). There is no explicit branch-name check/guard beyond that ordering — it doesn't verify "is current branch == SCRATCH_BRANCH" before proceeding, it unconditionally detaches to current HEAD's sha and deletes the scratch ref.

## 3. `_read_state` / `_write_state` / `_clear_state` and state file path

`/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/history_master.py:31, 219-235`

```python
STATE_FILENAME = "BASE_SPLIT_HISTORY_MASTER_STATE"
...
def _state_path(repo_root: Path) -> Path:
    return repo_root / ".git" / STATE_FILENAME


def _read_state(repo_root: Path) -> dict | None:
    path = _state_path(repo_root)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write_state(repo_root: Path, state: dict) -> None:
    _state_path(repo_root).write_text(json.dumps(state, indent=2))


def _clear_state(repo_root: Path) -> None:
    _state_path(repo_root).unlink(missing_ok=True)
```

Confirmed: the state file lives at `<repo_root>/.git/BASE_SPLIT_HISTORY_MASTER_STATE`, i.e. inside the `.git` directory (not the worktree), named literally `BASE_SPLIT_HISTORY_MASTER_STATE`, holding JSON (`json.dumps(..., indent=2)` on write, `json.loads` on read). `_read_state` returns `None` if the file doesn't exist (no exception). `_clear_state` uses `unlink(missing_ok=True)`, so clearing a nonexistent state file is also a no-op, not an error.