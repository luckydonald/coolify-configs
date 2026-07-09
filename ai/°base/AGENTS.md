# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A reusable git base that consuming projects adopt via checkout, rebase, or merge from `base/base`. Its files — scripts, hooks, AI settings — live in consuming repos like normal files. Everything base-specific is namespaced under `°base` so it never collides with consuming-repo code.

## Commands

Run all tests (from repo root):
```bash
uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v
```

Run a single test module:
```bash
uv run --project scripts/°base python -m unittest ai.scripts.tests.test_<name> -v
```

Sync AI tool settings (Claude ↔ Codex) and skills:
```bash
python3 scripts/°base/ai/settings/sync.py
```

Check sync without writing (used by pre-commit):
```bash
python3 scripts/°base/ai/settings/sync.py --check
```

## Architecture

### Directory layout

| Path | Purpose |
|---|---|
| `scripts/°base/` | All helpers; `°base` namespace keeps them from colliding |
| `ai/°base/` | AI artifacts for the base repo itself (queries, plans, memory) |
| `ai/skills/*/SKILL.md` | Canonical skill source; `sync.py` distributes wrappers |
| `ai/tool-settings/settings.json` | Tracked, tool-neutral settings (Claude + Codex share this) |
| `ai/tool-settings/settings.local.json` | Machine-local settings overlay (gitignored) |
| `.claude/settings.json` | Generated from `ai/tool-settings/`; do not edit directly |
| `.claude/hooks/permission-check.py` | PermissionRequest hook — enforces git commit policy |
| `.codex/rules/generated.rules` | Generated Codex command allowlist (Starlark `prefix_rule`s); do not edit — hand edits are picked up and folded back on the next sync, but get overwritten in the process |
| `.codex/config.toml` | Generated project-local Codex config — `[plugins."<id>"].enabled` plus a marked `[mcp_servers.*]` block (see below); other content is preserved if you add it |
| `.mcp.json` | Generated project-scoped Claude MCP server config; do not edit directly — hand-added servers are picked up and folded back into `ai/tool-settings/settings.json` on the next sync (reconstructed into `tools`/`cmd` where the command matches a known tool snippet, otherwise stored as a flat `cmd`) |
| `ai/tool-settings/mcp.schema.json` | JSON Schema for the `mcp` key below |
| `ai/.env` | Gitignored, project-local secrets (e.g. for MCP servers); see `ai/.env.example` |
| `scripts/°base/ai/settings/°settings_lib/` | The actual sync implementation; `sync.py` is a thin shim over it (mirrors `ai/references/°dllink_lib/`) |

### Settings + skills sync (`scripts/°base/ai/settings/sync.py`)

The single source of truth is `ai/tool-settings/settings.json`. Running `sync.py` renders it into `.claude/settings.json` (Claude), `.codex/hooks.json` (Codex hooks), `.codex/rules/generated.rules` (Codex command allowlist), and `.codex/config.toml` (Codex plugin enable/disable), adjusting async flags and tool-name arguments between the two formats. A pre-commit hook runs `sync.py --check` to block commits when the files drift.

`ai/tool-settings/settings.json` carries a `version` field (`hooks.CURRENT_VERSION`, currently `2`); `sync.py` always writes the current version but still reads older files — v1 (raw Claude permission strings, flat `enabledPlugins: {id: bool}`) upgrades transparently on load, since parsing permission entries and plugin entries is permissive regardless of the source version.

`sync.py` writes `ai/tool-settings/settings.json`/`settings.local.json` with a custom pretty-printer, not plain `json.dumps`: a dict's `"enabled"` key (if present) always renders first, at any depth; `permissions.allow`/`permissions.deny` array elements render one compact single-line object per line; and any `"cmd"` array (only ever `mcp.tools.*.*.cmd` / `mcp.servers.*.cmd`) renders as a single line. Everything else stays normal one-key/one-element-per-line `indent=2` formatting, for readable diffs.

`permissions.allow`/`permissions.deny` entries use a structured schema, not raw Claude strings: `{"type": "bash", "command": "git status:*"}`, `{"type": "read", "path": "**/.env*"}`, `{"type": "skill", "name": "..."}` (unrecognized tools fall back to a `pattern` field; unparseable legacy strings become `{"type": "raw", "value": "..."}`). This is what lets `sync.py` render the same entry into both Claude's `Tool(content)` syntax and Codex's `prefix_rule(pattern=[...], decision=...)` syntax. Only `bash` entries reach Codex — Codex `.rules` files govern command execution only, not file reads or skill invocation. Commands that can't be reduced to a static argv prefix (shell substitution, redirection, `&&`/`||`/`;`/`|`, env-var-assignment prefixes) stay Claude-only.

Codex's `.rules` file and `.codex/config.toml` plugin blocks are genuinely bidirectional: `sync.py` parses them back (Starlark via `ast`, TOML via `tomllib`) and folds new entries into `ai/tool-settings/settings.json`. Because a Codex prefix rule has no "exact match" concept, re-parsed bash commands are compared by their token-prefix (not their rendered string) before merging, so re-importing `sync.py`'s own generated output never duplicates entries.

