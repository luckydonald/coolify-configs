# Settings-sync improvements: version bump, formatting, plugin reshape, MCP tool-merge fix

## Context

`scripts/°base/ai/settings/sync.py` (thin shim over `°settings_lib/`) is the single sync engine
that keeps `ai/tool-settings/settings.json` (the neutral source of truth) in sync with
`.claude/settings.json`, `.codex/hooks.json`, `.codex/rules/generated.rules`,
`.codex/config.toml`, and `.mcp.json`. Commit `95f48bc` introduced structured permission-entry
dicts (replacing raw Claude strings) but forgot to bump the file's `"version"` field, which is
still hardcoded to `1` everywhere. Separately, real usage has surfaced a genuine bug: the
repo's own `ai/tool-settings/settings.json` currently has an **uncommitted local drift**
(`git diff` shows `mcp.servers.bugsink` losing its authored `"tools": [".env"]` reference and
its `"enabled": false` flag flipping to `true`) caused by the native-file round-trip merge logic
in `cli.py`/`mcp_servers.py`. This plan bundles that fix with six smaller, mostly independent
formatting/schema requests the user listed while in the area. Diagnosis for every change below
was confirmed against the actual code, the real drifted file, and existing tests (not assumed).

Two bugs combine to cause the observed bugsink drift, both must be fixed:
1. `parse_claude_mcp` hardcodes `"enabled": True` on every server it parses back from
   `.mcp.json` — but `.mcp.json` structurally has no enabled/disabled concept
   (`render_claude_mcp` renders every server into it regardless of state). This fabricated
   `True` then wins over Codex's real `enabled: false` in `_load_layer`'s
   `combined_mcp_native.update(data)` (a blind per-server overwrite, and `.mcp.json` is written
   after `.codex/config.toml` in the same run, so it reliably has the later mtime and sorts last).
2. `_merge_mcp_native_additions` (cli.py) replaces the *entire* existing server entry with the
   raw flat native-parsed one (`servers[name] = entry`) whenever its resolved `(argv, enabled)`
   signature differs at all from the existing one — even when only `enabled` changed — discarding
   the authored `tools: [...]` abstraction and replacing it with a hardcoded flat `cmd`.

## Approach

### 1. Version bump (hooks.py, cli.py)
Add `CURRENT_VERSION = 2` to `hooks.py`. `_normalize_native` (currently hardcodes
`"version": 1`) emits `CURRENT_VERSION` instead. cli.py's fallback default
(`{"version": 1, ...}`) also uses it. No migration logic is needed beyond this: the existing
permissive parser (`_parse_claude_permission_entry`) already upgrades raw Claude permission
strings to structured dicts regardless of the source `version`, so v1 files (the plain,
pre-95f48bc Claude schema — raw permission strings, flat `enabledPlugins: {id: bool}`, no `mcp`
key) already load correctly content-wise; only the version *stamp* was wrong. Add a test proving
a v1-shaped fixture (modeled on the real pre-95f48bc file, embedded statically — do not `git
show` inside a test, since this repo is checked out into other repos without guaranteed history)
normalizes to `version: 2` with upgraded permission entries.

### 2. Custom JSON pretty-printer (json_io.py) — covers reqs 2, 3, 6
Replace `_write_json`'s `json.dumps(data, indent=2)` with a hand-written recursive renderer
that keeps normal one-key/one-element-per-line indent=2 formatting everywhere, except:
- **Req 2** (`enabled` first): before rendering any dict's keys, reorder so `"enabled"` (if
  present) comes first — applied at every depth, generically, so no call site needs to control
  insertion order.
- **Req 3** (permission entries single-line): when rendering a list at path
  `permissions.allow`/`permissions.deny`, keep one array element per line, but render each
  element (a dict) compact (single-line JSON) instead of spreading it across multiple lines.
- **Req 6** (`cmd` arrays single-line): when rendering a list whose key is literally `"cmd"`,
  emit the whole array on one line. `"cmd"` only ever appears at `mcp.tools.*.*.cmd` and
  `mcp.servers.*.cmd` in this schema, so keying off the field name is safe and avoids brittle
  path-pattern matching.

