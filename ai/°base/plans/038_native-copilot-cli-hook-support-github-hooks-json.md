# Native Copilot CLI hook support (`.github/hooks/*.json`)

## Problem

`scripts/°base/ai/hooks/` (save-plan, save-decision, save-prompt, record-memory)
is wired up today only through `.claude/settings.json` (rendered by
`scripts/°base/ai/settings/`) and `.codex/hooks.json`. Copilot CLI *can*
technically read `.claude/settings.json` cross-tool, but the user wants a
**proper, native** `.github/hooks/*.json` file instead — a third rendered
target, analogous to how Codex gets `.codex/hooks.json`, not a reuse of
Claude's file.

## Research findings (confirmed against official docs, fetched into
`ai/references/https/docs.github.com/...`)

- Copilot CLI hook config: `{"version": 1, "hooks": {...}}`, loaded from
  `.github/hooks/*.json` (repo-level, all files combined) and
  `~/.copilot/hooks/*.json` (user-level).
- Event names can be configured in **PascalCase** ("VS Code compatible"
  format) and then the payload uses the *same snake_case field names as
  Claude Code* (`session_id`, `tool_name`, `tool_input`, `tool_response`/
  `tool_result`, `hook_event_name`, etc.) — this is essentially a superset of
  Claude's format. Confirmed PascalCase/camelCase event pairs we need:
  `SessionStart`/`sessionStart`, `UserPromptSubmit`/`userPromptSubmitted`,
  `PostToolUse`/`postToolUse`, `Stop`/`agentStop`.
- Runtime → Claude tool-name table (applies at least to `PreToolUse`/
  `PermissionRequest`; **not confirmed** for `PostToolUse`, so we should match
  defensively on both names): `bash`→`Bash`, `view`→`Read`, `create`→`Write`,
  `edit`/`str_replace_editor`/`apply_patch`→`Edit`, `ask_user`→
  `AskUserQuestion`, `task`→`Agent`/`Task`. **`exit_plan_mode` has no Claude
  equivalent and keeps its runtime (lowercase) name** — this breaks the
  existing `ExitPlanMode` matcher/tool_name checks in save-plan/hook.py.
- Copilot's `ask_user` tool schema (`question: string`, `choices: string[]`,
  `allow_freeform: bool`) is **materially simpler** than Claude's
  `AskUserQuestion` (`questions[]` with `options[]` incl. label/description/
  preview, `multiSelect`). save-decision/hook.py's `_parse_claude` will not
  correctly parse Copilot's payload — needs a dedicated parser.
