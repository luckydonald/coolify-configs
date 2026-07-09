Perfect! Now I have a comprehensive understanding. Let me provide a detailed report.

## Research Report: AI Tool Permission Settings Sync (Claude ↔ Codex)

Based on my thorough review of the codebase, here's a complete analysis of how permission settings are synced between Claude and Codex:

### 1. **Permission Entry Data Model** (`commands.py`, lines 8-98)

The codebase uses a **neutral, type-based schema** for all permission entries:

```python
{
  "type": "<type>",
  "<field_name>": "<value>"
}
```

**Supported permission types and their field names:**

| Type | Primary Field | Example Entry |
|------|---------------|---------------|
| `bash` | `command` | `{"type": "bash", "command": "ls:*"}` |
| `read` | `path` | `{"type": "read", "path": "**/.env*"}` |
| `write` | `path` | `{"type": "write", "path": "src/**"}` |
| `edit` | `path` | `{"type": "edit", "path": "README.md"}` |
| `glob` | `path` | `{"type": "glob", "path": "**/*.py"}` |
| `skill` | `name` | `{"type": "skill", "name": "commit-with-lplp-style"}` |
| `grep` | `pattern` | `{"type": "grep", "pattern": ".*"}` |
| `webfetch` | `pattern` | `{"type": "webfetch", "pattern": "..."}` |
| `websearch` | `pattern` | `{"type": "websearch", "pattern": "..."}` |
| `raw` | `value` | `{"type": "raw", "value": "<anything>"}` |

**Defined in:** `/home/user/git/luckydonald/base/scripts/°base/ai/settings/°settings_lib/commands.py`, lines 12-32

### 2. **Claude Permission String Format (Render/Parse)**

Claude uses a string format: `Tool(content)` (e.g., `"Bash(ls:*)"`, `"Read(src/**)"`)

**Conversion functions:**
- **`_parse_claude_permission_entry(entry)`** (lines 35-48): Parses Claude's `Tool(content)` string → neutral dict schema
  - Handles both string format and dict passthrough
  - Regex: `^([A-Za-z_]\w*)\((.*)\)$`
  - Falls back to `{"type": "raw", "value": entry}` if unparseable

- **`_render_claude_permission_entry(entry)`** (lines 51-64): Renders neutral dict → Claude's `Tool(content)` string
  - Inverse operation
  - Uses `_TYPE_TO_CLAUDE_TOOL` mapping to capitalize tool names

**Live example in** `/home/user/git/luckydonald/base/.claude/settings.json` (lines 96-210):
```json
"permissions": {
  "allow": [
    "Bash(tree:*)",
    "Bash(ls:*)",
    "Skill(commit-with-lplp-style)",
    "Read(ai/skills/commit-with-lplp-style/SKILL.md)"
  ],
  "deny": [
    "Read(**/.env*)"
  ]
}
```

### 3. **Bash-to-Codex `.rules` Translation** (`codex_rules.py`)

Codex uses a different permission model: **`.rules` files with `prefix_rule()` calls** for bash-only permissions.

**Key function:** `_bash_pattern_to_prefix(command)` (lines 75-97 in `commands.py`)
- Converts Claude bash patterns into Codex `prefix_rule(pattern = [...], decision = "...")` format
- **Only translates simple commands** without shell metacharacters (`$`, `;`, `|`, `<`, `>`, `&`)
- Cannot translate environment assignments or compound commands
- Example: `"ls:*"` → `prefix_rule(pattern = ["ls"], decision = "allow")`

**In practice:**
- `/home/user/git/luckydonald/base/.codex/rules/generated.rules` contains auto-generated `prefix_rule()` calls
- Skipped entries (those with shell metacharacters) are logged as comments with their original JSON
- **Important:** Codex rules do **NOT** cover Read, Write, Edit, Skill, or other permission types—only Bash

**File locations:**
- Generated: `/home/user/git/luckydonald/base/.codex/rules/generated.rules` (lines 1-94)
- Local overrides: `/home/user/git/luckydonald/base/.codex/rules/generated.local.rules`

