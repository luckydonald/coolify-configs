Perfect! Now I have all the information I need. Let me create a comprehensive report.

## Comprehensive Report: Permission String Flow Through AI Tool Settings System

Based on thorough investigation of the repository, here is a detailed analysis of how permission strings flow through the system:

---

### 1. Permission-Check Hook: Parsing and Matching

**File**: `/home/user/git/luckydonald/base/.claude/hooks/permission-check.py`

This script does **NOT** currently parse or match permission strings like `"Bash(git status:*)"` or `"Read(**/.env*)"`. Instead, it enforces git-specific policies through hardcoded logic:

**Syntax rules and matching:**
- **Tool filtering** (lines 131-133): Only processes `tool_name` in `{"Bash", "shell", "unified_exec"}`. Other tools pass through immediately.
- **Command extraction** (lines 136-144): Reads `tool_input.command` or `tool_input.cmd` (no matching against allow/deny strings).
- **Git add policy** (lines 31-48, 183-184): Hardcoded denial of `-A`, `--all`, `-u`, `--update` flags and `.` / `:/` paths.
- **Git commit policy** (lines 97-107, 185-186): Searches for `"co-authored-by:"` (case-insensitive, line 102) and `"noreply@anthropic"` in parsed argv and raw command string (lines 156-158).
- **No permission string matching**: The script **does NOT** read from `permissions.allow` or `permissions.deny`.

**Output format** (lines 10-17): Returns Claude's PermissionRequest hook response structure:
```python
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "permissionDecision": "deny" | (empty dict for allow),
    "permissionDecisionReason": "Human-readable reason"
  }
}
```

**Invocation**: Called by both Claude and Codex via the shared PermissionRequest hook (same matcher `"Bash|shell|unified_exec"` in both `.claude/settings.json` line 6 and `.codex/hooks.json` line 6). The hook receives the same JSON payload format from both tools.

---

### 2. Settings Sync Script: Permissions Handling

**File**: `/home/user/git/luckydonald/base/scripts/°base/ai/settings/sync.py`

**Key findings on permission string format and handling:**

**_CORE_SHARED_KEYS** (line 35):
```python
_CORE_SHARED_KEYS = {"version", "hooks", "permissions", "enabledPlugins"}
```
`permissions` is a tracked shared key.

**Current format** (observed in `/home/user/git/luckydonald/base/ai/tool-settings/settings.json` lines 96-211):
```json
"permissions": {
  "allow": [
    "Bash(tree:*)",
    "Bash(git status:*)",
    "Skill(commit-with-lplp-style)",
    "Read(ai/skills/commit-with-lplp-style/SKILL.md)"
  ],
  "deny": [
    "Read(**/.env*)",
    "Read(**/secrets/**)"
  ]
}
```

**Syntax rules supported:**
- **Tool prefix** (e.g., `Bash(...)`, `Read(...)`, `Skill(...)`): Parsed as `ToolName(CONTENT)`.
- **Wildcard patterns**: `*` and `**` are preserved as-is (no parsing in `sync.py`).
- **Command/path patterns**: Content after tool name is treated as literal strings; examples:
  - `Bash(git status:*)` — command match with wildcard
  - `Bash(tree:*)` — single command
  - `Read(**/.env*)` — glob pattern for file read
  - `Skill(commit-with-lplp-style)` — skill invocation

**_normalize_native** (lines 240-256):
- Normalizes hook command paths, does **NOT** normalize permission strings (they are copied verbatim in line 254).

**_merge** (lines 263-304):
- **Permissions merging** (lines 293-299): Unions allow/deny lists, removes duplicates via `_unique()` (line 298).
- No matching logic; just string deduplication.

**render_claude** (lines 405-413):
```python
def render_claude(shared: dict[str, Any]) -> dict[str, Any]:
    data = _render_hooks(shared, "claude")
    permissions = shared.get("permissions")
    if permissions:
        data["permissions"] = deepcopy(permissions)  # Line 409
    enabled_plugins = shared.get("enabledPlugins")
    if enabled_plugins:
        data["enabledPlugins"] = deepcopy(enabled_plugins)
    return data
```
- **Permissions ARE written to `.claude/settings.json`** (line 409).
- Permissions are deep-copied as-is; no transformation.

