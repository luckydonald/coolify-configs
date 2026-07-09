In this repo (/home/user/git/luckydonald/base), I need to understand how AI tool permission settings are synced between Claude and Codex, specifically around MCP server tool permissions.

Read these files fully and report their contents/structure:
1. /home/user/git/luckydonald/base/scripts/°base/ai/settings/°settings_lib/commands.py — this handles parsing/rendering permission entries (bash commands, skills, etc.) between Claude's permission string format (e.g. "Bash(echo foo)") and other representations. I need to know: what types of permission entries exist (bash, skill, read, mcp?), how are they parsed from Claude's allow-list strings, and how are they rendered back.
2. /home/user/git/luckydonald/base/scripts/°base/ai/settings/°settings_lib/codex_rules.py — full contents (I already know this handles bash prefix_rule translation for codex .rules files)
3. /home/user/git/luckydonald/base/scripts/°base/ai/settings/°settings_lib/mcp_servers.py — full contents, this handles MCP server config syncing between Claude and Codex config formats (claude mcp json vs codex toml)
4. /home/user/git/luckydonald/base/scripts/°base/ai/settings/°settings_lib/hooks.py and cli.py — briefly, to see how permissions.allow/deny lists flow through the sync pipeline (render_claude, render_codex functions, what shared.permissions looks like)
5. Look for any existing handling of MCP tool permission strings — search for "mcp__" pattern anywhere in the scripts/°base directory (grep -rn "mcp__" scripts/°base/) and also search for "mcp__" or similar patterns in any .claude/settings*.json files in the repo root (grep -rn "mcp__" --include=*.json .)
6. Search codex documentation references in the repo (e.g. ai/references/*.md, or files mentioning "codex" and "toml" and "permission" or "rules") for anything describing how Codex codex.toml or config represents MCP tool call permissions (codex uses `mcp_servers` config plus maybe `tools` allow lists, or "approval_policy", or per-tool trust settings). Search: grep -rln "mcp" ai/references/ 2>/dev/null, and check if there's a downloaded codex docs/readme file (e.g. ai/references/*codex* or similar).
7. Check .codex/config.toml or ai/tool-settings/*.json in the repo (if present) for any existing example of an MCP tool permission entry, and check .claude/settings.json / settings.local.json for existing "mcp__xxx__yyy" style allow entries as a real-world example format.

Report back:
- The exact data model/schema used for permission entries in this codebase (dict shape for "type": "bash" vs "skill" vs others)
- Whether there's already partial/stub support for MCP tool permissions in commands.py or codex_rules.py
- What Codex's actual mechanism is for allowing a specific MCP tool call (if you find docs/refs) — e.g., is it via .codex/rules prefix_rule, via config.toml under [mcp_servers.NAME] with some tool allowlist, or via a different mechanism entirely
- Relevant file paths and line numbers for everything found

Be thorough — this is research only, do not edit anything.