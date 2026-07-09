# Remove the redundant Claude command-shim for skills

## Context

`scripts/°base/ai/settings/sync.py` (via `°settings_lib/skills.py`) generates, for
every skill under `ai/skills/<slug>/SKILL.md`, both:

1. `.claude/skills/<slug>/SKILL.md` — a symlink to the canonical source (the real skill).
2. `.claude/commands/<slug>.md` — a generated slash-command shim carrying the *same*
   `name`/`description` frontmatter, whose body just says "Use the `<slug>` skill for
   this request."

This causes every skill (e.g. `commit-with-lplp-style`) to show up twice in Claude
Code's skill index — once as the real skill, once as the command shim. We confirmed via
web search that Claude Code's Terminal already lists skills directly in the native `/`
autocomplete (discovered from `.claude/skills/`), so the shim is solving a problem that
doesn't exist for Claude — it was presumably built before this was verified.

This is exactly the same situation the codebase already resolved on the Codex side:
`ai/°base/plans/005_migrate-ai-commands-to-shared-skills.md` states they *removed*
`.codex/commands/*.md` duplicates and stopped generating Codex command copies "because
Codex can use `.agents/skills` directly." We're doing the equivalent cleanup for Claude.

**Note:** one edit was already applied in `°settings_lib/skills.py` before plan mode
engaged (renaming `_iter_skill_source_paths`/`_collect_skill_sources` to drop
`claude_command`/`codex_command` scanning, i.e. only `claude_skill` sources remain).
That partial edit is consistent with this plan and should be kept as-is; the remaining
steps below complete it.

## Changes

### 1. `scripts/°base/ai/settings/°settings_lib/skills.py`
- Remove `_render_claude_command_shim` entirely.
- In `_sync_skills`, remove all `claude_command_paths` construction and the loop that
  writes the command shim (the `command_shim = ...` line and the `for path in
  sorted(claude_command_paths): ...` write loop). Keep the `claude_skill_paths`
  symlink-writing logic unchanged (already renamed from `claude_paths` in the prior
  edit — verify the rest of `_sync_skills` uses `claude_skill_paths` consistently, since
  the earlier partial edit renamed the dict returned by `_collect_skill_sources` but
  `_sync_skills` still unpacks it as `claude_paths` and references `claude_command_paths`
  — needs updating to match).

### 2. `scripts/°base/ai/settings/°settings_lib/paths.py`
- Remove the now-unused `CLAUDE_COMMANDS` and `CODEX_COMMANDS` constants (confirmed
  unused anywhere else in the repo outside `skills.py` and its tests).

### 3. `ai/°base/AGENTS.md` (~line 92)
- Update the skills-sync description to drop the sentence about `.claude/commands/<slug>.md`
  getting a generated shim, since it no longer does. End the paragraph after describing
  the `.claude/skills` / `.agents/skills` symlinks.

### 4. `scripts/°base/tests/test_ai_settings_sync.py` (`SkillsTests`, ~line 1236-1330)
- `patched_skill_paths`: drop `CLAUDE_COMMANDS`/`CODEX_COMMANDS` from the patched-names
  list and the assignments.
- Remove `test_sync_skills_imports_claude_command_and_renders_wrappers` (tests
  hand-authored `.claude/commands/*.md` being imported as a skill source and rewritten
  into a shim — no longer applicable, that import path is gone).
- Remove/rewrite `test_sync_skills_imports_new_claude_skill_over_generated_wrapper`:
  it uses `skills._render_claude_command_shim` (being deleted) to seed a stale generated
  command file and asserts a newer `.claude/skills/.../SKILL.md` wins. Since command
  shims no longer exist, drop the generated-command part of this test; if it still adds
  coverage for "a newer hand-authored `.claude/skills/<slug>/SKILL.md` overrides an
  older canonical source," keep that narrower assertion and drop the shim-related setup
  and assertions.

### 5. Delete stale generated files already on disk
- `.claude/commands/commit-with-lplp-style.md`
- `.claude/commands/bugsink-triage.md`
(Both currently carry the `GENERATED_MARKER`, confirmed via earlier `cat`.)

### 6. Regenerate and verify
- Run `python3 scripts/°base/ai/settings/sync.py` (project-allowed command) — should
  report no need to recreate the deleted `.claude/commands/*.md` files, and should
  leave `.claude/skills/*` symlinks untouched.
- Run the relevant unit tests: `python -m unittest scripts/°base/tests/test_ai_settings_sync.py -v`
  (or the narrower `SkillsTests` class) to confirm the updated/removed tests pass.

## Verification
1. `python3 scripts/°base/ai/settings/sync.py --check` exits 0 (nothing out of sync).
2. `python -m unittest scripts/°base/tests/test_ai_settings_sync.py -v` passes.
3. `git status` shows: modified `skills.py`, `paths.py`, `AGENTS.md`, `test_ai_settings_sync.py`;
   deleted `.claude/commands/commit-with-lplp-style.md` and `.claude/commands/bugsink-triage.md`.
4. Manually confirm `.claude/skills/commit-with-lplp-style/SKILL.md` and
   `.claude/skills/bugsink-triage/SKILL.md` symlinks are untouched/still valid.
