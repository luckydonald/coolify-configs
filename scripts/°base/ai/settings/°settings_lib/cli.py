"""Synchronize Claude and Codex project settings through a neutral JSON file.

The neutral files under ``ai/tool-settings/`` are meant to be readable and
editable by humans. Native files remain editable too: this script imports
entries from both sides, unions them by stable identity, then renders the
native formats back out.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from . import codex_rules, codex_toml, commands, mcp_servers, paths
from .hooks import CURRENT_VERSION, _shared_extras, _merge, _normalize_native, render_claude, render_codex_hooks, render_copilot_hooks
from .json_io import _read_json, _same_json, _write_json, _write_text_if_changed
from .skills import _sync_skills


def _reorder_like(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Re-key `new` to follow `existing`'s top-level key order, appending any
    keys `existing` doesn't have (in `new`'s order) at the end. `render_claude`/
    `render_codex_hooks` always build their result in a fixed construction
    order, so without this, a single unrelated content change (e.g. one
    permission entry) would reorder every top-level key in the native file
    and turn a one-line diff into a whole-file rewrite."""
    ordered = {key: new[key] for key in existing if key in new}
    for key, value in new.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _bash_prefix_key(command: str) -> tuple[str, ...] | None:
    prefix = commands._bash_pattern_to_prefix(command)
    return tuple(prefix) if prefix is not None else None


def _merge_codex_rules_additions(shared: dict[str, Any], rules_text: str) -> dict[str, Any]:
    """Merge genuinely new bash entries found in a (possibly hand-edited)
    `.rules` file into `shared`. Entries are compared by their Codex prefix
    tuple rather than their raw command string, because `render_codex_rules`
    normalizes every command into a `prefix + wildcard` rule — reparsing that
    same generated file would otherwise reintroduce e.g. "yarn" as the
    distinct-looking "yarn:*" every run and duplicate it forever."""
    parsed = codex_rules.parse_codex_rules(rules_text)
    if not parsed.get("allow") and not parsed.get("deny"):
        return shared

    shared = deepcopy(shared)
    permissions = shared.setdefault("permissions", {"allow": [], "deny": []})
    for bucket in ("allow", "deny"):
        existing = list(permissions.get(bucket) or [])
        known_prefixes = {
            key
            for entry in existing
            if isinstance(entry, dict) and entry.get("type") == "bash"
            for key in [_bash_prefix_key(entry.get("command", ""))]
            if key is not None
        }
        additions = []
        for entry in parsed.get(bucket) or []:
            key = _bash_prefix_key(entry["command"])
            if key is not None and key in known_prefixes:
                continue
            additions.append(entry)
            if key is not None:
                known_prefixes.add(key)
        if additions:
            permissions[bucket] = existing + additions
    shared["permissions"] = permissions
    return shared


