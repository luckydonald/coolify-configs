# Fix: memory-file deletions never propagate to the repo mirror

## Context

`scripts/°base/ai/hooks/record-memory/hook.py` mirrors Claude Code's per-project
memory files between an external directory
(`~/.claude/projects/<encoded-repo-path>/memory/<name>.md`) and a git-tracked
mirror inside the repo (`ai/°base/memory/<name>.md`, or `ai/memory/...` for
consuming repos), via hardlink (falling back to symlink cross-filesystem). It
only fires on `PostToolUse` matcher `"Write|Edit"` and on `SessionStart`
(`ai/tool-settings/settings.json`).

Earlier this session, a memory file was deleted from the external directory
via a plain `rm` (Bash tool) instead of the sanctioned deletion helper. Since
`record-memory` never listens for `Bash`, nothing observed the deletion — the
repo mirror was left stale, had to be cleaned up by hand (`git rm`), and that
deletion commit lacked the required `Deleted Memory: <name>.md` marker and
later got folded away during an unrelated history squash. This is exactly the
gap the user asked to fix: hook into the deletion itself and properly
propagate it to the repo mirror.

**Why this isn't just "make missing-source mean delete"**: the hook used to
work that way and it caused a real data-loss incident (commit `d1b384a`) — an
encoding bug in path resolution made the hook think the source dir was empty,
and it deleted real, wanted memory files from the repo. The fix (plan
`ai/°base/plans/007_prevent-accidental-memory-deletion.md`, commit `e12db5f`)
made the **repo copy authoritative**: `_sync_all()`'s `SessionStart` resync
never deletes the repo mirror from absence alone — a missing external file is
*recreated* from the repo copy instead. The only sanctioned way to originate a
deletion is `scripts/°base/ai/memory/delete.py`, which unlinks both copies and
commits the repo-side deletion with a `Deleted Memory: <name>.md` marker (a
pre-commit hook, `require_memory_delete_marker.py`, enforces this marker on
any commit that deletes a memory `.md` file). The bug is that `delete.py` is
never invoked automatically by anything — ordinary `rm` usage (on the
*external* file, under `$HOME/.claude/projects/<encoded>/memory/`, never
inside the repo itself) bypasses it entirely, so deletions silently never
propagate, and the *next* `SessionStart` would even resurrect the external
file from the (stale, should-be-deleted) repo copy.

**The fix**: add a narrow, *reactive* detection — when the `Bash` tool runs a
command whose parsed argv includes an `rm` of an absolute `.md` path living
directly under the external memory source dir, and that file is confirmed
gone from disk right after, treat it as a deliberate deletion signal (this is
categorically different and much safer than the old "resync sees absence"
signal that caused the incident — it's triggered by an actually-observed `rm`
of a specific named file, not by inferring intent from ambient directory
state) and propagate it into the repo mirror using the exact same mechanism
`delete.py` already uses. `_sync_all()`/`SessionStart` behavior is untouched.

## Approach

### 1. New `°memory_lib` package (not `_lib.py`)

Follow the existing `°reffiles_lib` pattern (`scripts/°base/ai/hooks/°reffiles_lib/`)
rather than growing the flat `_lib.py`: add
`scripts/°base/ai/hooks/°memory_lib/__init__.py` + `delete.py`, imported via
`importlib.import_module("°memory_lib")` (same non-ASCII-package trick already
used for `°reffiles_lib`/`°split_lib`). Public functions get plain names, no
leading underscore — matching `°reffiles_lib`'s `is_tracked`/
`handle_referenced_files` style, not `_lib.py`'s underscore-prefixed
"quasi-private" style.

`scripts/°base/ai/hooks/°memory_lib/delete.py`:
```python
def is_tracked(relpath: str) -> bool: ...          # git ls-files --error-unmatch
def unlink_path(path: Path) -> None: ...           # unlink if symlink or exists
def delete_memory(name: str, *, src_dir: Path, dst_dir: Path, dst_dir_rel: str) -> bool:
    """Unlink both copies of memory `name` and commit the repo-side deletion
    with the required `Deleted Memory: <name>` marker. Returns True if a
    commit was made (False if the repo copy isn't tracked -- nothing to do)."""
```
Body ports `delete.py`'s existing sequence (current `delete.py:36-46, 68-91`)
verbatim: `git add -- <dst_rel>`, then
`git commit --only <dst_rel> -m <subject> -m "Deleted Memory: <name>"`, subject
via `base_ai_commit_subject(...)` (still imported from `_lib.py` — importing
*from* `_lib.py` stays fine, matching how `°reffiles_lib/commit.py` already
does the same; the instruction is not to add new logic to `_lib.py` itself).
`__init__.py` re-exports `delete_memory` (and `is_tracked`/`unlink_path` if
useful directly).

### 2. Refactor `scripts/°base/ai/memory/delete.py` to call it

`main()` keeps CLI arg validation, `_memory_dirs()`/`_subproject_root()`
resolution, and the final "Deleted memory X in `<sha>`" print, but delegates
the actual unlink+commit to `memory_lib.delete_memory(...)`, using its `False`
return for the existing "Memory is not tracked" early-exit message. Behavior
must stay identical — all of `test_memory_delete.py` passes unchanged.