- Copilot's plan file lives at `~/.copilot/session-state/<session_id>/plan.md`
  (this session's own plan file proves the path), not
  `~/.claude/plans/*.md` — save-plan/hook.py's path regex needs to accept both.
- Copilot command-hook shape differs from Claude's: `{"type":"command",
  "bash": "...", "powershell": "...", "cwd", "env", "timeoutSec"}` vs Claude's
  `{"type":"command","command":"...","timeout","statusMessage","async"}`.
  There is **no `async`/fire-and-forget flag** for arbitrary command hooks in
  Copilot (only the built-in `notification` event is inherently
  fire-and-forget) — the `async: true` entries in today's Claude config just
  get dropped for the Copilot render.
- **Confirmed via `ai/references/https/docs.github.com/en/copilot/concepts/agents/copilot-memory.md`:**
  Copilot Memory is a **cloud/server-side** feature — repository-level facts
  and user-level preferences stored server-side with citations, viewable only
  via `github.com/settings/copilot/memory` or admin export, auto-expiring
  after 28 days of disuse. There is **no local file representation at all**
  (unlike Claude's per-project `~/.claude/projects/<encoded>/memory/*.md`).
  This definitively rules out a file-hardlink sync for Copilot — there is
  nothing on disk to link. Decision (final): `record-memory/hook.py` stays
  Claude-only; it safely no-ops under Copilot since the source directory it
  looks for will simply never exist.
  **Further confirmed** via
  `.../copilot-memory/manage-as-administrator.md`: the only export path is a
  web-UI-triggered, admin-only JSONL bulk/per-user export (org/enterprise
  settings page, rate-limited, no CLI/API endpoint), which is irrelevant for
  an unprivileged per-repo hook running as a regular user. There is no
  feasible unprivileged, local, per-repo sync path at all.
- Decided scope: only the 4 scripts under `scripts/°base/ai/hooks/` (not
  `.claude/hooks/permission-check.py`, which is a separate, out-of-scope
  concern). Bash-only command hooks (no PowerShell — repo is Linux/macOS-first).

## Approach

Add Copilot as a third rendered target in the existing neutral
settings-sync pipeline (`scripts/°base/ai/settings/°settings_lib/`), mirroring
how Codex was added, **and** patch the hook scripts themselves to be
tool-agnostic where Copilot's runtime facts genuinely differ from Claude's.

### 1. Settings-sync engine changes (`scripts/°base/ai/settings/°settings_lib/`)

- `paths.py`: add `COPILOT_HOOKS = Path(".github/hooks/generated.json")` and
  `COPILOT_LOCAL_HOOKS = Path(".github/hooks/generated.local.json")`
  (mirrors the Codex `generated.rules`/`generated.local.rules` naming).
- `hooks.py`:
  - add `render_copilot_hooks(shared)` — same neutral `hooks` block as
    `render_claude`/`render_codex_hooks`, but emits Copilot's native command
    shape (`bash` instead of `command`, `timeoutSec` instead of `timeout`,
    drop `statusMessage`/`async`), wrapped as `{"version": 1, "hooks": {...}}`
    (no `permissions`/`plugins`/`mcp` — Copilot's hooks file is hooks-only).
  - extend `_replace_tool_arg`/`_neutralize_command` so the `'claude'` /
    `'codex'` positional arg passed to save-prompt/save-decision/save-plan
    also round-trips as `'copilot'`.
  - keep matcher strings unioned across tool name variants (e.g.
    `Write|Edit|ExitPlanMode|exit_plan_mode`) so the same shared matcher works
    whether or not Copilot substitutes the Claude tool name for a given event.
- `cli.py`: extend `_load_layer`/`_apply_or_check` (or add a parallel call)
  to also import from and write `COPILOT_HOOKS`/`COPILOT_LOCAL_HOOKS`,
  following the exact pattern already used for `CLAUDE_SETTINGS`/`CODEX_HOOKS`
  and their `.local` counterparts in `main()`.
- Gitignore: add `.github/hooks/generated.local.json` alongside the existing
  local-settings ignore rules.

### 2. Hook script changes (`scripts/°base/ai/hooks/`)

- **save-plan/hook.py**: accept Copilot's session plan path
  (`~/.copilot/session-state/<session_id>/plan.md`) in `_plan_from_write`/
  `_plan_from_edit` in addition to `~/.claude/plans/*.md`; accept
  `tool_name in {"ExitPlanMode", "exit_plan_mode"}` for the finalize branch;
  add a fallback that reads the session's `plan.md` directly (constructed
  from `payload["session_id"]`) when `exit_plan_mode`'s `tool_response` has no
  usable `plan`/`filePath` field.
- **save-decision/hook.py**: add `_parse_copilot(payload)` for the `ask_user`
  tool's simpler schema (single `question`, flat `choices: string[]`,
  `allow_freeform`), dispatched via `tool_name == "ask_user"` in
  `parse_payload`; extend `_render_block` glyph selection for Copilot.
- **save-prompt/hook.py**: add a `"copilot"` entry to `PREFIXES` (distinct
  glyph); no special prompt-stripping needed (Claude-worker-prefix and Codex
  forwarded-plan stripping stay tool-specific, Copilot prompts pass through
  unmodified).
- **record-memory/hook.py**: no change — stays Claude-only, confirmed safe
  no-op under Copilot given no documented Copilot memory file location.

### 3. Tests

- Extend `scripts/°base/tests/test_ai_settings_sync.py` with
  `render_copilot_hooks` coverage mirroring the existing Codex render tests
  (tool-arg rewriting, command-shape translation, round-trip merge from a
  hand-edited `.github/hooks/generated.json`).
- Add/extend hook-script unit tests for: Copilot plan-path recognition,
  `exit_plan_mode` tool-name handling, and the new `_parse_copilot` ask_user
  parser (check for existing hook test files under `scripts/°base/tests/`
  covering save-plan/save-decision and extend those rather than creating
  new ones, if found).

### 4. Verification

- Run `uv run --project scripts/°base python -m unittest` (or the targeted
  test modules) after each component change.
- Run the sync script (`ai/settings/sync.py --dry-run` then apply) in this
  repo and inspect the generated `.github/hooks/generated.json` for
  correctness by hand.

### 5. Auto-capture the todo list into saved plan snapshots

New feature (not just Copilot compatibility): `save-plan/hook.py` should
automatically extract the current todo-list state (from the
`TodoWrite`/`update_todo` tool — Claude and Copilot's native todo tool,
mapped 1:1 per the tool-name table above) and append/update a "Todos"
section inside the *same* saved plan snapshot file
(`ai/plans/NNN_slug.md`) it already manages — instead of a human manually
copying the SQL todo list into the plan text.

- Add a `PostToolUse` matcher entry for `TodoWrite|update_todo` (plus a
  Codex equivalent, if one exists — verify Codex's todo/plan-tracking tool
  name at implementation time; Codex may only have `update_plan`, which is
  a different concept and should not be conflated with a to-do checklist).
- In `save-plan/hook.py`, on that event, parse the tool's item list from
  `tool_input`/`tool_response` (schema differs per tool: Claude's
  `TodoWrite` takes `todos: [{content, status, activeForm}]`; Copilot's
  `update_todo` — verify exact schema against this session's own tool
  definition) into a normalized checklist (e.g. `- [ ] `/`- [x] ` markdown
  items, grouped or annotated by status: pending/in_progress/done/blocked).
- Locate the *current* session's saved plan snapshot via the existing
  session-state tracking (`_load_state()`/`_STATE_FILE`, keyed by
  `session_id`) rather than requiring a fresh Write/ExitPlanMode event, and
  rewrite just the "Todos" section (idempotent replace-if-present /
  append-if-absent), then commit only that file — mirroring the existing
  commit-per-update behavior already used for plan content edits.
- If no plan snapshot exists yet for the session (todo tool used before any
  plan was saved), skip silently (no plan to attach todos to).

## Notes / open items carried forward

- `.claude/hooks/permission-check.py` (Bash `PreToolUse` gate) is explicitly
  out of scope per user decision — could be a natural follow-up task.
- Copilot's `PostToolUse` Claude-tool-name substitution is unconfirmed by
  docs; matchers are made defensive (list both forms) rather than assuming
  either behavior.
- Copilot memory-file sync is left as a documented gap, not implemented,
  because Copilot Memory is a cloud/server-side feature (confirmed via
  official docs) with no local file representation to hardlink from — this
  is a hard architectural constraint, not a missing-info gap.
