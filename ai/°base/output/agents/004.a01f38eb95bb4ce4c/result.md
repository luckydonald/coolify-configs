# Validated & Refined Implementation Plan

I read every file in `°settings_lib/`, the schema, the real `ai/tool-settings/settings.json` (plus its current uncommitted drift), the pre-migration v1 file at `95f48bc69b93f990ce7986344ca05722192c4ff1~1`, and the full test file. Your research and design are accurate on requirements 1–4, 6, 7. On requirement 5 I found a **second, deeper root cause** beyond what you diagnosed — both must be fixed together or the bugsink drift will keep recurring even after the tools-extraction logic lands. Details below, then the file-by-file plan.

## Key validation finding: req 5's real root cause is two bugs, not one

Your diagnosis (wholesale-replace-on-any-difference in `_merge_mcp_native_additions`) is correct but incomplete. Tracing the actual mechanics:

1. `parse_claude_mcp` (mcp_servers.py:79,91) **hardcodes `"enabled": True`** on every entry it parses back from `.mcp.json` — but `.mcp.json` structurally has no enabled/disabled concept (Claude renders *all* servers into it regardless of `enabled`, per `render_claude_mcp`, which doesn't filter). So every reparse of `.mcp.json` invents a fake `enabled: True`, regardless of what was actually authored.
2. In `_load_layer`, `combined_mcp_native.update(data)` combines the Codex-parsed and Claude-parsed native sources with a **blind per-server dict overwrite** (not a per-field merge), ordered by file mtime. Since `.codex/config.toml` and `.mcp.json` are written sequentially in the same `_apply_or_check` call (config before mcp.json), the mcp.json's mtime is reliably ≥ the config's mtime on every real run. That means Claude's fabricated `enabled: True` **deterministically wins** over Codex's real `enabled: false` in `combined_mcp_native` — before `_merge_mcp_native_additions` even runs its signature comparison.

This exactly explains the observed bugsink drift (`enabled: false → true`) independent of the tools-flattening bug, and it will keep firing every run even after you fix the wholesale-replace logic, unless fixed too. Fix:

- `parse_claude_mcp`: **omit `enabled` entirely** from returned entries (both stdio and http branches) instead of hardcoding `True` — the file genuinely carries no such information.
- `_load_layer`'s `combined_mcp_native` construction: change from `combined_mcp_native.update(data)` (whole-server overwrite) to a **per-field merge** per server name, so a source's *absence* of a field can't clobber a value contributed by an earlier source.
- `_merge_mcp_native_additions`: change `new_enabled = entry.get("enabled", True)` to `entry.get("enabled", existing_enabled)` — fall back to the *existing* value, not a hardcoded default, so a Claude-only content edit (which never carries real enabled info) can't silently flip enabled state.

I re-traced the real bugsink scenario against this combined fix (source-merge fix + fallback fix) and confirmed it fully resolves the observed drift even before the tools-extraction algorithm engages — the extraction algorithm is then needed for the *separate*, still-real problem of genuine content changes (hand-edits) losing the `tools` abstraction. Both are required for req 5 to be complete.

## Per-file plan

### `scripts/°base/ai/settings/°settings_lib/hooks.py`

1. Add `CURRENT_VERSION = 2` near the top (module-level constant, exported for cli.py to import).
2. `_normalize_native` line 85: change `"version": 1` → `"version": CURRENT_VERSION`. No other change needed — permission-entry upgrading (`_parse_claude_permission_entry`) already ignores the source `version` field entirely, so v1 files already upgrade content-wise; this fix only corrects the version stamp itself.
3. `_CORE_SHARED_KEYS`: replace `"enabledPlugins"` with `"plugins"`.
4. Add `_normalize_plugins(data: dict) -> dict[str, dict[str, Any]]`: if `data["plugins"]` is a dict, normalize each entry to `{"enabled": bool(entry.get("enabled", True))}` (tolerate a stray flat bool defensively); else read legacy/native flat `data["enabledPlugins"]` (`dict[str, bool]`) and wrap each value as `{"enabled": bool}`. Called from `_normalize_native` in place of the current `deepcopy(data.get("enabledPlugins") or {})` line, output key renamed `"plugins"`.
5. `_merge`: replace the `enabled_plugins` block — `plugins = deepcopy(merged.get("plugins") or {})`, then for each `(plugin_id, entry)` in `incoming.get("plugins") or {}`: `plugins[plugin_id] = deepcopy(entry)` (per-id overwrite, same semantics as the old `dict.update`). Store as `merged["plugins"]`.
6. `render_claude`: replace the `enabled_plugins = shared.get("enabledPlugins")` block with reading `shared.get("plugins")` and flattening: `data["enabledPlugins"] = {pid: bool(e.get("enabled", True)) for pid, e in plugins.items() if isinstance(e, dict)}` when `plugins` is truthy. This is the only place Claude's native `enabledPlugins: {id: bool}` shape is (re)built — keep it exactly as-is structurally.
7. `render_claude`'s MCP list block (req 4): remove the two `if enabled:` / `if disabled:` guards — whenever `servers` (the outer `if servers:`) is truthy, always set both `data["enabledMcpjsonServers"]` and `data["disabledMcpjsonServers"]`, using `[]` for an empty bucket. The existing "no servers at all" test still passes because the outer `if servers:` guard is unchanged.

### `scripts/°base/ai/settings/°settings_lib/json_io.py`

Add a custom pretty-printer (replaces the current `json.dumps(data, indent=2)` call) that implements req 2, 3, 6 in one coherent pass:

```python
def _reorder_enabled_first(d: dict) -> list[tuple[str, Any]]:
    items = list(d.items())
    if "enabled" not in d:
        return items
    return [("enabled", d["enabled"])] + [(k, v) for k, v in items if k != "enabled"]

def _compact_json(value: Any) -> str:
    # single-line rendering, still applying enabled-first at any depth
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)}: {_compact_json(v)}" for k, v in _reorder_enabled_first(value)) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_compact_json(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)

def _dump_json(value: Any, indent: int, path: tuple[str, ...]) -> str:
    pad, pad_in = "  " * indent, "  " * (indent + 1)
    if isinstance(value, dict):
        if not value: return "{}"
        items = _reorder_enabled_first(value)
        parts = [f'{pad_in}{json.dumps(k)}: {_dump_json(v, indent + 1, path + (k,))}' for k, v in items]
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    if isinstance(value, list):
        if not value: return "[]"
        if path and path[-1] == "cmd" and all(isinstance(x, str) for x in value):
            return _compact_json(value)                                    # req 6
        if len(path) >= 2 and path[-2] == "permissions" and path[-1] in ("allow", "deny"):
            parts = [f"{pad_in}{_compact_json(item)}" for item in value]   # req 3
            return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
        parts = [f"{pad_in}{_dump_json(item, indent + 1, path)}" for item in value]
        return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
    return json.dumps(value, ensure_ascii=False)
```

`_write_json` becomes `text = _dump_json(data, 0, ()) + "\n"`.

Notes/validation:
- Using the **key name `"cmd"`** (not a hardcoded path) for req 6 is deliberately generic but safe: I confirmed via the schema that `"cmd"` appears in exactly two places (`mcp.tools.*.*.cmd`, `mcp.servers.*.cmd`) and nowhere else in the whole document shape, so it satisfies "only these two specific array locations" without brittle path-matching.
- Req 2 ("enabled first, recursively, any depth") is handled purely as a pre-serialization key reorder (`_reorder_enabled_first`), independent of the compaction logic — dict insertion order elsewhere in the code doesn't need to change at all, since the writer normalizes it.
- `_same_json` / `_read_json` are untouched (structural, not textual, comparison) — a pure formatting change alone won't trigger a rewrite of already-in-sync files. This is fine here because req 1/5/7 all touch the real repo's `ai/tool-settings/settings.json` content this run, so the new formatting will actually be exercised end-to-end during verification.
- No changes needed to `_write_text_if_changed` (TOML/rules files, untouched by this).

### `scripts/°base/ai/settings/°settings_lib/mcp_servers.py`

1. **`parse_claude_mcp`**: remove `"enabled": True` from both the stdio and http branches (return entries without an `enabled` key at all). Add a comment explaining why (`.mcp.json` has no enabled concept; see `cli._merge_mcp_native_additions`'s fallback-to-existing logic).
2. **`_resolve_server_argv`**: relax the cmd requirement (req 5, sub-part 3) —
```python
def _resolve_server_argv(mcp, server, git_root):
    tools = mcp.get("tools") or {}
    argv: list[str] = []
    for ref in server.get("tools") or []:
        resolved = _resolve_tool_ref(tools, ref)
        if resolved is None:
            return None
        argv.extend(resolved)
    cmd = server.get("cmd")
    if cmd is not None:
        if not isinstance(cmd, list) or not all(isinstance(t, str) for t in cmd):
            return None
        argv.extend(cmd)
    if not argv:
        return None
    return [_substitute_git_root(t, git_root) for t in argv]
```
   (Allows missing/empty `cmd` as long as `tools` contributes a non-empty argv; still rejects a genuinely empty combined result.)
3. **New: `extract_tools_from_cmd(tools: dict, cmd: list[str], git_root: Path) -> tuple[list[str], list[str]]`** — greedy, leftmost-first, chained matching:
   - Build a flat candidate list of `(ref_string, resolved_tokens)` over every `(name, variant)` in `tools` where `entry["mode"] == "prefix"`, substituting git-root via the existing `_substitute_git_root` helper; `ref_string = name` when `variant == ""`, else `f"{name}@{variant}"`.
   - At each position in `cmd`, find all candidates whose `resolved_tokens` is a prefix of `cmd[pos:]`. **Tie-break: longest `resolved_tokens` first, then alphabetical by `(name, variant)`** for determinism. On a match, append `ref_string` to the output, advance `pos` by `len(resolved_tokens)`, and repeat from the new position (supports chaining, matches `_resolve_server_argv`'s `tools[0]+tools[1]+...+cmd` semantics).
   - Stop when no candidate matches at the current position; return `(tool_refs, cmd[pos:])`.
   - Termination is guaranteed since every candidate's `cmd` is non-empty (schema `minItems: 1`), so each successful match strictly advances `pos`.
   - Document as a heuristic, not a globally-optimal matcher: greedy longest-match-at-each-step can in rare pathological cases miss a chain that a different (shorter-first) choice would have completed. Acceptable given this only affects a best-effort reconstruction on native round-trip; falls back to storing the flat `cmd` (its natural degenerate case) when nothing matches.
4. **New: `_reconstruct_stdio_entry(mcp: dict, cmd: list[str], enabled: bool, git_root: Path) -> dict`**:
```python
def _reconstruct_stdio_entry(mcp, cmd, enabled, git_root):
    tool_refs, remaining = extract_tools_from_cmd(mcp.get("tools") or {}, cmd, git_root)
    entry = {"enabled": enabled, "type": "stdio"}
    if tool_refs:
        entry["tools"] = tool_refs
    if remaining or not tool_refs:
        entry["cmd"] = remaining
    return entry
```
   When nothing matches, this degenerates exactly to "store the flat cmd" (your stated fallback expectation).

### `scripts/°base/ai/settings/°settings_lib/cli.py`

1. **`_load_layer`**: change the `combined_mcp_native` construction from blind overwrite to per-field merge:
```python
combined_mcp_native: dict[str, Any] = {}
for _, data in sorted(mcp_native_sources, key=lambda item: item[0]):
    for name, entry in data.items():
        merged_entry = dict(combined_mcp_native.get(name) or {})
        merged_entry.update(entry)
        combined_mcp_native[name] = merged_entry
```
   (Only meaningful in combination with `parse_claude_mcp` no longer emitting a fake `enabled`.)
2. Fallback default (line 179): `{"version": 1, ...}` → `{"version": hooks.CURRENT_VERSION, ...}` (import `CURRENT_VERSION` from `.hooks`).
3. **`_merge_mcp_native_additions`**: rewrite per the design validated above —
```python
def _merge_mcp_native_additions(shared, native_servers, git_root):
    if not native_servers:
        return shared
    shared = deepcopy(shared)
    mcp = shared.setdefault("mcp", {"tools": {}, "servers": {}})
    mcp.setdefault("tools", {})
    servers = mcp.setdefault("servers", {})

    for name, entry in native_servers.items():
        existing = servers.get(name)
        is_http = entry.get("type") == "http" or (existing is not None and existing.get("type") == "http")

        if existing is None:
            new_enabled = entry.get("enabled", True)
            servers[name] = (
                mcp_servers._reconstruct_stdio_entry(mcp, entry.get("cmd") or [], new_enabled, git_root)
                if entry.get("type") == "stdio" else entry
            )
            continue

        existing_enabled = existing.get("enabled", True)
        new_enabled = entry.get("enabled", existing_enabled)  # no hardcoded True default

        if is_http:
            existing_sig = (existing.get("type"), existing.get("url"), tuple(sorted((existing.get("headers") or {}).items())))
            new_sig = (entry.get("type"), entry.get("url"), tuple(sorted((entry.get("headers") or {}).items())))
        else:
            existing_sig = mcp_servers._resolve_server_argv(mcp, existing, git_root)
            new_sig = mcp_servers._resolve_server_argv(mcp, entry, git_root)

        if existing_sig == new_sig:
            if existing_enabled != new_enabled:
                updated = deepcopy(existing)
                updated["enabled"] = new_enabled
                servers[name] = updated
            continue

        if is_http:
            entry = deepcopy(entry)
            entry["enabled"] = new_enabled
            servers[name] = entry
        else:
            servers[name] = mcp_servers._reconstruct_stdio_entry(mcp, entry.get("cmd") or [], new_enabled, git_root)

    mcp["servers"] = servers
    shared["mcp"] = mcp
    return shared
```
   I traced this against every existing MCP-related test (see below) plus the real bugsink scenario and confirmed correctness in each case.
4. `_apply_or_check`'s `codex_config_path` block: replace `shared.get("enabledPlugins") or {}` with a flatten of the new nested shape:
```python
plugins_flat = {pid: bool((e or {}).get("enabled", True)) for pid, e in (shared.get("plugins") or {}).items()}
plugins_text = codex_toml.render_codex_plugins(current_text, plugins_flat)
```
   (`codex_toml.render_codex_plugins`'s own signature stays flat `dict[str, bool]` — TOML-only, unaffected.) The `_load_layer` block that constructs `{"enabledPlugins": plugins}` from `codex_toml.parse_codex_plugins` (lines 164–166) needs **no change** — it still feeds a flat native-source dict through `_merge`/`_normalize_native`, which now does the flat→nested conversion internally.

### `scripts/°base/ai/settings/°settings_lib/mcp_servers.py` (TOML rendering, req 2)

`_server_table_lines`: move the `enabled = ...` line to immediately after the `[mcp_servers."name"]` header, before the type-specific branch:
```python
lines = [f"[{table_name}]"]
lines.append(f'enabled = {"true" if server.get("enabled", True) else "false"}')
if server.get("type") == "http":
    ...
else:
    argv = _resolve_server_argv(mcp, server, git_root)
    if argv is None:
        return None
    ...
```
Harmless that `enabled` gets computed before the possible early `return None` — the whole `lines` list is discarded by the caller (`render_codex_mcp_block`) when `None` is returned, exactly as today.

### `ai/tool-settings/mcp.schema.json`

Replace the stdio `then: {"required": ["cmd"]}` clause with an `anyOf` requiring a non-empty `cmd` or a non-empty `tools`:
```json
{
  "if": { "properties": { "type": { "const": "stdio" } }, "required": ["type"] },
  "then": {
    "anyOf": [
      { "required": ["cmd"] },
      { "required": ["tools"], "properties": { "tools": { "minItems": 1 } } }
    ]
  }
}
```
Note: the base `cmd` property already declares `minItems: 1`, so `required: ["cmd"]` alone is sufficient for that branch; the base `tools` property currently has **no** `minItems`, so the `anyOf`'s `tools` branch must locally add `"minItems": 1` — otherwise `{"tools": []}` with no `cmd` would incorrectly satisfy the schema.

### `paths.py`

No changes needed — version constant belongs with the normalization logic in `hooks.py`, not with path constants.

## Existing tests requiring updates

- `test_render_claude_omits_mcp_server_lists_when_no_servers` — unaffected (still passes as-is; kept as regression guard).
- All fixtures using `"enabledPlugins": {...}` in shared-file JSON strings/dicts (lines ~692–931 per current line numbers) → `"plugins": {id: {"enabled": bool}, ...}`.
- `test_load_layer_merges_hand_edited_codex_rules_and_plugins`: assertion `shared["enabledPlugins"] == {"hand-added@marketplace": True}` → `shared["plugins"] == {"hand-added@marketplace": {"enabled": True}}`.
- `test_apply_or_check_writes_and_is_idempotent`: fixture `"enabledPlugins": {"demo@marketplace": true}` → `"plugins": {"demo@marketplace": {"enabled": true}}`; TOML assertion (`[plugins."demo@marketplace"]`) unaffected.
- `test_exact_command_without_wildcard_does_not_duplicate_across_runs`: fixture `"enabledPlugins": {}` → `"plugins": {}`.
- `test_parse_claude_mcp_round_trip`: currently asserts `parsed["bugsink"] == {..., "enabled": True}` — must drop the `"enabled"` key from the expected dict (per the `parse_claude_mcp` fix); keep the existing `assertNotIn("tools", ...)`.
- All `"version": 1` fixtures throughout can stay `1` (that's the whole point — they must still load correctly); no fixture needs its version bumped, but consider adding one assertion somewhere (e.g. in `CliLoadLayerTests`) that `_load_layer(...)["version"] == 2` to lock in req 1's actual effect.

## New tests to add

1. **Req 1** (`HooksTests`) — `test_normalize_native_upgrades_v1_file_shape`: embed a **static, trimmed fixture** shaped like the pre-migration file at `95f48bc69b93f990ce7986344ca05722192c4ff1~1` (raw-string `permissions.allow`/`deny` like `"Bash(tree:*)"`, flat `enabledPlugins: {"openai-developers@openai-developers": true}`, no `mcp` key, `"version": 1`). Call `hooks._normalize_native(fixture)` and assert: `normalized["version"] == hooks.CURRENT_VERSION` (2); permission strings upgraded to structured dicts (e.g. `{"type": "bash", "command": "tree:*"}`); `normalized["plugins"] == {"openai-developers@openai-developers": {"enabled": True}}`; `normalized["mcp"] == {"tools": {}, "servers": {}}`. **Do not shell out to `git show` inside the test** — this repo is designed to be adopted by other repos via checkout/rebase/merge, and full commit history isn't guaranteed to survive that; embed a static copy of the relevant excerpt instead (informed by, but independent of, the real commit).
2. **Req 4** (`HooksTests`) — `test_render_claude_populates_empty_bucket_when_all_servers_same_state`: one server enabled only → assert `enabledMcpjsonServers == ["x"]` **and** `disabledMcpjsonServers == []` (both keys always present); mirror for all-disabled.
3. **Req 5** (`McpServersTests`):
   - `test_extract_tools_from_cmd_matches_single_tool_prefix` and `..._chains_multiple_tools` (leftmost-first, chained).
   - `test_extract_tools_from_cmd_no_match_returns_full_cmd_unchanged` (fallback case).
   - `test_extract_tools_from_cmd_prefers_longest_match_on_ambiguous_prefix` (tie-break policy).
   - `test_resolve_server_argv_allows_tools_only_server_with_no_cmd`.
4. **Req 5** (`CliLoadLayerTests`) — the key regression tests:
   - `test_load_layer_preserves_authored_tools_when_native_enabled_flag_only_changes` — reproduce the real bugsink scenario end-to-end: authored `{"enabled": false, "tools": [".env"], "cmd": [...]}`, render to both `.mcp.json` and `.codex/config.toml`, load both back via `_load_layer`, assert the authored `tools`/`cmd` shape and `enabled: false` **both** survive unchanged (this is the direct regression test for the drift you found in git status).
   - `test_load_layer_reconstructs_tools_when_native_cmd_content_genuinely_changes` — start from an authored tools-based entry, hand-edit `.codex/config.toml`'s `args` to a **different** but still tool-prefixed argv (simulating a real content change), load back, assert the new entry has `tools` reconstructed (not a raw flattened `cmd`).
   - `test_load_layer_stores_flat_cmd_when_no_tool_prefix_matches` — hand-edited native cmd with no matching tool prefix → assert plain flat `cmd`, no `tools` key (fallback case).
   - `test_load_layer_claude_only_source_does_not_reset_enabled_state` — a project with only `.mcp.json` (no Codex config) and an existing authored `enabled: false` entry whose cmd is unchanged → assert `enabled` stays `false` after `_load_layer` (regression test for the `parse_claude_mcp`-omits-enabled + fallback-to-existing fix).
5. **Req 3/6** (`json_io` — new `JsonIoTests` class, doesn't exist yet, needs adding): `test_write_json_compacts_permission_entries_one_per_line`, `test_write_json_compacts_cmd_arrays_single_line`, `test_write_json_puts_enabled_first_at_any_depth`, ideally driven by writing a small dict to a temp file and asserting on exact text (not just parsed structure) since `_same_json`-style structural equality won't catch formatting regressions.
6. **Req 7**: `test_normalize_native_converts_flat_enabled_plugins_to_nested_shape` and `test_render_claude_builds_flat_enabled_plugins_from_nested_shape` (round-trip both directions).

## Verification

After implementation, run the full suite plus a real sync in this repo:
```
uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v
python3 scripts/°base/ai/settings/sync.py
git diff ai/tool-settings/settings.json .claude/settings.json .codex/config.toml .mcp.json
```
Expect: `ai/tool-settings/settings.json`'s `mcp.servers.bugsink` returns to its authored `{"enabled": false, "type": "stdio", "tools": [".env"], "cmd": ["npx", "-y", "bugsink-mcp"]}` shape (fixing the currently-uncommitted drift), `version` becomes `2`, `enabledPlugins` becomes `plugins` (nested), and `.claude/settings.json`'s `enabledMcpjsonServers`/`disabledMcpjsonServers` mismatch noted in `git status` resolves itself as a natural consequence of the `enabled` fix. Then re-run `sync.py --check` to confirm idempotency (zero further changes).

### Critical Files for Implementation
- /Users/user/Documents/programming/Python/base/scripts/°base/ai/settings/°settings_lib/cli.py
- /Users/user/Documents/programming/Python/base/scripts/°base/ai/settings/°settings_lib/mcp_servers.py
- /Users/user/Documents/programming/Python/base/scripts/°base/ai/settings/°settings_lib/hooks.py
- /Users/user/Documents/programming/Python/base/scripts/°base/ai/settings/°settings_lib/json_io.py
- /Users/user/Documents/programming/Python/base/scripts/°base/tests/test_ai_settings_sync.py
- /Users/user/Documents/programming/Python/base/ai/tool-settings/mcp.schema.json