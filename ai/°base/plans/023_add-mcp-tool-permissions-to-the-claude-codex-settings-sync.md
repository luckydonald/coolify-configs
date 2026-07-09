# Add MCP tool permissions to the Claude↔Codex settings sync

## Context

The repo's `ai/tool-settings/settings.json` is a neutral schema that
`scripts/°base/ai/settings/sync.py` renders into native Claude
(`.claude/settings.json`) and Codex (`.codex/rules/generated.rules`,
`.codex/config.toml`) formats, and parses back from hand-edits on either
side. Bash commands already have this full round-trip (`commands.py` +
`codex_rules.py`), but **MCP tool call permissions have no support at
all**: Claude represents them as a bare permission string
`mcp__<server>__<tool>` (no parens, so it doesn't match the generic
`Tool(content)` parser and silently falls back to an opaque `{"type":
"raw", "value": ...}`), and there is currently zero logic translating
that into anything Codex understands.

Codex's real equivalent (confirmed in
`ai/references/https/developers.openai.com/codex/mcp.md`) is per-tool
approval overrides on `[mcp_servers.<name>]` in `config.toml`:
`enabled_tools`/`disabled_tools` (visibility) and
`tools.<tool>.approval_mode = "auto" | "prompt" | "approve"` (skip vs.
require confirmation). The `"auto"` mode is what maps to Claude's "allow"
semantics (never prompt); the Codex equivalent of a hard "deny" is
removing the tool from availability via `disabled_tools` (approval_mode
has no "always block" value).

Goal: give `permissions.allow`/`permissions.deny` a first-class `"mcp"`
entry type, wire it through both renderers, and make it round-trip from
hand-edited native files, matching the existing bash-rule and MCP-server
patterns already in the codebase.

## Neutral schema addition

New permission entry type:
```json
{"type": "mcp", "server": "bugsink", "tool": "list_projects"}
```
Claude string form (existing convention, not our invention):
`"mcp__bugsink__list_projects"` — bare, no parens.

## Changes

### 1. `scripts/°base/ai/settings/°settings_lib/commands.py`

- Add `_MCP_TOOL_ENTRY = re.compile(r"^mcp__(?P<server>[^_]+)__(?P<tool>.+)$")` (server names are simple slugs with no `__`; tool names may contain underscores) and check it **before** the generic `_PERMISSION_ENTRY` regex in `_parse_claude_permission_entry`, returning `{"type": "mcp", "server": ..., "tool": ...}`.
- In `_render_claude_permission_entry`, special-case `entry_type == "mcp"` (alongside the existing `"raw"` special-case) to render back `f"mcp__{entry['server']}__{entry['tool']}"`.
- No changes needed to `_TYPE_FIELD`/`_TYPE_TO_CLAUDE_TOOL` — those stay generic-tool-only.

### 2. `scripts/°base/ai/settings/°settings_lib/mcp_servers.py`

- Add `_mcp_tool_permissions(shared) -> dict[str, dict[str, list[str]]]`: scan `shared["permissions"]["allow"]`/`["deny"]` for `type == "mcp"` entries, group by `server` into `{"allow": [tool, ...], "deny": [tool, ...]}`.
- Extend `_server_table_lines(name, server, mcp, git_root, tool_permissions=None)`:
  - after the existing `enabled = ...` line, if `tool_permissions.get(name, {}).get("deny")`, append `disabled_tools = [...]` (sorted, `json.dumps`'d).
  - for each allowed tool (sorted), append a blank line + `[{table_name}.tools.{json.dumps(tool)}]` + `approval_mode = "auto"`. These per-tool sub-tables must stay contiguous within this server's block (TOML requires each table's own keys before the next header), which `render_codex_mcp_block` already achieves by building each server's lines as one contiguous chunk.
- `render_codex_mcp_block`: compute `tool_permissions = _mcp_tool_permissions(shared)` once, pass into `_server_table_lines`.
- Add `parse_codex_mcp_tool_permissions(text: str) -> dict[str, list[dict]]` returning `{"allow": [...], "deny": [...]}` neutral entries, parsed via `tomllib.loads`: for each `mcp_servers.<name>`, read `disabled_tools` → deny entries, and `tools.<tool>.approval_mode == "auto"` → allow entries. Mirrors `parse_codex_mcp_toml`'s existing shape but returns permission entries instead of server defs.

### 3. `scripts/°base/ai/settings/°settings_lib/cli.py`

- Add `_merge_mcp_tool_permission_additions(shared, parsed)` (parallel to `_merge_codex_rules_additions`): dedupe by `(type, server, tool)` against existing `permissions.allow`/`deny` entries of type `"mcp"`, append genuinely new ones.
- In `_load_layer`, after the existing `codex_rules_path` merge step, if `codex_config_path` is available and readable, call `mcp_servers.parse_codex_mcp_tool_permissions(config_text)` (reuse the `config_text` already read for plugins/MCP servers a few lines up) and merge via the new function.

### 4. `ai/tool-settings/settings.json`

Add to `permissions.allow`:
```json
{"type": "mcp", "server": "bugsink", "tool": "list_projects"},
{"type": "mcp", "server": "bugsink", "tool": "list_issues"},
{"type": "mcp", "server": "bugsink", "tool": "get_issue"}
```
(bugsink stays `"enabled": false` at the server level — these are dormant until the server is turned on, same as any other allow-list entry for a disabled tool/server.)

Run the sync script afterward so `.claude/settings.json` gains the three `"mcp__bugsink__..."` allow strings and `.codex/config.toml` gains the corresponding `[mcp_servers."bugsink".tools."..."]` `approval_mode = "auto"` blocks.

### 5. Tests — `scripts/°base/tests/test_ai_settings_sync.py`

- `commands.py`: parse `"mcp__bugsink__list_projects"` → `{"type": "mcp", "server": "bugsink", "tool": "list_projects"}` and render back, round-trip.
- `mcp_servers.render_codex_mcp_block`: given a shared dict with one stdio server plus an allow-mcp and a deny-mcp entry for it, assert the rendered block contains `disabled_tools = [...]` and a `[mcp_servers."name".tools."tool"]` / `approval_mode = "auto"` sub-table.
- `mcp_servers.parse_codex_mcp_tool_permissions`: parse a hand-written TOML snippet with `disabled_tools` and a `tools.<tool>.approval_mode = "auto"` sub-table, assert correct allow/deny neutral entries.
- `cli._load_layer`: a hand-edited `.codex/config.toml` with an extra `approval_mode = "auto"` tool block not present in `shared` gets merged back in without duplicating on a second run (mirrors `test_load_layer_merges_hand_edited_codex_rules_and_plugins`).

## Verification

1. `uv run --project scripts/°base python -m unittest scripts.°base.tests.test_ai_settings_sync -v`
2. `./scripts/°base/ai/settings/sync.py` — confirm it writes `.claude/settings.json` (three new `mcp__bugsink__*` allow strings) and `.codex/config.toml` (new `approval_mode = "auto"` blocks under `[mcp_servers."bugsink"...]`), and that a second run reports "All files are already in sync."
3. Inspect the generated `.codex/config.toml` by eye to confirm it's valid TOML (e.g. `python3 -c "import tomllib; tomllib.load(open('.codex/config.toml','rb'))"`).
