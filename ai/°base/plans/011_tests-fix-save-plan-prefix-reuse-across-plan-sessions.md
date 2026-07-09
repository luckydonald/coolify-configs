# Tests + fix: save-plan prefix reuse across /plan sessions

## Context

After fixing the Stop false-positive bug, a second `/plan` command was issued in the same Claude Code session. The `save-plan` hook saw the same `session_id` mapped to prefix `010` (from the previous plan), treated the new plan as a slug-change update to the same plan, and renamed `010_fix-save-plan-…` → `010_tests-save-plan-…`. This destroyed the original plan file on disk and gave the wrong prefix to the new plan.

Root cause: `session_id` is stable across the entire Claude Code session. After ExitPlanMode fires (plan approved), the state entry is never cleared, so the next `/plan` invocation reuses the same prefix.

## Changes

### 1. `scripts/°base/ai/hooks/save-plan/hook.py` — mark session "done" after ExitPlanMode

After ExitPlanMode commits (or deduplicates), set a `"done"` flag on the session state entry:

```python
elif tool_name == "ExitPlanMode":
    plan = ...
    # --- existing commit logic ---
    # At the very end, mark this plan session as finalized:
    if session_id and session_id in state:
        state[session_id]["done"] = True
        _save_state(state)
```

In the Write-trigger branch's `if session:` block, check the flag BEFORE reusing the prefix:

```python
if session:
    if session.get("done"):
        session = None   # treat as a brand-new plan; allocate fresh prefix below
```

This means:
- Write → allocates 010, saves state (no "done" flag)
- ExitPlanMode (any rejection or approval) → commits/dedupes, sets `done=True`
- New Write in next `/plan` session → sees `done=True` → allocates 011 ✓
- Multiple ExitPlanMode iterations for the SAME plan with only `Edit` between them → no Write between them, flag only checked on Write → prefix unchanged ✓

### 2. Restore `010_fix-save-plan-…` and renumber test plan to `011_`

```bash
# Restore deleted plan from the last good commit
git checkout 977adc3 -- "ai/°base/plans/010_fix-save-plan-hook-stop-false-positive-and-routing.md"

# Rename test plan from 010_ to 011_
git mv "ai/°base/plans/010_tests-save-plan-hook-stop-false-positive-and-routing-fix-1-f.md" \
       "ai/°base/plans/011_tests-save-plan-hook-stop-false-positive-and-routing.md"
```

Commit both in one commit, amending the `3e3599e` auto-commit per lplp style.

### 3. `scripts/°base/tests/test_ai_hooks_base_routing.py` — new tests

All added to the existing test class. Infrastructure already available (`init_repo`, `run_hook`, `last_subject`, `PLAN_HOOK`).

**Fix 1 — Stop false positive (3 tests):**

`test_claude_stop_ignores_string_tool_response`  
Payload: `tool_name=""`, `tool_response="Exit code: 0\nSuccess."` (a string)  
Assert: no new commit, no plans dir created.

`test_claude_stop_ignores_dict_tool_response_without_plan`  
Payload: `tool_name=""`, `tool_response={"status": "ok"}` (dict, no "plan" or "filePath")  
Assert: no new commit.

`test_claude_exit_plan_mode_still_captures_plan_from_file_path`  
Payload: `tool_name="ExitPlanMode"`, `tool_response={"filePath": str(plan_file)}`  
Assert: `ai/°base/plans/001_*.md` created, correct content, commit made.

**Fix 2 — Prefix reuse (1 test):**

`test_new_plan_in_same_session_gets_fresh_prefix`  
Sequence:  
1. Write trigger with plan A → expect `001_plan-a.md`, `done=False` in state  
2. ExitPlanMode → expect `done=True` in state  
3. Write trigger with plan B (different content/slug) → expect `002_plan-b.md` (NOT `001_plan-b.md`)

## Verification

```bash
uv run --project scripts/°base python -m unittest \
  scripts.°base.tests.test_ai_hooks_base_routing -v 2>&1 | tail -20
```

All existing tests pass. Four new tests pass. `ai/°base/plans/` has both `010_fix-…` and `011_tests-…`.