### 3. Detect Bash `rm` of a source memory file in `record-memory/hook.py`

Add a small **local** parser to `hook.py` (stays underscore-prefixed, matching
this file's existing private-helper style — only the shared `°memory_lib` API
avoids underscores) using the same technique as
`.claude/hooks/permission-check.py`'s `split_on_shell_operators` +
`shlex.split` (don't reinvent differently, keep it local since that's a
different hook dir/sys.path setup):

```python
def _rm_targets(command: str) -> list[Path]:
    """Absolute .md paths passed as plain arguments to an `rm` invocation
    anywhere in `command` (chained via &&/||/;). Expands `$HOME`/`~` since the
    shell would have. Relative paths are skipped -- no reliable view of the
    Bash tool's cwd."""
```
Logic: `shlex.split(command)`, split into sub-argvs on `{"&&", "||", ";"}`
(copy the ~10-line loop from `split_on_shell_operators`), for each sub-argv
with `argv[0] == "rm"`: walk remaining tokens, skip ones starting with `-` up
to a bare `--` (flags), for the rest apply `os.path.expandvars(token)` then
`Path(...).expanduser()`; keep only absolute paths ending in `.md`.

In `main()`'s `PostToolUse` branch (current `hook.py:323-335`), branch on
payload shape: try `tool_input.get("command")` (Bash-shaped) first; if
present, for each path from `_rm_targets(command)` whose
`.resolve().parent == src_dir.resolve()` and which is now confirmed absent
(`not path.exists()`), call
`memory_lib.delete_memory(path.name, src_dir=src_dir, dst_dir=dst_dir, dst_dir_rel=dst_dir_rel)`.
Otherwise fall back to today's `tool_input.get("file_path")` handling
(Write/Edit-shaped, unchanged). No matches → no-op, same as today. This only
ever *removes* — never creates/recreates, and never touches `_sync_all()`/the
`SessionStart` path.

### 4. Wire the new matcher

`ai/tool-settings/settings.json`: widen the existing `record-memory`
`PostToolUse` entry's matcher from `"Write|Edit"` to
`"Write|Edit|Bash|shell|unified_exec"` (same Bash-family pattern already used
by the `PermissionRequest` entry for `permission-check.py`) — one hooks array,
not a duplicate entry, since `hook.py` now branches on payload shape itself.

Run `python3 scripts/°base/ai/settings/sync.py` afterward and confirm
`.claude/settings.json`, `.codex/hooks.json`, and `.github/hooks/generated.json`
all pick up the widened matcher (`sync.py --check` must pass clean).

### 5. Doc touch-up

`ai/°base/AGENTS.md`'s hook table (~line 105): update the `record-memory`
row's trigger column from `` `Write`, `Edit`, `SessionStart` `` to
`` `Write`, `Edit`, `Bash`, `SessionStart` ``.

## Tests

`scripts/°base/tests/test_ai_hooks_base_routing.py` (reuse `run_hook()`,
`init_repo()`, `_encode_project_path()`, `last_subject()`; follow the existing
`test_memory_*` tests' setup style — a temp `home` dir standing in for
`$HOME`, passed via `extra_env={"HOME": str(home)}`). **The `rm` target in
every new test is the external source path
`home / ".claude" / "projects" / <encoded> / "memory" / "<name>.md"` —
never a path inside the repo.**

- **Bash `rm` of a tracked mirror's source file → mirror deleted with
  marker.** Seed a repo-tracked mirror file + matching external source file
  under the fake `$HOME`; fire
  `{"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": f'rm "{src_file}"'}}`
  with `extra_env={"HOME": str(home)}`; assert the mirror file no longer
  exists and `git log -1 --format=%B` contains a standalone
  `Deleted Memory: <name>.md` line.
- **`rm` of a `.md` file outside the source dir → no-op.** Some other
  absolute path (e.g. under the repo, or an unrelated `$HOME` subdir); assert
  no commit, mirror untouched.
- **Non-`rm` command mentioning a `.md` filename (e.g. `cat "<src_file>"`) →
  no-op.**
- **`rm` of a source file with no tracked repo mirror → no-op**, no crash.
- **Chained command (`rm "<src_file>" && echo done`) → still detected.**
- Existing `test_memory_session_start_restores_missing_claude_source_from_repo`
  and `test_memory_session_start_does_not_resurrect_marked_deleted_memory`
  must keep passing unchanged — confirms `SessionStart`/`_sync_all` behavior
  is untouched.

`scripts/°base/tests/test_memory_delete.py`: run as-is after the refactor —
all existing cases must still pass, confirming `delete.py`'s externally
observable behavior didn't change.

Run:
```bash
uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v
python3 scripts/°base/ai/settings/sync.py --check
```

## Manual verification

In a scratch temp repo (or reuse the test helpers interactively): seed a
tracked `ai/°base/memory/<name>.md` plus a matching external source file
under a fake `$HOME`, pipe a synthetic `PostToolUse`/`Bash`
`rm "<home>/.claude/projects/<encoded>/memory/<name>.md"` payload into
`record-memory/hook.py` directly, and confirm the mirror file is gone and
`git log -1` shows the `Deleted Memory:` marker — reproducing the exact
scenario from this session, now handled automatically instead of requiring a
manual `git rm` + hand-written marker.