### 4. **MCP Server Config Sync** (`mcp_servers.py`)

MCP servers are synced via a **neutral `mcp` schema** in the shared settings file:

**Neutral schema structure** (`ai/tool-settings/mcp.schema.json`):
```json
{
  "mcp": {
    "tools": {
      "tool_name": {
        "variant_name": {
          "mode": "prefix",
          "cmd": ["npx", "-y", "envmcp", "--env-file", "ai/.env"]
        }
      }
    },
    "servers": {
      "server_name": {
        "enabled": true,
        "type": "stdio",
        "tools": ["tool_name", "tool_name@variant_name"],
        "cmd": ["npx", "-y", "bugsink-mcp"]
      }
    }
  }
}
```

**Live example** (`ai/tool-settings/settings.json`, lines 549-592):
```json
"mcp": {
  "tools": {
    ".env": {
      "": {"mode": "prefix", "cmd": ["npx", "-y", "envmcp", "--env-file", "ai/.env"]},
      "repo-root": {"mode": "prefix", "cmd": ["npx", "-y", "envmcp", "--env-file", "$(git rev-parse --show-toplevel)/.env"]}
    }
  },
  "servers": {
    "bugsink": {
      "enabled": false,
      "type": "stdio",
      "tools": [".env"],
      "cmd": ["npx", "-y", "bugsink-mcp"]
    }
  }
}
```

**Claude rendering** (`.mcp.json`):
- Renders to Claude's native `.mcp.json` format under `mcpServers` key
- File: `/home/user/git/luckydonald/base/.mcp.json`
- Example output (lines shown in earlier read):
```json
{"mcpServers": {
  "bugsink": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "envmcp", "--env-file", "ai/.env", "npx", "-y", "bugsink-mcp"]
  }
}}
```

**Codex rendering** (`.codex/config.toml`):
- Renders to Codex's `[mcp_servers.<name>]` TOML tables
- File: `/home/user/git/luckydonald/base/.codex/config.toml` (lines 6-13)
- Example output:
```toml
[mcp_servers."bugsink"]
command = "npx"
args = ["-y", "envmcp", "--env-file", "ai/.env", "npx", "-y", "bugsink-mcp"]
enabled = false
```

**Key functions:**
- `render_claude_mcp(shared, git_root)` (lines 50-70): Renders neutral → `.mcp.json`
- `render_codex_mcp_block(shared, git_root)` (lines 115-132): Renders neutral → `.codex/config.toml` block
- `parse_claude_mcp(mcp_json_data)` (lines 73-93): Parses `.mcp.json` → neutral
- `parse_codex_mcp_toml(text)` (lines 165-193): Parses `.codex/config.toml` → neutral

### 5. **NO EXISTING MCP TOOL PERMISSION SUPPORT**

**Critical finding:** There is **NO handling of MCP-specific tool permissions** currently in the codebase:

- ❌ No references to `mcp__<server>__<tool>` permission format
- ❌ No `approval_mode`, `enabled_tools`, or `disabled_tools` support in `mcp_servers.py`
- ❌ No syncing of per-tool approval settings between Claude and Codex
- ✓ Only server enable/disable state is synced (`enabled` boolean)

**Grep results confirm:**
- `grep -rn "approval_mode\|enabled_tools\|disabled_tools" /scripts/°base` → **no results**
- Only one `mcp__` reference found: in test file line 141 (test comment about using a tool)

### 6. **How Codex Handles MCP Tool Permissions** (From Documentation)

**Codex's mechanism** (from `ai/references/https/developers.openai.com/codex/mcp.md`, lines 89-142):

In `.codex/config.toml` under `[mcp_servers.<server>]`:

