# Plan: 4 AI hook improvements (committed separately)

## Context

Four improvements to the `ai/` hook suite, each committed separately using lplp-pipbuck style. Tasks in implementation order: `.debug` payload dumping → usage line fix → prompts-while-running fix → compact recording.

---

## Task 1 — `.debug` payload dumping

If `ai/.debug` (or `ai/°base/.debug` in the base repo) exists, every hook writes its raw stdin payload as a timestamped JSON file to `ai[/°base]/output/debug/`. Zero cost when flag absent.

### `_lib.py` — add `dump_debug_payload(payload, hook_name)`

```python
import datetime  # add to top-level imports

def dump_debug_payload(payload: dict, hook_name: str) -> None:
    subproject = _subproject_root()
    git_root_str = _git_text("rev-parse", "--show-toplevel")
    git_root = Path(git_root_str) if git_root_str else subproject
    is_base = _is_inside_base_repo(subproject) or _is_inside_base_repo(git_root)
    ai_prefix = "ai/°base" if is_base else "ai"
    if not (subproject / ai_prefix / ".debug").is_file():
        return
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S_%f")
    debug_dir = subproject / ai_prefix / "output" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / f"{ts}-{hook_name}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
```

### Four hooks — insert after `read_payload()`

Add `dump_debug_payload` to imports; call `dump_debug_payload(payload, "<hook-name>")` immediately after `payload = read_payload()` in: `save-prompt/hook.py`, `save-decision/hook.py`, `save-plan/hook.py`, `record-memory/hook.py`. The `record-memory` hook calls `_chdir_to_git_root()` before `read_payload()`, but `dump_debug_payload` uses `_subproject_root()` (reads `CLAUDE_PROJECT_DIR`) so chdir doesn't matter.

### Tests — add 3 to `test_ai_hooks_base_routing.py`

- `test_debug_payload_written_when_flag_exists` — non-base repo, `ai/.debug` exists → file in `ai/output/debug/` with valid JSON
- `test_debug_payload_not_written_without_flag` — no flag → `ai/output/debug/` absent
- `test_debug_payload_routes_to_base_prefix` — base-repo origin, `ai/°base/.debug` → file in `ai/°base/output/debug/` (NOT `ai/output/debug/`)

---

## Task 2 — Fix missing usage line in agent Task Notification

`_parse_task_notification` in `save-prompt/hook.py` reads `<usage>` children using underscore names (`usage/subagent_tokens`, `usage/tool_uses`, `usage/duration_ms`). Every other `<task-notification>` element uses hyphens; the `<usage>` children likely match that convention. Fix: try **both** underscore and hyphen variants with `_text("usage/subagent_tokens") or _text("usage/subagent-tokens")` etc.

The `_usage_summary(info)` function and the agent content template already exist — no other change needed.

---

## Task 3 — Fix prompts typed while an agent was running

When the user types a prompt while an agent runs, the text may be appended after `</task-notification>` in the same payload. `_handle_task_notification` returns True and the trailing text is silently dropped.

In `main()` of `save-prompt/hook.py`, before calling `_handle_task_notification`:
```python
remaining = ""
if "<task-notification>" in prompt:
    remaining = re.sub(r"<task-notification>.*?</task-notification>", "", prompt, flags=re.DOTALL).strip()
```
After `_handle_task_notification` returns True, if `remaining` is non-empty, append it as a separate prompt entry using `append_and_commit`.

---

## Task 4 — Record `/compact` results

Compact arrives as a plain-text prompt (no task-notification XML) starting with `/compact` and containing `⎿` lines. Current behavior: logged verbatim as `❯ /compact…`.

New behavior in `save-prompt/hook.py`:

**Detection** — `is_compact = prompt.strip().startswith("/compact") and "⎿" in prompt`

**Parse autoloads** — iterate lines: skip `/compact` and `⎿  Compacted`; for other `⎿` lines, strip the `⎿ ` prefix and reformat:
- `Read PATH (N lines)` → `- Read \`PATH\` (\`N\` lines)`
- `Referenced file PATH` → `- Referenced file \`PATH\``
- `Plan file referenced (PATH)` → `- Plan file referenced (\`PATH\`)`
- `Skills restored (NAME)` → `- Skills restored (\`NAME\`)`
- anything else → `- ITEM` (verbatim)

**Write files** — write `autoloads.md` to `output/compact/NNN/autoloads.md` (using `_next_agent_number(compact_dir)` for NNN). Commit the autoloads file separately (git add + commit). Skip `result.md` and raw-log (not available from plain text).

**query.md entry**:
```
❯ Conversation compacted:
> - [Autoload (`N` chars, `M B`)](output/compact/001/autoloads.md)
```

**SKIP_PROMPTS exclusion** — remove `/compact` from `SKIP_PROMPTS` (if present) so it reaches the new handler.

### Tests — add to `test_ai_hooks_base_routing.py`

- `test_compact_prompt_writes_autoloads_file` — simulate a `/compact` prompt with `⎿` lines; assert `output/compact/001/autoloads.md` exists with correctly formatted markdown, and `query.md` has the structured entry.
- `test_compact_autoloads_skips_compacted_line` — `⎿  Compacted` line is excluded from the autoloads file.
- `test_compact_sequential_numbering` — two compact prompts → `001/` and `002/`.

---

## Verification

```bash
uv run --project scripts/°base python -m unittest scripts.°base.tests.test_ai_hooks_base_routing -v
```

All existing 153 tests plus new ones must pass.