`_read_json`/`_same_json` stay untouched (structural equality, not textual) — this is a pure
serialization change.

### 3. `enabledMcpjsonServers`/`disabledMcpjsonServers` always both present (hooks.py, req 4)
In `render_claude`, when `mcp.servers` is non-empty, always set both
`enabledMcpjsonServers` and `disabledMcpjsonServers` (using `[]` for whichever bucket is empty)
instead of omitting a key when its bucket happens to be empty. When `mcp.servers` is empty/absent,
still omit both keys (existing test `test_render_claude_omits_mcp_server_lists_when_no_servers`
keeps passing unchanged). This is the only literal `enabled…/disabled…` array pair in the
codebase — Claude's native plugin format is a single `{id: bool}` dict, not two arrays, so
requirement 7 below doesn't need a second such pair.

### 4. `enabledPlugins` → `plugins` (hooks.py, cli.py) — req 7
Rename the **neutral** shared-file key only; Claude's *native* `.claude/settings.json` keeps
using its real `enabledPlugins: {id: bool}` key (`render_claude` already builds it separately —
just source it from the new shape).
- `_CORE_SHARED_KEYS`: `"enabledPlugins"` → `"plugins"`.
- `_normalize_native`: build `plugins: {id: {"enabled": bool}}` — read `data["plugins"]` if
  already dict-shaped (normalizing each entry's `.enabled`, default `True`), else fall back to
  legacy/native flat `data["enabledPlugins"]` (`dict[str, bool]`) and wrap each value.
- `_merge`: merge `plugins` per-id (same overwrite semantics the old `enabled_plugins.update()`
  had).
- `render_claude`: flatten `shared["plugins"]` back into `enabledPlugins: {id: bool}` for the
  Claude-native output.
- cli.py `_apply_or_check`'s Codex-config block: flatten `shared.get("plugins")` to
  `dict[str, bool]` before calling `codex_toml.render_codex_plugins` (that function's own
  signature stays flat — it's TOML-only, no need for the nested shape there).
- cli.py `_load_layer`'s existing `{"enabledPlugins": plugins}` native-source construction
  (from `codex_toml.parse_codex_plugins`) needs **no change** — `_normalize_native` now handles
  the flat→nested conversion for it automatically.

### 5. MCP tool/cmd merge fix (mcp_servers.py, cli.py, mcp.schema.json) — req 5, the core fix

**mcp_servers.py:**
- `parse_claude_mcp`: stop hardcoding `"enabled": True` — omit `enabled` entirely from returned
  entries (`.mcp.json` carries no real enabled information).
- `_resolve_server_argv`: allow missing/empty `cmd` as long as `tools` alone contributes a
  non-empty argv (only reject when the *combined* result is empty). Needed so a server that's
  entirely one tool invocation doesn't require a redundant empty/dummy `cmd`.
- New `extract_tools_from_cmd(tools, cmd, git_root) -> tuple[list[str], list[str]]`: greedily
  matches, from the front of `cmd`, against every `(name, variant)` in `mcp.tools` with
  `mode == "prefix"` (git-root-substituted via the existing `_substitute_git_root`), preferring
  the **longest matching prefix**, then alphabetical `(name, variant)` for determinism; on a
  match, records a ref (`name`, or `name@variant` when variant isn't the empty-string default),
  advances past the matched tokens, and repeats (supports chained tools, matching
  `_resolve_server_argv`'s left-to-right composition). Terminates because every tool cmd is
  non-empty. Returns `(tool_refs, remaining_cmd)`; when nothing matches, degenerates to
  `([], cmd)` — the existing "just store flat cmd" fallback.
- New `_reconstruct_stdio_entry(mcp, cmd, enabled, git_root) -> dict`: wraps
  `extract_tools_from_cmd` into a full server entry (`type: stdio`, `tools` if any were found,
  `cmd` for the remainder, `enabled`).
- `_server_table_lines` (TOML, req 2): move the `enabled = ...` line to immediately after the
  `[mcp_servers."name"]` header, before the type-specific `url`/`command`/`args` lines.

**cli.py:**
- `_load_layer`'s `combined_mcp_native` construction: change from a blind
  `combined_mcp_native.update(data)` per source to a **per-field merge per server name**, so an
  earlier source's field (e.g. Codex's real `enabled`) can't be clobbered by a later source that
  simply doesn't carry that field (e.g. Claude's `.mcp.json`, post the `parse_claude_mcp` fix
  above).
- `_merge_mcp_native_additions`: rewrite so that
  - a genuinely new server name gets `_reconstruct_stdio_entry`-ed (stdio) instead of stored flat
    raw, so a hand-added native server that happens to match a known tool prefix still gets the
    `tools` abstraction;
  - `new_enabled` falls back to `existing_enabled` (not a hardcoded `True`) when the native entry
    doesn't carry one;
  - if the resolved signature (argv for stdio, url/headers for http) is unchanged but `enabled`
    differs, only `enabled` is updated on the *existing* entry — the authored `tools`/`cmd` shape
    is preserved untouched;
  - if the resolved signature genuinely changed, stdio entries are rebuilt via
    `_reconstruct_stdio_entry` (re-extracting `tools` from the new flat cmd) rather than stored
    as a raw flat `cmd`; http entries replace wholesale as before (no tools concept there).

**ai/tool-settings/mcp.schema.json:** relax the stdio `allOf`/`then` clause from unconditionally
`"required": ["cmd"]` to `anyOf` non-empty `cmd` or non-empty `tools` (add local
`"minItems": 1"` to the `tools` branch, since the base `tools` property has no such constraint).

## Critical files
- `scripts/°base/ai/settings/°settings_lib/hooks.py` — version, plugins reshape, enabled-list req 4
- `scripts/°base/ai/settings/°settings_lib/json_io.py` — custom pretty-printer (reqs 2, 3, 6)
- `scripts/°base/ai/settings/°settings_lib/mcp_servers.py` — tool extraction, enabled-parsing fix, TOML ordering
- `scripts/°base/ai/settings/°settings_lib/cli.py` — version default, native-source merge, `_merge_mcp_native_additions` rewrite, plugins flatten for Codex TOML
- `ai/tool-settings/mcp.schema.json` — stdio cmd/tools `anyOf` relaxation
- `scripts/°base/tests/test_ai_settings_sync.py` — update all `enabledPlugins` fixtures to `plugins`; update `test_parse_claude_mcp_round_trip`'s `enabled` expectation; add new tests (v1 upgrade, req-4 both-buckets, tool-extraction unit tests, the two-bug bugsink regression scenario, JSON-formatting tests, plugins round-trip)

## Verification
1. `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v` — full suite green, including new tests for: v1-file upgrade to version 2; `enabledMcpjsonServers`/`disabledMcpjsonServers` both present with an empty-bucket case; `extract_tools_from_cmd` (single match, chained match, no-match fallback, longest-match tie-break); the real bugsink-shaped regression (authored `tools`+`enabled:false` entry survives a native round-trip through both `.mcp.json` and `.codex/config.toml` unchanged); a case where native cmd content genuinely changes and `tools` gets correctly re-extracted; a case with no tool-prefix match falling back to flat `cmd`; JSON output text assertions for compact permission entries / single-line `cmd` arrays / enabled-first ordering; plugins flat↔nested round-trip both directions.
2. Run the real sync: `python3 scripts/°base/ai/settings/sync.py`, then inspect
   `git diff ai/tool-settings/settings.json .claude/settings.json .codex/config.toml .mcp.json`.
   Expect: `mcp.servers.bugsink` returns to its authored
   `{"enabled": false, "type": "stdio", "tools": [".env"], "cmd": ["npx", "-y", "bugsink-mcp"]}`
   shape (fixing the current uncommitted drift), `"version"` becomes `2`, `enabledPlugins`
   becomes `plugins` (nested `{"enabled": ...}` per id), permission entries and `cmd` arrays
   render single-line, `enabled` sorts first, and `.claude/settings.json`'s
   `enabledMcpjsonServers`/`disabledMcpjsonServers` mismatch (noted in `git status` at the start
   of this session) resolves itself as a side effect of the `enabled`-handling fix.
3. Re-run `python3 scripts/°base/ai/settings/sync.py --check` — must report "already in sync"
   (idempotency).