**render_codex_hooks** (lines 416-417):
```python
def render_codex_hooks(shared: dict[str, Any]) -> dict[str, Any]:
    return _render_hooks(shared, "codex")
```
- **Permissions are NOT included in the codex rendering**. Only hooks are returned by `_render_hooks()` (which only yields `{"hooks": {...}}`).
- `.codex/hooks.json` receives **NO** permissions data.

**_load_layer** (lines 611-626):
- Loads from shared settings (JSON), Claude native, and Codex native.
- Merges them all via `_merge()`.
- Returns a normalized shared state with hooks and permissions.

**_apply_or_check** (lines 629-646):
- Line 636: Writes shared state to `shared_path` (ai/tool-settings/settings.json).
- Line 637: Writes Claude rendering to `claude_path` (.claude/settings.json) **with permissions**.
- Line 638: Writes Codex rendering to `codex_path` (.codex/hooks.json) **without permissions**.

**CODEX CONFIG TOML** (lines 87-175, 140-175):
- `_migrate_codex_feature_flag()` and `_rewrite_codex_feature_flag()` only handle the deprecated `[features].codex_hooks → [features].hooks` migration.
- No generation of `~/.codex/config.toml` content by `sync.py`.
- No permissions data written to Codex's config.toml.

**Confirmed**: `permissions` (allow/deny) is **ONLY** written to `.claude/settings.json` via `render_claude()`. Codex's `.codex/hooks.json` never receives permissions. Codex's `~/.codex/config.toml` is **read but not overwritten wholesale** by `sync.py` — only a narrow feature-flag migration is applied.

---

### 3. Test Patterns for Permissions/Hooks

**File**: `/home/user/git/luckydonald/base/scripts/°base/tests/test_ai_settings_sync.py`

**Tests for permissions rendering:**

- **test_render_claude_keeps_permissions** (lines 196-205):
  ```python
  shared = {
      "hooks": {},
      "permissions": {"allow": ["Bash(git status:*)"], "deny": ["Read(**/.env*)"]},
  }
  rendered = MODULE.render_claude(shared)
  self.assertEqual(rendered["permissions"]["allow"], ["Bash(git status:*)"])
  self.assertEqual(rendered["permissions"]["deny"], ["Read(**/.env*)"])
  ```
  Verifies that `render_claude()` preserves permission strings exactly as-is.

- **test_merge_unions_permissions_without_duplicates** (lines 225-232):
  ```python
  base = {"permissions": {"allow": ["A", "B"], "deny": ["X"]}}
  incoming = {"permissions": {"allow": ["B", "C"], "deny": ["X", "Y"]}}
  merged = MODULE._merge(base, incoming)
  self.assertEqual(merged["permissions"]["allow"], ["A", "B", "C"])
  self.assertEqual(merged["permissions"]["deny"], ["X", "Y"])
  ```
  Verifies union behavior and deduplication.

- **test_load_layer_preserves_shared_metadata** (lines 207-223):
  Verifies that extra metadata in shared settings (like `download_link`) is preserved in the shared layer but NOT rendered to Claude or Codex outputs.

