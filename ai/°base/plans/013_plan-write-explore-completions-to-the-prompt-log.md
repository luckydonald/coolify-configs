# Plan: Write Explore completions to the prompt log

## Context

When an Explore subagent finishes, the Claude Code harness injects a `<task-notification>` XML into the next `UserPromptSubmit` event, just like it does for Agent tasks. Currently `_handle_task_notification()` in `save-prompt/hook.py` handles all tasks as Agent tasks, writing to `agents/NNN.{id}/` with a "Task Notification:" header.

The goal is to detect Explore tasks and write them to `output/explore/NNN.{id}/` with the distinct format shown in `ai/°base/errors/9.expected.md`:

```
❯ Exploration <kbd>finished</kbd>:
> - > Explore record-memory hook and commit logic
> - [Answer (`1234` chars, `1234 B`)](output/explore/001.b5dyyqcfr/result.md)
> - [Raw log (`26915` chars, `26.5 KB`)](<abs-path>)
> - `33` tools · `46.9k` tokens · `1m 41s`
```

Input trigger (from `9.md`): `❯ Explore(Explore record-memory hook and commit logic)`

## File to modify

**`scripts/°base/ai/hooks/save-prompt/hook.py`** — all changes are here.  
**`scripts/°base/tests/test_ai_hooks_base_routing.py`** — one new test.

## Implementation steps

### 1. Extend `_parse_task_notification` to extract `<subagent-type>`

Add `"subagent_type": _text("subagent-type")` to the returned dict. This is the primary detection signal when the harness supplies it.

### 2. Add `_extract_explore_description(output_file, tool_use_id) -> str`

Scan the JSONL output file for a tool_use entry that is an Explore call:
- `name == "Agent"` **and** `input.subagent_type` is `"Explore"` (case-insensitive) → return `input.get("description", "") or input.get("prompt", "")[:120]`
- `name == "Explore"` → return `input.get("description", "")`

Return `""` if nothing found. Uses the same recursive `_iter_dicts` walk already in `_extract_agent_prompt`.

### 3. Add `_human_tokens(n_str) -> str`

```python
def _human_tokens(n_str: str) -> str:
    try:
        n = int(n_str)
    except ValueError:
        return n_str
    if n < 1000:
        return str(n)
    return f"{n / 1000:.3g}k"
```

Examples: `"46900"` → `"46.9k"`, `"67643"` → `"67.6k"`, `"500"` → `"500"`.

### 4. Add `_human_duration_ms(ms_str) -> str`

```python
def _human_duration_ms(ms_str: str) -> str:
    try:
        ms = int(ms_str)
    except ValueError:
        return ms_str
    s = ms // 1000
    m, s = divmod(s, 60)
    if m and s:
        return f"{m}m {s}s"
    if m:
        return f"{m}m"
    return f"{s}s"
```

Examples: `"101000"` → `"1m 41s"`, `"30000"` → `"30s"`, `"120000"` → `"2m"`.

### 5. Modify `_handle_task_notification` to branch on Explore

After the existing `info = _parse_task_notification(...)` check, add an Explore detection block **before** the existing Agent logic:

```python
explore_description = _extract_explore_description(info["output_file"], info["tool_use_id"])
is_explore = bool(
    explore_description
    or info.get("subagent_type", "").lower() == "explore"
)
```

**If `is_explore`:**

1. Route to `output/explore/` instead of `agents/`:
   ```python
   explore_dir = log_path.parent / "output" / "explore"
   num = _next_agent_number(explore_dir)
   dir_name = f"{num:03d}.{info['task_id']}"
   result_dir = explore_dir / dir_name
   result_dir.mkdir(parents=True, exist_ok=True)
   ```
2. Write only `result.md` (no `prompt.md`):
   ```python
   result_file = result_dir / "result.md"
   result_file.write_text(info["result"], encoding="utf-8")
   ```
3. Commit just `result.md` (same `--no-verify --only` pattern as Agent):
   ```python
   subprocess.run(["git", "add", "--", result_rel], capture_output=True)
   subprocess.run(["git", "commit", "--no-verify", "--only", result_rel,
                   "-m", base_ai_commit_subject(f"ai: explore {dir_name} result")],
                  capture_output=True)
   ```
4. Build Explore-format content and call `append_and_commit`:
   ```python
   rel_result = f"output/explore/{dir_name}/result.md"
   result_chars = len(info["result"])
   log_chars = _char_count(info["output_file"])
   log_size = _human_size(info["output_file"])
   usage = (
       f"> - `{info['tool_uses']}` tools"
       f" · `{_human_tokens(info['subagent_tokens'])}` tokens"
       f" · `{_human_duration_ms(info['duration_ms'])}`\n"
   )
   content = (
       f"{prefix} Exploration <kbd>finished</kbd>:\n"
       f"> - > {explore_description}\n"
       f"> - {_markdown_file_link('Answer', result_chars, _human_size(str(result_file)), rel_result)}\n"
       f"> - {_markdown_file_link('Raw log', log_chars, log_size, info['output_file'])}\n"
       f"{usage}\n"
   )
   ```

**Else:** existing Agent logic unchanged (no code changes to that path).

### 6. Add test `test_claude_explore_notification_writes_result_and_summary`

In `test_ai_hooks_base_routing.py`, modelled after the existing Agent test (`test_claude_task_notification_writes_agent_files_and_summary_metadata`):

- Output file JSONL has `name == "Agent"`, `input.subagent_type == "Explore"`, `input.description == "Explore record-memory hook and commit logic"`.
- Task notification XML uses `task-id = "b5dyyqcfr"`, status `"completed"`, result `"Done."`, usage `tool_uses=33 subagent_tokens=46900 duration_ms=101000`.
- Assert:
  - `ai/°base/output/explore/001.b5dyyqcfr/result.md` contains `"Done."`
  - `ai/°base/agents/` does **not** exist
  - `ai/°base/query.md` matches the format from `9.expected.md`
  - Last commit subject is `[base] ai: updated prompt`

## Verification

```bash
# Run all tests
uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v

# Run just the relevant test module
uv run --project scripts/°base python -m unittest ai.scripts.tests.test_ai_hooks_base_routing -v
```
