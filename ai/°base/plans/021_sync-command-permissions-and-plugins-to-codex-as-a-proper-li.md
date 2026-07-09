# Sync command permissions and plugins to Codex, as a proper library

## Context

`scripts/°base/ai/settings/sync.py` currently syncs `hooks` between Claude and Codex, but two
things never reach Codex:

- `permissions.allow`/`permissions.deny` (sync.py:405-417) — Claude-only, because entries are
  opaque strings like `"Bash(git status:*)"` in Claude's own glob syntax.
- `enabledPlugins` (sync.py:410-412) — only ever written into `.claude/settings.json`.

Research into two Codex doc pages during planning:

- `ai/references/https/developers.openai.com/codex/rules.md` (fetched, not yet saved to the
  repo) — Codex's command-allowlist equivalent is project-local `.codex/rules/*.rules` files
  containing Starlark `prefix_rule(pattern=[...], decision="allow"|"prompt"|"forbidden")` calls,
  scanned for any trusted `.codex/` config layer — the same trust model `.codex/hooks.json`
  already relies on.
- `ai/references/https/developers.openai.com/codex/plugins.md` (fetched, not yet saved) — Codex
  plugin enable/disable is `[plugins."<id>"]` `enabled = true|false` in a `config.toml`, keyed
  `name@marketplace` — nearly identical in shape to Claude's `enabledPlugins` map. `plugins` is
  not in the list of keys `config-advanced.md` blocks from project-local `.codex/config.toml`, so
  this can be project-scoped like everything else here, rather than reaching into the user's
  global `~/.codex/config.toml` (which the script currently only touches for one narrow,
  pre-existing feature-flag migration, `_migrate_codex_feature_flag`/`CODEX_CONFIG` — left
  untouched by this plan). A new project-local `.codex/config.toml` becomes a second Codex file
  this script owns and regenerates, parallel to `.codex/hooks.json`.

Both of these need real two-way sync (parse native Codex files back into the shared model, not
just generate them), and the user asked to use this as the moment to extract `sync.py`'s logic
into proper submodules instead of one 690-line file. There's already an in-repo precedent for
exactly this: `scripts/°base/ai/references/download-link.py` is a thin shim over
`ai/references/°dllink_lib/` (see `°dllink_lib/__init__.py`, `cli.py`, `models.py`, etc.), tested
in `tests/test_download_link.py` via `importlib.import_module("°dllink_lib.xxx")`. This plan
mirrors that pattern for the settings sync.

## 1. Extract `sync.py` into `°settings_lib/`

