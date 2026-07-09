# Three hook/commit-hygiene fixes

## Context

Three unrelated small issues, all in the AI-hook commit machinery:

1. Commit `a930d821373bae4ff8c59d7c6e63048b46567588` ("`[base] ai: errors/18.md`") added `ai/°base/errors/19.md` — the number in the message doesn't match the file. **Finding: this is not a hook bug.** There is no automated mechanism anywhere in this repo that generates `ai: errors/N.md`-style commit messages (`grep`-confirmed) — this message was hand-typed by an earlier session and simply miscounted. It's moot for current history anyway: this exact commit is no longer reachable from `HEAD` — an earlier rebase this session (fixing `ai/°base/errors/19.md`'s own reported bug) already folded it into a properly-named `ai: Plan: …` commit. **No code change needed here** — just confirming the cause since nothing needs fixing.

2. `record-memory`-style task-notification handling in `save-prompt/hook.py` commits agent/explore artifact files (`ai: agent <id> results` / `ai: explore <id> result`) in their own commit, then immediately makes a *second* commit appending the summary to `query.md` (`ai: updated prompt`). These should be one commit. The fix is mechanical: `append_and_commit()` (`scripts/°base/ai/hooks/_lib.py:234-258`) already supports bundling extra files into one commit via its `extra_paths` param — used today for the Copilot-issue-artifact case (`save-prompt/hook.py:281`) — but `_handle_task_notification`'s agent branch (`save-prompt/hook.py:651-706`) and explore branch (`607-649`) each do their own separate `git add`/`git commit` for the artifact file(s) *before* calling `append_and_commit()`, instead of passing them through `extra_paths`.

3. The progress/todo list shown during this session came from the newer `TaskCreate`/`TaskUpdate` tool, which isn't hooked into anything (`ai/tool-settings/settings.json`'s only todo-capture matcher is `"TodoWrite|update_todo"`). The existing capture (`_handle_todo_capture`, `save-plan/hook.py:343-378`) already injects a `## Todos` section into the session's saved plan file (`ai/°base/plans/NNN_*.md`) for the classic `TodoWrite`/`update_todo` tools. Per your answer: extend this to also fire on `TaskCreate`/`TaskUpdate`, updating the same in-plan `## Todos` section, with a new commit-message convention (`ai: Todo added` the first time a section is created, `ai: Todo updated` thereafter) replacing the current `ai: update todos in plan <prefix>_<slug>` message.

## Approach

### Fix 2 — merge agent/explore-artifact commit into the query.md commit

In `save-prompt/hook.py`'s `_handle_task_notification`:

- **Explore branch** (`607-649`): delete the separate `git add`/`git commit` for `result_file` (`619-624`). Change the `append_and_commit(...)` call (`643-648`) to pass `extra_paths=(result_file,)` and `default_commit_msg=f"ai: explore {dir_name} result"` (currently it uses the generic `default_commit_msg` parameter already threaded in from `main()`, i.e. `"ai: updated prompt"` — override it for this call site only).
- **Agent branch** (`651-706`): delete the separate `git add`/`git commit` for `prompt_file`/`result_file` (`672-680`). Change the `append_and_commit(...)` call (`700-705`) to pass `extra_paths=(prompt_file, result_file)` and `default_commit_msg=f"ai: agent {dir_name} results"`.