Plugins live under the `plugins` key (`{"<id>": {"enabled": bool}}`, not the old flat `enabledPlugins: {id: bool}` — that shape is still read for backward compatibility but never written). `render_claude` flattens it back into Claude's native `enabledPlugins: {id: bool}`; `render_codex_plugins` (TOML-only, unaffected by the nesting) still takes a flat `dict[str, bool]`, so `sync.py` flattens `plugins` before calling it.

MCP servers are configured under the `mcp` key (schema: `ai/tool-settings/mcp.schema.json`), split into
reusable `mcp.tools.<name>.<variant>` command-prefix snippets (`{"mode": "prefix", "cmd": [...]}` — only
`prefix` is implemented) and `mcp.servers.<name>` definitions (`type: "stdio"|"http"`, `enabled`, and
either an ordered `tools` list of `"tool"`/`"tool@variant"` references and/or `cmd` (at least one of the
two is required for `stdio`), or `url`/`headers`).
`sync.py` resolves each server's final argv as `tools[0].cmd + tools[1].cmd + ... + cmd` (leftmost tool is
the outermost/first-spawned process) and substitutes any literal `$(git rev-parse --show-toplevel)` token
with the absolute repo root itself — this is **not** shell-expanded, since MCP servers are spawned as a
plain argv array by both clients. The built-in `.env` tool wraps a server with `envmcp` reading
`ai/.env` (gitignored; see `ai/.env.example`), so servers needing secrets (e.g. `bugsink`, which reads
`BUGSINK_URL`/`BUGSINK_TOKEN`) don't need per-client `env`/`env_vars` wiring — `envmcp` loads the file
into its own process env and spawns the wrapped server inheriting it, identically for Claude and Codex.
Resolved servers render into `.mcp.json` (Claude) and a marked `[mcp_servers.*]` block in
`.codex/config.toml` (Codex, `enabled` native, rendered first in the table for readable diffs); both are
parsed back on the next sync as flat entries (no `tools` field — native formats have no such concept).
Since a flat parsed-back entry would otherwise permanently flatten an authored `tools`-based server on
every round-trip, `sync.py` (`mcp_servers.extract_tools_from_cmd`) re-matches the flat `cmd` against known
`mcp.tools` snippets (greedy longest-prefix-first, chained left-to-right) and reconstructs a `tools`/`cmd`
split whenever a native entry is new or its resolved command genuinely changed; when only `enabled`
changed, that field is updated on the existing authored entry in place instead. `.mcp.json` has no
`enabled` concept at all (every server renders into it regardless of state), so a parsed-back Claude entry
never carries that key — the merge falls back to whatever `ai/tool-settings/settings.json` (or Codex's
`config.toml`, which does carry a real `enabled`) already knows, rather than assuming `True`.
`enabledMcpjsonServers`/`disabledMcpjsonServers` in `.claude/settings.json` are derived one-directionally
from each server's `enabled` flag and — once at least one server exists — both arrays are always written
(one may be empty), so toggling a server only changes an array's contents, not which keys are present.

Skills follow the same pattern: the canonical file is `ai/skills/<slug>/SKILL.md`. `sync.py` symlinks `.claude/skills/<slug>/SKILL.md` and `.agents/skills/<slug>/SKILL.md` to the canonical path (Linux/Mac only — this repo does not support Windows checkouts). Always edit the canonical file, then run `sync.py`. No `.claude/commands/<slug>.md` shim is generated: Claude Code's Terminal already lists skills directly in its `/` autocomplete (and Codex reads `.agents/skills` directly), so a command wrapper would just duplicate the skill.

### AI artifact routing (`scripts/°base/ai/hooks/_lib.py`)

Every Claude Code hook calls `resolve_log_path()`, which routes output based on whether it is running inside the base repo (`ai/°base/…`) or a consuming repo (`ai/…`). Detection: `_is_inside_base_repo()` — directory named `base` **and** origin URL matches `luckydonald/base`.

Hooks:

| Hook | Trigger | Output |
|---|---|---|
| `save-prompt/hook.py` | `UserPromptSubmit` | `ai/°base/query.md` |
| `save-decision/hook.py` | `AskUserQuestion` post | `ai/°base/query.md` |
| `save-plan/hook.py` | `Write`, `ExitPlanMode`, `Stop` | `ai/°base/plans/NNN_*.md` |
| `record-memory/hook.py` | `Write`, `Edit`, `Bash`, `SessionStart` | syncs memory hardlinks |

### Pre-commit hooks

- `reject_co_authored_by.py` — blocks `Co-Authored-By:` in commit messages (stage: commit-msg)
- `require_memory_delete_marker.py` — requires an explicit marker when memory files are deleted (stage: commit-msg)

### Commit format

```
[base] topic: ai: Run: Short summary.
```

`[base]` is the where-tag; `topic` is a component or subsystem name (not a Conventional Commit type like `feat` or `fix`).