New package `scripts/°base/ai/settings/°settings_lib/`, split by concern (function names below
are today's `sync.py` names, moved as-is unless noted):

- `paths.py` — all path constants (`TRACKED_SHARED`, `LOCAL_SHARED`, `CLAUDE_SETTINGS`,
  `CLAUDE_LOCAL`, `CODEX_HOOKS`, `CODEX_LOCAL_HOOKS`, `CODEX_CONFIG`, plus new `CODEX_RULES`,
  `CODEX_RULES_LOCAL`, `CODEX_PROJECT_CONFIG` — see below — and the skill/command paths),
  `GENERATED_MARKER`, `_git_root()`.
- `json_io.py` — `_read_json`, `_write_json`, `_same_json`, `_unique`, `_write_text_if_changed`.
- `commands.py` — **new module**: `_parse_claude_permission_entry`, `_render_claude_permission_entry`,
  the type↔field/tool-name maps, `_bash_pattern_to_prefix`.
- `hooks.py` — `_hook_id`, `_matcher_tokens`, `_entry_commands`, `_subsumes`, `_overlay_entry`,
  `_shared_extras`, `_merge`, `_normalize_native` (updated to call into `commands.py` for
  permission entries), `_replace_tool_arg`, `_normalize_command_path`, `_uv_project_hook_command`,
  `_neutralize_uv_project_hook_command`, `_neutralize_command`, `_render_hooks`, `render_claude`,
  `render_codex_hooks`.
- `codex_rules.py` — **new module**: `render_codex_rules(shared) -> str` (generation) and
  `parse_codex_rules(text) -> dict` (parsing, see §3).
- `codex_toml.py` — existing `_features_bounds`, `_rewrite_codex_feature_flag`,
  `_ask_codex_config_migration`, `_migrate_codex_feature_flag` (untouched, still targets the
  global `CODEX_CONFIG`), plus new `parse_codex_plugins(text) -> dict[str, bool]` and
  `render_codex_plugins(text, enabled_plugins) -> str` (see §4).
- `skills.py` — all `_skill_*`/`_render_*skill*`/`_sync_skills`/frontmatter helpers, unchanged.
- `cli.py` — `_load_layer` (extended, §5), `_apply_or_check` (extended, §5), `main()`, argparse.
- `__init__.py` — re-exports the public surface (mirrors `°dllink_lib/__init__.py`): at minimum
  `render_claude`, `render_codex_hooks`, `render_codex_rules`, `parse_codex_rules`,
  `render_codex_plugins`, `parse_codex_plugins`, `_merge`, `_normalize_native`,
  `_parse_claude_permission_entry`, `_render_claude_permission_entry`, `_bash_pattern_to_prefix`,
  `main`. This keeps most existing `MODULE.foo(...)` test assertions working unchanged.

`sync.py` shrinks to the same shim shape as `download-link.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations
import importlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
main = importlib.import_module("°settings_lib.cli").main

if __name__ == "__main__":
    raise SystemExit(main())
```

This keeps every existing invocation (`python3 scripts/°base/ai/settings/sync.py`,
`./scripts/°base/ai/settings/sync.py`, the pre-commit hook, the SessionStart hook) working
unchanged — none of the ~10 permission entries referencing this script's path need to change.

## 2. Neutral permission entry schema (unchanged from prior draft)

```json
{ "type": "bash", "command": "git status:*" }
{ "type": "read", "path": "ai/skills/commit-with-lplp-style/SKILL.md" }
{ "type": "skill", "name": "commit-with-lplp-style" }
```

- `type` = lowercased Claude tool name; value field is type-specific (`command` for `bash`,
  `path` for `read`/`write`/`edit`/`glob`, `name` for `skill`, generic `pattern` fallback for
  unknown tools).
- Unparseable legacy strings become `{ "type": "raw", "value": "<original>" }` (lossless).
- `commands.py`: `_parse_claude_permission_entry`/`_render_claude_permission_entry` do the
  round-trip; wired into `hooks._normalize_native` so any string entries (old-format shared file,
  or native `.claude/settings.json`) upgrade to objects before merge/dedup. `render_claude` maps
  objects back to strings, so Claude's own file is unaffected in format.
- Net effect: next `sync.py` run rewrites the tracked `ai/tool-settings/settings.json`
  permissions block from ~90 strings to objects (expected one-time diff).

## 3. Codex `.rules` — generate *and* parse

`codex_rules.py`:

- `_bash_pattern_to_prefix(command) -> list[str] | None` (as before): strip a trailing `:*` or
  bare trailing `*` token, `shlex.split` the remainder as the prefix; return `None`
  (untranslatable, Claude-only) for commands containing `$ \` && || ; | < >` or matching an
  env-assignment prefix (`^[A-Za-z_]\w*=`).
- `render_codex_rules(shared) -> str`: for `type == "bash"` entries, `permissions.allow` →
  `decision="allow"`, `permissions.deny` → `decision="forbidden"`; emit one
  `prefix_rule(pattern=[...], decision="...")` Starlark call per entry (string literals via
  `json.dumps`, which is valid Starlark string syntax); leading `GENERATED_MARKER` comment;
  trailing comment counting skipped/untranslatable entries so nothing silently vanishes.
- **New** `parse_codex_rules(text) -> dict`: parse with `ast.parse(text)` — Starlark's
  `prefix_rule(...)` call syntax is valid Python syntax for the subset we ever emit or expect
  (literals, lists, kwargs, comments), so the stdlib `ast` module is a safe, dependency-free
  parser for *reading*. Walk for `ast.Call` nodes named `prefix_rule`, pull `pattern`/`decision`
  via `ast.literal_eval` on the keyword values. For each call:
  - `pattern` must be a flat list of plain strings (no nested union sub-lists) to round-trip —
    reconstruct the Claude-style command as `" ".join(pattern) + ":*"` (a prefix_rule always
    means "this prefix, any suffix", matching the wildcard convention already used for generated
    entries).
  - `decision == "allow"` → goes into the returned `allow` bucket, `"forbidden"` → `deny` bucket,
    as `{"type": "bash", "command": "<reconstructed>"}`.
  - `decision == "prompt"` or a non-flat pattern → skip (no Claude equivalent / can't reduce to a
    single command string); these stay Codex-only additions that don't feed back into the shared
    model. Malformed/unparseable file contents → treat as no entries found (don't crash sync).
- Scope: only `.codex/rules/generated.rules` (our own generated file) is read back — not other
  hand-authored `.rules` files a developer might add next to it, and not the user-global
  `~/.codex/rules/default.rules` that Codex's own "add to allowlist"/Smart Approvals flow writes
  to. This mirrors how `_load_layer` today only reads back the specific native files it also
  writes.

## 4. Codex `enabledPlugins` — sync to project-local `.codex/config.toml`

New path constant `CODEX_PROJECT_CONFIG = Path(".codex/config.toml")` (repo-relative, tracked —
distinct from the existing `CODEX_CONFIG` global-home migration target, which is left alone).

`codex_toml.py`:

- `parse_codex_plugins(text) -> dict[str, bool]`: `tomllib.loads(text)` (stdlib, read-only,
  available since this repo already requires Python ≥3.11 per `pyproject.toml`), pull
  `{"<id>": entry.get("enabled", True) for id, entry in data.get("plugins", {}).items()}`.
- `render_codex_plugins(text, enabled_plugins) -> str`: surgical text edit, same style as the
  existing `_rewrite_codex_feature_flag`/`_features_bounds` table-bounds scanning — for each
  `id -> bool` in `enabled_plugins`, find an existing `[plugins."<id>"]` table and update/insert
  its `enabled = ...` line, or append a new `[plugins."<id>"]\nenabled = ...\n` block if the
  table doesn't exist yet. Leaves every other line/table/comment in the file untouched. If the
  file doesn't exist yet, start from a `GENERATED_MARKER`-commented empty string.
- This makes `enabledPlugins` genuinely bidirectional: a plugin toggled on/off directly in
  `.codex/config.toml` (e.g. via Codex's own `/plugins` UI, if it ever writes project-local
  config) is picked up on the next sync and reflected into `.claude/settings.json` too.

## 5. Wiring into `cli.py`

- `_load_layer` grows two more optional native sources beyond the current
  (shared JSON, Claude JSON, Codex hooks JSON): read `.codex/rules/generated.rules`
  → `codex_rules.parse_codex_rules` → merge into `permissions.allow`/`deny`; read
  `.codex/config.toml` (project-local) → `codex_toml.parse_codex_plugins` → merge into
  `enabledPlugins` (dict update, most-recently-modified file wins per existing mtime-based
  merge order used for the other native sources).
- `_apply_or_check` (for both the tracked and local settings groups) additionally computes and
  writes, via `_write_text_if_changed`:
  - `render_codex_rules(shared)` → `CODEX_RULES` / `CODEX_RULES_LOCAL`
    (`.codex/rules/generated.rules`, `.codex/rules/generated.local.rules` — the `.local.` name
    matches the existing `**/*.local.*` gitignore rule, confirmed already present).
  - `render_codex_plugins(<current file text>, shared["enabledPlugins"])` → `CODEX_PROJECT_CONFIG`
    (tracked; no local variant needed since plugin enablement isn't expected to differ per
    machine — local overrides can still land in `.codex/config.local.toml` later if ever needed,
    out of scope now).
- `main()` gains the two new path constants in its call sites; no CLI flag changes.

## 6. Docs

Update `ai/°base/AGENTS.md`'s settings table (~line 40-47): add `.codex/rules/generated.rules`
and `.codex/config.toml` as generated/do-not-edit-directly files, and note the structured
permission-entry schema and the `°settings_lib` extraction (mirroring how the table already
distinguishes tracked source vs. generated output).

## Tests

Follow `tests/test_download_link.py`'s import style:

```python
sys.path.insert(0, str(LIB_ROOT))  # scripts/°base/ai/settings
commands = importlib.import_module("°settings_lib.commands")
hooks = importlib.import_module("°settings_lib.hooks")
codex_rules = importlib.import_module("°settings_lib.codex_rules")
codex_toml = importlib.import_module("°settings_lib.codex_toml")
cli = importlib.import_module("°settings_lib.cli")
```

Existing tests in `test_ai_settings_sync.py` keep working via `°settings_lib`'s `__init__.py`
re-exports (`MODULE = importlib.import_module("°settings_lib")`, same attribute access as today)
where the function still exists at that name; a few will need to move to the submodule-qualified
form where that's clearer (e.g. permission parsing tests naturally read as `commands.foo(...)`).

New coverage:
- `commands`: parse/render round-trip for `bash`/`read`/`skill`/unknown-tool/malformed-raw;
  `_bash_pattern_to_prefix` wildcard-stripping and the untranslatable cases.
- `codex_rules`: `render_codex_rules` allow→`"allow"`/deny→`"forbidden"`/non-bash-skipped/
  untranslatable-skipped; `parse_codex_rules` round-trips what `render_codex_rules` emits, skips
  `prompt` decisions and non-flat patterns, and doesn't crash on garbage input.
- `codex_toml`: `parse_codex_plugins`/`render_codex_plugins` round-trip, and — importantly — a
  test that `render_codex_plugins` preserves unrelated existing content (comments, other tables)
  in a realistic `config.toml` fixture when adding/updating a `[plugins."id"]` block.
- `cli._load_layer`: a fixture where a hand-edited `.codex/rules/generated.rules` and
  `.codex/config.toml` each contain an entry not present in the shared JSON, asserting both merge
  into the shared model and then propagate into `.claude/settings.json`.
- `cli._apply_or_check`/`main`: new files are written/reported in `changed`; a second run reports
  already-in-sync (idempotency).

## Verification

1. `uv run --project scripts/°base python -m unittest discover scripts/°base/tests -v` (full
   suite, since this touches a widely-imported module).
2. Run `./scripts/°base/ai/settings/sync.py` for real in the repo and inspect the diff:
   - `ai/tool-settings/settings.json` permissions are now objects, same entries as before.
   - `.codex/rules/generated.rules` created with translatable bash commands; skipped-entry count
     matches expectations (e.g. the `GIT_SEQUENCE_EDITOR=...` and `echo "exit: $?"` entries).
   - `.codex/config.toml` created/updated with a `[plugins."openai-developers@openai-developers"]`
     `enabled = true` block, nothing else touched.
   - `.claude/settings.json` unchanged in meaning (same strings, same `enabledPlugins`).
3. `./scripts/°base/ai/settings/sync.py --check` again — reports already in sync.
4. Hand-edit `.codex/rules/generated.rules` to add one `prefix_rule(...)`, rerun sync, confirm it
   appears as a new `Bash(...)` entry in `.claude/settings.json` and as an object in
   `ai/tool-settings/settings.json` (proves the parse-back path works end to end).