**The merged commit's message must always be this more-interesting results/explore message, never "ai: updated prompt"** — that's the whole point of the fix, not an incidental default. Watch out for `append_and_commit`'s `commit_template_relpath="ai/commit-templates/prompt.md"` argument (still passed at both call sites): `_commit_message()` (`_lib.py:199-206`) uses that template's content **instead of** `default_commit_msg` whenever the template file exists, which would silently clobber our chosen message if such a template is ever present. Since these two call sites are committing an agent/explore result, not a plain prompt, pass `commit_template_relpath=""` (or another path that's guaranteed not to exist) for both, so `default_commit_msg` always wins regardless of any prompt-commit template.

No changes to `append_and_commit()` itself — it already handles multiple `extra_paths` and staging/committing them together (`_lib.py:254-257`).

**Tests** (`scripts/°base/tests/test_ai_hooks_base_routing.py`):
- `test_claude_task_notification_writes_agent_files_and_summary_metadata` (line 448) and `test_claude_explore_notification_writes_result_and_summary` (line 575) currently assert `last_subject(repo) == "[base] ai: updated prompt"`. Update both to assert the merged subject (`f"[base] ai: agent {dir_name} results"` / `f"[base] ai: explore {dir_name} result"`) and additionally assert there is exactly **one** commit on top of `init` (e.g. compare `git log --oneline` count, or check `HEAD^` is the `init` commit) to prove the two writes really landed in a single commit, not two.
- Re-run `test_claude_task_notification_trailing_prompt_logged_separately` (line 544) unchanged — that one covers a distinct trailing-text-after-notification path and shouldn't be affected, but confirm it still passes.

### Fix 3 — capture TaskCreate/TaskUpdate into the plan's `## Todos` section

**Payload shape is unknown — determine it empirically first**, the same way the Copilot `ask_user` payload shape was nailed down earlier this session (real captured `.debug` JSON, not guesswork): enable `ai/°base/.debug`, run a couple of `TaskCreate`/`TaskUpdate` calls, and inspect the resulting `ai/°base/output/debug/*-save-plan.json` payloads (`dump_debug_payload` already wired into `save-plan/hook.py`'s `main()`). Unlike `TodoWrite`/`update_todo` (which resend the *entire* current list every call), `TaskCreate`/`TaskUpdate` almost certainly mutate **one task at a time** (create one; update one by `taskId`) — so a single event's `tool_input` can't render a full `## Todos` section on its own.

Plan (subject to what the captured payloads actually show):
- Extend the existing per-session state file (`_load_state()`/`_save_state()`, `save-plan/hook.py:30-45`, already keyed by `session_id`) with a `tasks: {taskId: {subject, status, activeForm}}` map per session, updated incrementally: `TaskCreate`'s payload adds an entry, `TaskUpdate`'s payload merges into the existing entry for its `taskId`.
- Render the full `## Todos` section from that accumulated map (reusing `_render_todos_markdown`, generalizing `_normalize_todos` to also build its flat list from this map when the triggering tool is `TaskCreate`/`TaskUpdate`) — same `_apply_todos_section`/idempotent-replace logic as today, no changes needed there.
- Wire `"TaskCreate", "TaskUpdate"` into `main()`'s dispatch (`save-plan/hook.py:392-393`) alongside `"TodoWrite", "update_todo"`.
- Add the matcher to `ai/tool-settings/settings.json` (new `PostToolUse` entry, matcher `"TaskCreate|TaskUpdate"`, same `save-plan/hook.py` command as the existing `TodoWrite|update_todo` entry) and re-run `sync.py`.

**Commit message convention change** (applies to *all* todo-capture commits, not just the new tools): replace `_handle_todo_capture`'s current message (`f"ai: update todos in plan {prefix}_{slug}"`, line 374) with:
- `"ai: Todo added"` when `_apply_todos_section` is creating the `## Todos` section for the first time (i.e. the heading wasn't present in `current` before the edit).
- `"ai: Todo updated"` when it already existed and is being replaced.

Determine which case applies by checking for the heading in `current` (the pre-edit plan text) before calling `_apply_todos_section`, right where `_handle_todo_capture` already has `current = plan_path.read_text(...)` (line 366).

**Tests**:
- `scripts/°base/tests/test_save_plan_todo_capture.py`: update the existing `HandleTodoCaptureTests` assertions for the new commit-message strings (`"ai: Todo added"` / `"ai: Todo updated"`, through `base_ai_commit_subject`); add cases proving the added-vs-updated distinction (first write → "added", second write to the same section → "updated").
- Add new tests for the `TaskCreate`/`TaskUpdate` path once the real payload shape is known — one task created (renders a single-item `## Todos` section, commit "ai: Todo added"), a second task created (section now has two items), then a `TaskUpdate` changing one task's status (section reflects the new status, commit "ai: Todo updated").

## Verification

```bash
uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v
python3 scripts/°base/ai/settings/sync.py --check
```

Manually: touch `ai/°base/.debug`, run a couple of real `TaskCreate`/`TaskUpdate` calls in a scratch session, confirm `ai/°base/plans/<current-session-plan>.md` grows a correct `## Todos` section and each mutation lands as its own `ai: Todo added`/`ai: Todo updated` commit; separately, trigger a real subagent/Explore task-notification and confirm `git log` shows one combined commit (not two) for its artifact files + query.md entry.

## Todos

- [x] 
- [x] 
- [x] 
- [x] 
- [x] Final live verification task