def _merge_mcp_tool_permission_additions(
    shared: dict[str, Any], parsed: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Merge genuinely new MCP tool permission entries parsed back from a
    hand-edited `.codex/config.toml` (`disabled_tools` / per-tool
    `approval_mode = "auto"`) into `shared`, deduped by (server, tool)."""
    if not parsed.get("allow") and not parsed.get("deny"):
        return shared

    shared = deepcopy(shared)
    permissions = shared.setdefault("permissions", {"allow": [], "deny": []})
    for bucket in ("allow", "deny"):
        existing = list(permissions.get(bucket) or [])
        known = {
            (entry.get("server"), entry.get("tool"))
            for entry in existing
            if isinstance(entry, dict) and entry.get("type") == "mcp"
        }
        additions = []
        for entry in parsed.get(bucket) or []:
            key = (entry["server"], entry["tool"])
            if key in known:
                continue
            additions.append(entry)
            known.add(key)
        if additions:
            permissions[bucket] = existing + additions
    shared["permissions"] = permissions
    return shared


def _merge_mcp_native_additions(
    shared: dict[str, Any], native_servers: dict[str, dict[str, Any]], git_root: Path
) -> dict[str, Any]:
    """Merge genuinely new/changed MCP server entries parsed back from native
    files (`.mcp.json`, `.codex/config.toml`) into `shared`. Entries are
    compared by their *resolved* argv/url, not raw equality, because
    `render_claude_mcp`/`render_codex_mcp_block` always emit a fully-resolved
    flat command — reparsing our own generated output would otherwise look
    "different" from the authored `tools`-based entry (which has no `cmd`
    matching the resolved form literally) and clobber it every run.

    Native formats have no `tools` concept — a parsed-back entry only ever
    carries a flat `cmd`. To avoid permanently flattening an authored
    `tools`-based entry on every round-trip: when only `enabled` changed,
    just update that field on the existing entry in place; when the resolved
    command genuinely changed (or the server is brand new), re-run
    `mcp_servers.extract_tools_from_cmd` against the known `mcp.tools`
    snippets to reconstruct a `tools`/`cmd` split rather than storing the raw
    flat `cmd` forever. `.mcp.json` also carries no real `enabled` value
    (`parse_claude_mcp` omits the key), so `new_enabled` falls back to the
    existing value rather than a hardcoded default."""
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
            if entry.get("type") == "stdio":
                servers[name] = mcp_servers._reconstruct_stdio_entry(mcp, entry.get("cmd") or [], new_enabled, git_root)
            else:
                entry = deepcopy(entry)
                entry["enabled"] = new_enabled
                servers[name] = entry
            continue

        existing_enabled = existing.get("enabled", True)
        new_enabled = entry.get("enabled", existing_enabled)

        if is_http:
            existing_sig = (
                existing.get("type"),
                existing.get("url"),
                tuple(sorted((existing.get("headers") or {}).items())),
            )
            new_sig = (
                entry.get("type"),
                entry.get("url"),
                tuple(sorted((entry.get("headers") or {}).items())),
            )
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


def _load_layer(
    shared_path: Path,
    claude_path: Path,
    codex_path: Path,
    codex_rules_path: Path | None = None,
    codex_config_path: Path | None = None,
    claude_mcp_path: Path | None = None,
    copilot_path: Path | None = None,
) -> dict[str, Any]:
    shared_source = _read_json(shared_path)
    shared = deepcopy(shared_source)
    if shared:
        shared = _merge({}, shared)

    native_sources: list[tuple[float, dict[str, Any]]] = []
    mcp_native_sources: list[tuple[float, dict[str, Any]]] = []
    native_paths = [claude_path, codex_path]
    if copilot_path is not None:
        native_paths.append(copilot_path)
    for path in native_paths:
        if path.is_file():
            native_sources.append((path.stat().st_mtime, _read_json(path)))
    codex_config_text: str | None = None
    if codex_config_path is not None and codex_config_path.is_file():
        codex_config_text = codex_config_path.read_text(encoding="utf-8")
        plugins = codex_toml.parse_codex_plugins(codex_config_text)
        if plugins:
            native_sources.append((codex_config_path.stat().st_mtime, {"enabledPlugins": plugins}))
        mcp_from_codex = mcp_servers.parse_codex_mcp_toml(codex_config_text)
        if mcp_from_codex:
            mcp_native_sources.append((codex_config_path.stat().st_mtime, mcp_from_codex))
    if claude_mcp_path is not None and claude_mcp_path.is_file():
        mcp_from_claude = mcp_servers.parse_claude_mcp(_read_json(claude_mcp_path))
        if mcp_from_claude:
            mcp_native_sources.append((claude_mcp_path.stat().st_mtime, mcp_from_claude))

    if not shared and native_sources:
        shared = _normalize_native(sorted(native_sources, key=lambda item: item[0])[-1][1])
    for _, data in sorted(native_sources, key=lambda item: item[0]):
        shared = _merge(shared, data)
    shared = shared or {"version": CURRENT_VERSION, "hooks": {}, "permissions": {"allow": [], "deny": []}}

    if codex_rules_path is not None and codex_rules_path.is_file():
        shared = _merge_codex_rules_additions(shared, codex_rules_path.read_text(encoding="utf-8"))

    if codex_config_text is not None:
        parsed_tool_permissions = mcp_servers.parse_codex_mcp_tool_permissions(codex_config_text)
        shared = _merge_mcp_tool_permission_additions(shared, parsed_tool_permissions)

    combined_mcp_native: dict[str, Any] = {}
    for _, data in sorted(mcp_native_sources, key=lambda item: item[0]):
        for name, entry in data.items():
            merged_entry = dict(combined_mcp_native.get(name) or {})
            merged_entry.update(entry)
            combined_mcp_native[name] = merged_entry
    if combined_mcp_native:
        shared = _merge_mcp_native_additions(shared, combined_mcp_native, paths._git_root())

    shared.update(_shared_extras(shared_source))
    return shared


def _apply_or_check(
    shared_path: Path,
    claude_path: Path,
    codex_path: Path,
    apply: bool,
    codex_rules_path: Path | None = None,
    codex_config_path: Path | None = None,
    claude_mcp_path: Path | None = None,
    backup_timestamp: str | None = None,
    copilot_path: Path | None = None,
) -> list[str]:
    changed: list[str] = []
    shared = _load_layer(shared_path, claude_path, codex_path, codex_rules_path, codex_config_path, claude_mcp_path, copilot_path)
    claude = render_claude(shared)
    codex = render_codex_hooks(shared)
    git_root = paths._git_root()

    render_targets = [
        (shared_path, shared),
        (claude_path, claude),
        (codex_path, codex),
    ]
    if copilot_path is not None:
        render_targets.append((copilot_path, render_copilot_hooks(shared)))

    for path, data in render_targets:
        existing = _read_json(path)
        if existing:
            data = _reorder_like(existing, data)
        if _same_json(path, data):
            continue
        changed.append(str(path))
        if apply:
            _write_json(path, data, backup_timestamp)

    if codex_rules_path is not None:
        rules_text = codex_rules.render_codex_rules(shared)
        changed.extend(_write_text_if_changed(codex_rules_path, rules_text, apply, backup_timestamp))

    if codex_config_path is not None:
        current_text = codex_config_path.read_text(encoding="utf-8") if codex_config_path.is_file() else ""
        plugins_flat = {
            plugin_id: bool((entry or {}).get("enabled", True))
            for plugin_id, entry in (shared.get("plugins") or {}).items()
        }
        plugins_text = codex_toml.render_codex_plugins(current_text, plugins_flat)
        mcp_block, codex_skipped = mcp_servers.render_codex_mcp_block(shared, git_root)
        for name in codex_skipped:
            print(f"Skipped MCP server '{name}' for Codex: unresolvable tool reference.")
        final_text = mcp_servers.insert_or_replace_block(
            plugins_text, mcp_servers.MCP_TOML_BEGIN_MARKER, mcp_servers.MCP_TOML_END_MARKER, mcp_block
        )
        changed.extend(_write_text_if_changed(codex_config_path, final_text, apply, backup_timestamp))

    if claude_mcp_path is not None:
        mcp_data, claude_skipped = mcp_servers.render_claude_mcp(shared, git_root)
        for name in claude_skipped:
            print(f"Skipped MCP server '{name}' for Claude: unresolvable tool reference.")
        if not _same_json(claude_mcp_path, mcp_data):
            changed.append(str(claude_mcp_path))
            if apply:
                _write_json(claude_mcp_path, mcp_data, backup_timestamp)

    return changed


def _stage(paths_to_add: list[str]) -> None:
    if not paths_to_add:
        return
    subprocess.run(["git", "add", "--", *paths_to_add], check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize Claude and Codex AI tool settings.")
    parser.add_argument("--dry-run", action="store_true", help="show what would change without writing files")
    parser.add_argument("--check", action="store_true", help="like --dry-run but exit 1 if files are out of sync (used by pre-commit)")
    args = parser.parse_args(argv)

    os.chdir(paths._git_root())

    # A merge commit can leave the tracked settings genuinely out of sync (a
    # normal thing to fix), but it can also just surface pre-existing drift in
    # the gitignored `.local` layer that has nothing to do with the merge. Since
    # merge commits are usually not an interactive moment to stop and fix
    # things by hand, auto-sync instead of blocking, and back up whatever gets
    # overwritten so nothing is silently lost.
    merge_auto_sync = args.check and not args.dry_run and paths._is_merge_in_progress()

    dry = args.dry_run or (args.check and not merge_auto_sync)
    apply = not dry
    backup_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") if merge_auto_sync else None
    if args.dry_run:
        print("Dry run — no files will be written.")

    config_status = codex_toml._migrate_codex_feature_flag(paths.CODEX_CONFIG, apply, sys.stdin.isatty())

    changed_main = _apply_or_check(
        paths.TRACKED_SHARED,
        paths.CLAUDE_SETTINGS,
        paths.CODEX_HOOKS,
        apply,
        paths.CODEX_RULES,
        paths.CODEX_PROJECT_CONFIG,
        paths.CLAUDE_MCP,
        backup_timestamp,
        paths.COPILOT_HOOKS,
    )
    changed_local: list[str] = []
    if (
        paths.LOCAL_SHARED.is_file()
        or paths.CLAUDE_LOCAL.is_file()
        or paths.CODEX_LOCAL_HOOKS.is_file()
        or paths.COPILOT_LOCAL_HOOKS.is_file()
    ):
        changed_local = _apply_or_check(
            paths.LOCAL_SHARED,
            paths.CLAUDE_LOCAL,
            paths.CODEX_LOCAL_HOOKS,
            apply,
            paths.CODEX_RULES_LOCAL,
            None,
            None,
            backup_timestamp,
            paths.COPILOT_LOCAL_HOOKS,
        )
    changed_skills = _sync_skills(apply)
    changed = changed_main + changed_local + changed_skills

    if merge_auto_sync:
        if changed:
            print("Merge in progress — auto-synced AI tool settings:")
            for path in changed:
                print(f"  Wrote: {path}")
            if backup_timestamp:
                print(f"Backups of overwritten files saved alongside originals (*.bak.{backup_timestamp}.*).")
            _stage(changed_main + changed_skills)
        else:
            print("All files are already in sync.")
        return config_status

    if changed:
        if args.check:
            print("AI tool settings are out of sync:")
            for path in changed:
                print(f"  {path}")
            print("Run `./scripts/°base/ai/settings/sync.py` to fix.")
            return 1
        verb = "Would write" if dry else "Wrote"
        for path in changed:
            print(f"{verb}: {path}")
    else:
        print("All files are already in sync.")

    return config_status


if __name__ == "__main__":
    raise SystemExit(main())
