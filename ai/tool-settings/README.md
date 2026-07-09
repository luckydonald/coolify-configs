# AI Tool Settings

`ai/tool-settings/settings.json` is the tracked, human-editable settings file shared by Claude and Codex.
`scripts/°base/ai/settings/sync.py` reads it, preserves non-native metadata there, and renders the native files
under `.claude/` and `.codex/`.

Core keys:

- `version`: settings format version
- `hooks`: hook definitions shared across tools
- `permissions`: allow/deny command lists for Claude-style configuration

Downloader settings:

- `download_link.ide`: default IDE command used by `scripts/°base/ai/references/download-link.py`

Recommended values for `download_link.ide` are executable names that accept a file path as their final argument,
such as `pycharm`, `rustrover`, `codium`, or `code`.

`settings.local.json` is the ignored machine-local overlay for per-machine overrides. Keep shared defaults in
`settings.json` and machine-specific preferences in `settings.local.json`.