```toml
[mcp_servers.example_server]
enabled_tools = ["tool1", "tool2"]     # Allow-list (optional)
disabled_tools = ["tool3"]              # Deny-list (optional after allow)
default_tools_approval_mode = "prompt"  # Default: "auto", "prompt", "approve"

[mcp_servers.example_server.tools.tool1]
approval_mode = "approve"               # Per-tool override
```

**Current Codex does NOT sync these settings from anywhere** — they must be manually configured in `.codex/config.toml` or added via `codex mcp` CLI commands. The sync system in this repo currently **ignores** these fields entirely.

### 7. **Sync Pipeline and File Locations**

**Master flow** (`cli.py`, lines 112-159):

```
ai/tool-settings/settings.json (neutral, tracked)
           ↓
      _load_layer() [merge shared + native files]
           ↓
    render_claude() → .claude/settings.json
    render_codex_hooks() → .codex/hooks.json
    render_codex_rules() → .codex/rules/generated.rules
    render_codex_mcp_block() → .codex/config.toml
    render_claude_mcp() → .mcp.json
```

**Entry points:**
- Sync script: `/home/user/git/luckydonald/base/scripts/°base/ai/settings/sync.py` (lines 215-271)
- Main function: `cli.main()` in `/home/user/git/luckydonald/base/scripts/°base/ai/settings/°settings_lib/cli.py`
- Triggered on SessionStart hook (`.claude/settings.json` line 21-23)

**File paths** (`paths.py`):
- Tracked shared: `ai/tool-settings/settings.json`
- Local shared: `ai/tool-settings/settings.local.json`
- Claude: `.claude/settings.json`, `.claude/settings.local.json`
- Codex: `.codex/hooks.json`, `.codex/hooks.local.json`, `.codex/config.toml` (project-scoped)
- Codex rules: `.codex/rules/generated.rules`, `.codex/rules/generated.local.rules`
- Claude MCP: `.mcp.json`

### 8. **Shared Permissions Data Flow** (`hooks.py`)

**Permissions handling** (lines 63-69, 130-136):

```python
def _normalize_permissions(permissions):
    # Parse Claude string format → neutral dicts
    for key in ("allow", "deny"):
        normalized[key] = [_parse_claude_permission_entry(v) for v in values]

def render_claude(shared):
    # Render neutral dicts → Claude string format
    rendered_permissions[key] = [_render_claude_permission_entry(v) for v in values]
```

**MCP in render_claude** (lines 264-271):
```python
servers = (shared.get("mcp") or {}).get("servers") or {}
if servers:
    enabled = [name for name, server in servers.items() if server.get("enabled", True)]
    disabled = [name for name, server in servers.items() if server.get("enabled", True) is False]
    if enabled:
        data["enabledMcpjsonServers"] = enabled
    if disabled:
        data["disabledMcpjsonServers"] = disabled
```

Only server **enable/disable** state is rendered to Claude (one-directional), not tool permissions.

---

## Summary: What's Missing for MCP Tool Permissions

**To fully implement MCP tool permission syncing, you would need to:**

1. **Extend the permission schema** to support type `"mcp_tool"`:
   ```json
   {"type": "mcp_tool", "server": "github", "tool": "create_issue", "approval_mode": "prompt"}
   ```

2. **Update `commands.py`**:
   - Add `"mcp_tool": "approval_mode"` to `_TYPE_FIELD`
   - Add `"mcp_tool": "McpTool"` (or similar) to `_TYPE_TO_CLAUDE_TOOL` (or handle specially)

3. **Extend `mcp_servers.py`**:
   - Add functions to render `approval_mode` and tool enable/disable lists to `.codex/config.toml`
   - Parse `[mcp_servers.X.tools.Y]` approval settings back from Codex config

4. **Update `.codex/config.toml` rendering** to include per-tool approval settings (currently only syncs server enable/disable)

5. **Update Claude's permission representation** to handle MCP tool permissions (currently Claude doesn't have a native format for these)

This explains why the test file mentions `mcp__github_comment__update_claude_comment` (line 141 of test) — it's a reference to the **desired format** but not yet implemented in the sync system.