**No tests** for matching or parsing permission strings (because `sync.py` doesn't do matching).

---

### 4. Existing Permission Format Redesign Discussions

**Files with relevant TODOs/comments:**

- **`/home/user/git/luckydonald/base/ai/°base/query.tmp.md` (lines 45-48)**:
  ```
  › I want to use the codex project local `./.codex/config.toml` to whitelist commands - like claude does.
    Is that possible? If so,
    for that you can split the @ai/tool-settings/settings.json into proper objects, i.e. `{ type: "bash", command: "tree:*" }` instead of `"Bash(tree:*)"`,
    and merge that back together as needed.
  ```
  **Proposal**: Split from string format `"Bash(tree:*)"` to object format `{ type: "bash", command: "tree:*" }` to enable easier transformation to both Claude and Codex native formats.

- **`/home/user/git/luckydonald/base/ai/°base/plans/002_codex-compat.md` (line 37)**:
  ```
  - Shared permission hook continues blocking unsafe git add and Co-Authored-By, and is adapted for Codex's hook payload/output format.
  ```
  Indicates that the shared permission hook (`permission-check.py`) should be adapted for Codex's payload/output format (but currently it already is — both use the same JSON interface).

- **No other `ai/tool-settings/` files exist** besides `settings.json`, `settings.local.json`, and `README.md`. No existing neutral format definition file.

---

### 5. Codex Permission Mechanism: Native vs. Hook-Based

**Codex's dual permission system** (from `/home/user/git/luckydollah/base/ai/references/https/developers.openai.com/codex/permissions.md`):

Codex has **two independent permission enforcement mechanisms**:

1. **Native permission profiles** (`.codex/config.toml` via `[permissions.*]` sections, lines 35-185):
   - Filesystem rules (read/write/deny with glob patterns, line 186-312).
   - Network rules (domain allowlist/denylist, line 318-388).
   - Enforced by sandboxing (Seatbelt on macOS, bubblewrap/seccomp on Linux/WSL, line 440-454).
   - Does NOT require hooks.

2. **Hook-based PermissionRequest** (via `.codex/hooks.json`, this repo's current approach):
   - Custom script runs when Codex attempts a tool use.
   - Receives payload: `{"tool_name": "...", "tool_input": {...}, "command": ...}`.
   - Returns approval/denial in response.
   - Currently shared with Claude (same `permission-check.py` script).

**Current state in this repo**:
- The shared `permission-check.py` hook in `.codex/hooks.json` enforces git add/commit policies via PermissionRequest.
- `.codex/config.toml` is **not used at all** (file doesn't exist in repo yet).
- **Codex does NOT currently have command allow/deny lists** like Claude's `permissions.allow/deny` in `.claude/settings.json`. The `"Bash(git status:*)"` format is **Claude-specific**.

**Implications for redesign**:
- Codex's native permission system uses **structured TOML** with separate filesystem and network scopes.
- The string format `"Bash(git status:*)"` doesn't map cleanly to Codex's filesystem rule syntax (`"." = "read"`, `"**/*.env" = "deny"`).
- Permission-check hook could be adapted, but it won't replace Codex's native permission profiles.

---

### Summary Table

| Aspect | Current Implementation |
|--------|------------------------|
| **Permission string format** | `"ToolName(pattern)"` e.g. `"Bash(git status:*)"`, `"Read(**/.env*)"` |
| **Stored in** | `ai/tool-settings/settings.json` under `permissions.allow[]` and `permissions.deny[]` |
| **Rendered to Claude** | Yes, via `render_claude()` → `.claude/settings.json` (lines 405-413) |
| **Rendered to Codex** | No, `render_codex_hooks()` returns only hooks (line 417) |
| **Parsed/matched by sync.py** | No, treated as opaque strings (only deduplicated) |
| **Used by permission-check.py** | No, uses hardcoded git policy logic (not string matching) |
| **Wildcard syntax** | `*` and `**` preserved as-is (no normalization) |
| **Permission-check.py invocation** | Both Claude and Codex via PermissionRequest hook (line 6 in both `.claude/settings.json` and `.codex/hooks.json`) |
| **Codex native permission system** | Permission profiles in `~/.codex/config.toml` (separate from hooks, not currently used) |

---

### Syntax Rules Definitively Supported

1. **Tool prefix syntax**: `ToolName(CONTENT)` where ToolName ∈ {`Bash`, `Read`, `Skill`, ...}
2. **Wildcard patterns**: `*` (single-level), `**` (multi-level) — preserved verbatim
3. **Command suffixes**: `:*` for command pattern matching (e.g., `git status:*`)
4. **Glob patterns**: `**/.env*`, `**/secrets/**` for file matching
5. **Literal paths**: Exact paths like `ai/skills/commit-with-lplp-style/SKILL.md`
6. **No escaping or special operators**: Strings are split via `|` in matchers only, not in permissions

---

This completes the thorough investigation. The permission system is currently minimal, with `sync.py` acting only as a pass-through for Claude's settings, and Codex having no command-level allow/deny support via this mechanism (relying instead on its native permission profiles in config.toml, which are not yet integrated).