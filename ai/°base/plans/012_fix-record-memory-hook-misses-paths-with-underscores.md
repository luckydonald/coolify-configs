# Fix: record-memory hook misses paths with underscores

## Context

In the `archive_apps` Claude session (commit `ff301248`), Claude wrote four memory files to `~/.claude/projects/-Users-user-Documents-programming-Shell-archive-apps/memory/`. These writes fired the `PostToolUse` hook, but no git commits appeared in the repo.

**Root cause:** `_encoded_project_dir()` in `record-memory/hook.py` encodes the project path by replacing only `/` with `-`:

```python
encoded = str(subproject).replace("/", "-")
```

Claude Code's actual encoding replaces **all non-alphanumeric characters** (including `_`) with `-`. So for `CLAUDE_PROJECT_DIR=/Users/user/Documents/programming/Shell/archive_apps`, the hook computes:

```
~/.claude/projects/-Users-user-Documents-programming-Shell-archive_apps/memory/   ← hook computes
~/.claude/projects/-Users-user-Documents-programming-Shell-archive-apps/memory/   ← Claude actually wrote
```

When the `PostToolUse` handler runs `src_file.relative_to(src_dir.resolve())`, `src_dir` doesn't contain the written file → `ValueError` → silent `return 0`. No commit.

Confirmed empirically: `ls ~/.claude/projects/` shows `-Users-user-Documents-programming-Shell-archive-apps` (underscore became hyphen).

## Changes

### 1. Fix encoding in `scripts/°base/ai/hooks/record-memory/hook.py`

Add `import re` at the top (already has `import os`, `import subprocess`, `import sys`).

Change `_encoded_project_dir()` (lines 36–40):

```python
# Before
def _encoded_project_dir(subproject: Path) -> Path:
    encoded = str(subproject).replace("/", "-")
    return Path.home() / ".claude" / "projects" / encoded

# After
def _encoded_project_dir(subproject: Path) -> Path:
    encoded = re.sub(r"[^a-zA-Z0-9]", "-", str(subproject))
    return Path.home() / ".claude" / "projects" / encoded
```

### 2. Fix matching encoding in `scripts/°base/tests/test_ai_hooks_base_routing.py`

Three existing tests hard-code the old encoding using `.replace("/", "-")` at lines 653, 690, 714. Update all three to use the same regex so they stay in sync with the production code. Either inline `re.sub(r"[^a-zA-Z0-9]", "-", str(repo))` or extract a small helper at the top of the test file.

### 3. Add a regression test

Add a new test: `PostToolUse(Write)` with a project directory path that contains underscores (e.g., `my_project` as the repo dir name). The hook must:
- resolve the correct `src_dir` (with underscore replaced by `-`)
- detect the written file is inside it
- create the hardlink in `ai/memory/`
- commit `ai: record memory <name>`

This test would have *failed* before the fix and *passes* after.

## Verification

```bash
uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v
```

Focus on tests containing `memory` in their names. All three existing memory tests should still pass; the new underscore test must pass too.
