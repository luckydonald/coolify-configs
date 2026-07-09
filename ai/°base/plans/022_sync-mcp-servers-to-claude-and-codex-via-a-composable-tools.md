# Sync MCP servers to Claude and Codex, via a composable "tools" prefix schema

## Context

`scripts/°base/ai/settings/sync.py` (via `°settings_lib/`) already syncs hooks, structured command
permissions, skills, and Codex plugin enablement between a neutral `ai/tool-settings/settings.json`
and the native Claude (`.claude/settings.json`) / Codex (`.codex/hooks.json`, `.codex/rules/generated.rules`,
`.codex/config.toml`) formats. MCP servers are the next native concept neither client shares yet:

- Claude: project-scoped MCP servers live in `.mcp.json` at the repo root
  (`{"mcpServers": {"<name>": {command/args/env or type/url/headers}}}`), with approval controlled by
  `enabledMcpjsonServers`/`disabledMcpjsonServers` in `.claude/settings.json` (confirmed against
  `code.claude.com/docs/en/mcp`).
- Codex: MCP servers live in `[mcp_servers.<name>]` tables in `config.toml` — project-local
  `.codex/config.toml` is allowed per `ai/references/.../codex/mcp.md` — with `command`/`args`/`env`/`env_vars`
  for stdio, `url`/`bearer_token_env_var`/`http_headers` for HTTP, plus a native `enabled` flag.

The first concrete server to wire up is `bugsink-mcp` (`ai/references/.../7c/bugsink-mcp/.../README.md`),
which needs `BUGSINK_URL`/`BUGSINK_TOKEN` supplied without embedding them in tracked config. Rather than
hardcoding an `envmcp`-wrapped command into every server entry, the user wants a **composable "tools"
mechanism**: named, reusable command-prefix snippets (`envmcp` reading `ai/.env`, or `mcpipe --debug`, etc.)
that any server can opt into by name, with sync doing the concatenation. This decouples "how do I get
secrets into this process" from "what MCP server am I running" and makes swapping/adding wrapper tools a
one-place edit instead of a per-server one.

Confirmed via `envmcp`'s actual source (`src/index.ts`): `loadEnvironmentVariablesFromFile` sets each
loaded var directly on `process.env`, and the wrapped command is spawned with `env: process.env` (full
inheritance) — so `npx envmcp --env-file ai/.env -- <command> <args...>` makes every variable in
`ai/.env` a real environment variable for the wrapped process, for **both** Claude and Codex, with no
client-specific `env`/`env_vars`/`bearer_token_env_var` wiring needed.

`ai/.env` is already covered by the existing `**/*.env`/`**/*.env*` gitignore rules (verified), so no
real secrets are ever committed; a new `ai/.env.example` (tracked, one new gitignore exception mirroring
the existing `!/.env.example` root exception) documents the required keys.

## 1. Neutral `mcp` schema in `ai/tool-settings/settings.json`

New top-level key, sibling to `hooks`/`permissions`/`enabledPlugins`, per the user's design:

```jsonc
"mcp": {
  "tools": {
    ".env": {
      "": {
        "mode": "prefix",
        "cmd": ["npx", "-y", "envmcp", "--env-file", "ai/.env"]
      },
      "repo-root": {
        "mode": "prefix",
        "cmd": ["npx", "-y", "envmcp", "--env-file", "$(git rev-parse --show-toplevel)/.env"]
      },
      "debug": {
        "mode": "prefix",
        "cmd": ["npx", "-y", "mcpipe", "--debug", "--env-file", "ai/.env"]
      }
    }
  },
  "servers": {
    "bugsink": {
      "enabled": true,
      "type": "stdio",
      "tools": [".env"],
      "cmd": ["npx", "-y", "bugsink-mcp"]
    }
  }
}
```

(Note: the user's own `.env`/`""` example cmd omitted the literal `envmcp` package name — fixed above to
match envmcp's actual invocation syntax from its README.)

- `tools.<name>.<variant>` — `""` is the default variant (`.env` alone, or `.env@`, or `.env@default` all
  resolve to it). Only `"mode": "prefix"` is supported for now (schema allows for future modes but sync
  only implements `prefix`; an unrecognized mode is treated as an unresolvable reference, same handling
  as a missing tool/variant — see below).
- `servers.<name>.tools` — ordered list of `"tool"` or `"tool@variant"` references. Resolved
  left-to-right: the final command argv is `tools[0].cmd + tools[1].cmd + ... + server.cmd`, i.e. the
  leftmost tool is the outermost/first-spawned process (matches how `npx envmcp -- npx bugsink-mcp` reads
  left to right).
- `servers.<name>.cmd` — the server's own bare command as one array (sync splits it into
  `command`/`args` for both clients — no need to hand-split).
