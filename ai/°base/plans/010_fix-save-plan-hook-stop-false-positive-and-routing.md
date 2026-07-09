# Fix: save-plan hook — Stop false positive and routing

## Session setup

Activate `/commit-with-lplp-style` before implementing. The test artifact commit `7ab548f` is a nearby `ai:` auto-commit that must be folded into the fix commit.

## Context

`save-plan/hook.py` fires on three triggers: `Write` (plan file written), `ExitPlanMode` (user approves), and `Stop` (session end — originally a Codex fallback). Two bugs were found:

1. **Stop hook commits garbage as plans.** The `else` branch in `main()` falls through for Stop events and calls `_plan_from_response(payload.get("tool_response"))`. For Stop, `tool_response` can be any string (e.g., last Bash output). `_plan_from_response` returns any string directly → gets committed as a "plan". This is how `ai/plans/001_exit-code-0.md` (content: Bash output) appeared in the base repo.

2. **Routing misses the base repo when `subproject` ≠ git root.** `_is_inside_base_repo(subproject)` checks `subproject.name == "base"`, where `subproject = CLAUDE_PROJECT_DIR`. If CLAUDE_PROJECT_DIR is set differently (symlinks, monorepo sub-path, or not set — falling back to pre-`_chdir_to_git_root` cwd), the name check fails → plans land in `ai/plans/` instead of `ai/°base/plans/`.

The `Write` trigger (which fires when Claude writes `~/.claude/plans/*.md`) works correctly — confirmed by dry-run: passing a `Write` payload with `file_path=/Users/user/.claude/plans/test.md` → committed `ai/°base/plans/009_test-plan.md` (test artifact, needs cleanup).

## Changes

### `scripts/°base/ai/hooks/save-plan/hook.py` — `main()`

**Fix 1 — Stop false positive:** Replace the generic `else` branch with an explicit `elif tool_name == "ExitPlanMode":`. Stop events for Claude produce no plan; they fall through cleanly.

```python
# Before:
elif tool_name == "Write":
    plan = _plan_from_write(tool_input)
else:
    plan = (tool_input.get("plan") or "").strip()
    if not plan:
        plan = _plan_from_response(payload.get("tool_response"))

# After:
elif tool_name == "Write":
    plan = _plan_from_write(tool_input)
elif tool_name == "ExitPlanMode":
    plan = (tool_input.get("plan") or "").strip()
    if not plan:
        plan = _plan_from_response(payload.get("tool_response"))
# Stop for Claude: no plan extraction — Write/ExitPlanMode already handled it.
```

### `scripts/°base/ai/hooks/_lib.py` — `resolve_log_path()`

**Fix 2 — Routing:** Capture the git root returned by `_chdir_to_git_root()` and check it as a fallback in `_is_inside_base_repo`. If either `subproject.name` or `git_root.name` is "base" AND the origin matches, route to `ai/°base/`.

```python
# Before:
_chdir_to_git_root()
is_base = _is_inside_base_repo(subproject)

# After:
git_root = _chdir_to_git_root()
is_base = _is_inside_base_repo(subproject) or _is_inside_base_repo(git_root)
```

`_is_inside_base_repo` signature is unchanged; the git root Path is a valid argument.

## Cleanup

Commit `7ab548f [base] ai: save plan 009_test-plan` is a test artifact. As part of implementing this fix, amend/fold that commit into the fix commit (lplp style). Also delete `ai/°base/plans/009_test-plan.md` from the working tree and index before the fix commit.

## Hook status (confirmed during planning)

- **save-prompt**: ✓ appended this session's prompt to `ai/°base/query.md`
- **save-plan (Write trigger)**: ✓ fired when plan file was written → `ai/°base/plans/010_*.md` committed
- **save-plan (Stop trigger)**: ✗ the bug — can commit garbage content as plans

## Verification

1. End a non-plan-mode session → confirm no spurious plan commit is created (Stop no longer calls `_plan_from_response`)
2. `git log --oneline -5 -- ai/plans/` shows nothing new after a normal session
3. Plan-mode session → Write trigger commits `ai/°base/plans/NNN_*.md` (already confirmed working)