- `$(git rev-parse --show-toplevel)` **is resolved by the sync script itself**, not by a shell — MCP
  servers are spawned via a plain argv array (no shell) by both Claude and Codex, so a literal
  `$(...)` token would never be substituted at runtime. Since `°settings_lib.paths._git_root()` already
  computes this, `mcp_servers.py`'s renderer does a literal substring replace of that exact token in every
  resolved argv element with the absolute repo root, at generation time. This makes the `repo-root`
  variant in the example actually work (resolving the user's stated uncertainty), rather than remaining
  unused.
- `servers.<name>.enabled` (default `true`), `type` (`"stdio"` | `"http"`). HTTP servers skip `tools`/`cmd`
  resolution entirely and use `url`/`headers` directly (kept minimal — no OAuth/bearer wiring, which is
  genuinely client-specific and out of scope).

## 2. JSON Schema

New file `ai/tool-settings/mcp.schema.json` (draft 2020-12), validating the `mcp` key's shape:

- `tools`: object; each value is an object keyed by variant name (`""` allowed) whose value requires
  `mode` (currently only `"prefix"` via `enum`/`const`, written so adding modes later doesn't require a
  schema rewrite — use `enum: ["prefix"]` rather than `const` for that headroom) and `cmd` (non-empty
  array of strings).
- `servers`: object; each value requires `type` (`enum: ["stdio", "http"]`), optional `enabled` (boolean,
  default `true`). `allOf`/`if`-`then` branches: `type == "stdio"` requires `cmd` (array of strings) and
  allows `tools` (array of strings matching `^[^@]+(@.*)?$`); `type == "http"` requires `url` and allows
  `headers` (object of string→string).
- Top-level `additionalProperties: false` at each level to catch typos early.
- Reference it from `ai/tool-settings/settings.json` via a `"$schema"` comment-adjacent note in
  `AGENTS.md` (the settings file itself has no schema pointer today for its other sections either, so no
  new precedent needed there — just document the schema's existence and path).

## 3. New module `°settings_lib/mcp_servers.py`

- `_git_root_token_sub(value, root)` — literal `str.replace("$(git rev-parse --show-toplevel)", str(root))`
  helper, applied to every resolved argv string.
- `_resolve_tool_ref(tools, ref) -> list[str]` — splits `"name@variant"` (default variant `""` when no
  `@` or empty suffix), looks up `tools[name][variant]["cmd"]`; returns `None` if the tool, variant, or
  `mode != "prefix"` doesn't exist (caller treats this as "skip server, note why" rather than crashing
  the whole sync — mirrors the existing `_bash_pattern_to_prefix` "untranslatable → skip with a count"
  pattern).
- `_resolve_server_argv(mcp_shared, name, server) -> list[str] | None` — for `type == "stdio"`: walks
  `server.get("tools", [])`, concatenates each resolved prefix's `cmd` then the server's own `cmd`,
  substitutes the repo-root token on every element, returns the final argv (`None` + skip-reason if any
  tool reference fails to resolve).
- `render_claude_mcp(shared, git_root) -> dict` — builds `.mcp.json` content: for each `servers` entry,
  stdio → `{"type": "stdio", "command": argv[0], "args": argv[1:]}`; http → `{"type": "http", "url": ..., "headers": ...}`.
  Always rendered (even `{"mcpServers": {}}` when none configured), matching how `.codex/hooks.json` is
  always written — no special-casing for "empty". Servers whose tool references fail to resolve are
  omitted with a summary skip count (returned alongside, or logged by the caller — mirror
  `render_codex_rules`'s trailing skip-comment convention, adapted to a return value since `.mcp.json` is
  JSON, not commentable — e.g. return `(dict, skipped_names)` and have `cli.py` print skipped names the
  same way it prints "Wrote:"/"Would write:" lines today).
- `parse_claude_mcp(mcp_json_data) -> dict[str, dict]` — reads `.mcp.json`'s `mcpServers`, returns flat
  neutral entries (`type`, `cmd` reconstructed as `[command] + args` for stdio, or `url`/`headers` for
  http), `enabled` defaulted to `True` (approval state isn't stored in `.mcp.json` — see §5). These
  parsed-back entries have **no `tools` field** — a hand-added Claude server round-trips as a flat `cmd`,
  which is correct: only the authored source uses the tools abstraction for convenience, hand edits fold
  back as literal commands (same philosophy as Codex `.rules` hand edits folding back as flat bash
  commands, not reconstructed prefix-rule ASTs).
- `render_codex_mcp_block(shared, git_root) -> tuple[str, list[str]]` — same resolution, rendered as
  `[mcp_servers.<name>]` TOML tables (stdio: `command`/`args`; http: `url`/`http_headers`), plus the
  native `enabled = true/false` field, wrapped in a dedicated marker pair:
  ```
  # --- BEGIN generated mcp_servers (scripts/°base/ai/settings/sync.py) ---
  [mcp_servers.bugsink]
  ...
  # --- END generated mcp_servers ---
  ```
  Unlike the single-line `enabled=` patch used for plugins (`codex_toml._find_table_bounds` +
  `_ENABLED_ASSIGNMENT`), MCP server tables have multiple fields including arrays, which aren't safe to
  patch line-by-line. Treating the whole region as an owned, fully-regenerated block (same idea as
  `.codex/rules/generated.rules` being regenerated wholesale, just scoped to a marked region inside
  `config.toml`) sidesteps writing a partial TOML editor. Returns skipped names too, same as above.
- `insert_or_replace_block(text, begin_marker, end_marker, body) -> str` — small generic helper: replaces
  everything between the markers if present (keeping the markers), else appends the marked block at the
  end. Used in `cli.py` alongside the existing `codex_toml.render_codex_plugins` call on the same file.
- `parse_codex_mcp_toml(text) -> dict[str, dict]` — `tomllib.loads(text)`, reads `data.get("mcp_servers", {})`
  regardless of the marker region (so a hand-added server outside the generated block still parses),
  converts each table to a flat neutral entry (`cmd` reconstructed from `command`+`args`, or `url`/`headers`;
  `enabled` read directly, default `True`).

## 4. `paths.py`

Add `CLAUDE_MCP = Path(".mcp.json")`. Reuse the existing `CODEX_PROJECT_CONFIG` for MCP (same file
already used for plugins). No local-scoped MCP file — `.mcp.json` is meant to be checked in and shared
(matches how plugin sync is tracked-only, no local variant).

## 5. `hooks.py` — fold `mcp` into the generic merge, and derive Claude approval lists

- Add `"mcp"` to `_CORE_SHARED_KEYS`.
- `_normalize_native`: default `"mcp": {"tools": deepcopy(data.get("mcp", {}).get("tools") or {}), "servers": deepcopy(data.get("mcp", {}).get("servers") or {})}`.
- `_merge`: shallow whole-entry-wins-by-name merge at both the `tools` and `servers` sub-keys (same
  pattern already used for `enabledPlugins`'s flat dict `.update()`) — native sources never contribute new
  `tools` (they don't have the concept), only `servers`.
- `render_claude(shared)`: after the existing `enabledPlugins` block, add — if `shared.get("mcp", {}).get("servers")`,
  compute `enabledMcpjsonServers` (names where `entry.get("enabled", True)`) and `disabledMcpjsonServers`
  (names explicitly `False`), set on `data` (only emit non-empty lists). This is **one-directional**
  (shared → Claude settings only): approval state lives with the config that defines the server, and
  round-tripping a user's local `/mcp` approval clicks back into a version-controlled shared file would
  be surprising — same reasoning as why Claude's local MCP scope (`~/.claude.json`) is untouched here.

## 6. `cli.py` wiring

- `_load_layer` gains `claude_mcp_path: Path | None = None`:
  - If it exists, parse via `mcp_servers.parse_claude_mcp`, append as a native source
    `{"mcp": {"servers": ...}}` using that file's mtime.
  - `codex_config_path` handling (already present for plugins) additionally parses
    `mcp_servers.parse_codex_mcp_toml(text)` and merges `{"mcp": {"servers": ...}}` into the same
    native-source entry alongside `{"enabledPlugins": ...}` (same file/mtime, different top-level keys).
- `_apply_or_check` gains `claude_mcp_path`:
  - Always render+write `.mcp.json` via `mcp_servers.render_claude_mcp(shared, paths._git_root())` /
    `_write_json`/`_same_json`.
  - After the existing `render_codex_plugins` call on `codex_config_path`'s current text, additionally
    apply `mcp_servers.insert_or_replace_block(text, BEGIN, END, mcp_block_text)` before writing via
    `_write_text_if_changed` — one write per file, both plugins and mcp_servers content folded into the
    same final text.
  - Print any skipped-server names (unresolvable tool references) the same way other changed/skip
    summaries are printed today.
- `main()`: wire `paths.CLAUDE_MCP` into the tracked-group call only (no local-group MCP sync, consistent
  with plugins).

## 7. The `bugsink` entry + `ai/.env`

- Add the `tools[".env"]`/`servers["bugsink"]` entries from §1 to `ai/tool-settings/settings.json`'s new
  `mcp` key.
- Create `ai/.env.example` (tracked):
  ```
  # Bugsink MCP server credentials — see ai/references/https/github.com/7c/bugsink-mcp/.../README.md
  BUGSINK_URL=https://your-bugsink-instance.com
  BUGSINK_TOKEN=your-api-token
  ```
- Add `!ai/.env.example` to `.gitignore` near the existing `!/.env.example` exception (~line 754), so the
  example file survives the blanket `**/*.env*` rule while `ai/.env` itself stays ignored.
- Do **not** create a real `ai/.env` with credentials — the user populates it themselves.

## 8. Docs (`ai/°base/AGENTS.md`)

Extend the directory-layout table and "Settings + skills sync" section: document `.mcp.json` (generated,
bidirectional for server definitions, one-directional for approval), the `mcp.tools`/`mcp.servers`
neutral schema and its JSON Schema at `ai/tool-settings/mcp.schema.json`, the `[mcp_servers.*]` marked
block in `.codex/config.toml`, the `$(git rev-parse --show-toplevel)` sync-time substitution, and the
`envmcp` + `ai/.env` convention as the reference "tool" for supplying MCP server secrets.

## Tests

Extend `scripts/°base/tests/test_ai_settings_sync.py` (import `mcp_servers` the same way as the other
submodules):

- Tool-resolution: `_resolve_tool_ref`/`_resolve_server_argv` — default variant, named variant,
  multi-tool ordering, missing tool/variant → `None` + skip, repo-root token substitution.
- `render_claude_mcp`/`parse_claude_mcp` round-trip for stdio and http entries; `enabled`/`tools` stripped
  from `.mcp.json` output (only resolved `command`/`args` remain).
- `render_codex_mcp_block`/`parse_codex_mcp_toml` round-trip; `insert_or_replace_block` insert-into-file
  and replace-existing-region-without-touching-outside-content cases (mirrors
  `test_render_codex_plugins_preserves_unrelated_content`); a combined test where `[plugins.*]` and
  `[mcp_servers.*]` coexist in one `config.toml` and both survive a sync run.
- `hooks._merge`/`_normalize_native`: `mcp.tools`/`mcp.servers` participate in merge like
  `enabledPlugins`; `render_claude` emits `enabledMcpjsonServers`/`disabledMcpjsonServers` correctly.
- `cli._load_layer`: a fixture with a hand-added server in `.mcp.json` merges in as a flat `cmd` entry
  with no `tools`; a fixture where `.codex/config.toml`'s `[mcp_servers.*]` table changes `enabled = false`
  merges back too.
- `cli._apply_or_check`/`main`: writing `.mcp.json` and the `config.toml` MCP block is idempotent on a
  second run.
- Schema: validate the `bugsink` example (and a couple of malformed shapes) against
  `ai/tool-settings/mcp.schema.json` using `jsonschema` if already a dependency, otherwise a minimal
  structural check written by hand (confirm which is available before deciding).

## Verification

1. `uv run --project scripts/°base python -m unittest discover scripts/°base/tests -v` (full suite).
2. Run `./scripts/°base/ai/settings/sync.py` for real and inspect the diff:
   - `.mcp.json` created with `bugsink`: `command: "npx"`, `args: ["-y", "envmcp", "--env-file", "ai/.env", "npx", "-y", "bugsink-mcp"]`.
   - `.claude/settings.json` gains `"enabledMcpjsonServers": ["bugsink"]`.
   - `.codex/config.toml` gains a `[mcp_servers.bugsink]` table inside the new marker block, existing
     `[plugins.*]` content untouched.
3. `./scripts/°base/ai/settings/sync.py --check` again — reports already in sync.
4. Hand-edit `.mcp.json` to add a second throwaway stdio server, rerun sync, confirm it appears as a new
   flat object (no `tools` field) in `ai/tool-settings/settings.json`'s `mcp.servers`.
5. Confirm `ai/.env.example` is trackable (`git status`/`git add --dry-run`) and a scratch `ai/.env`
   remains ignored (`git check-ignore -v ai/.env`).
