from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path


_FEATURE_ASSIGNMENT = re.compile(
    r"^(\s*)(codex_hooks|hooks)\s*=\s*([^\n#]*?)(\s*(?:#.*)?)(\r?\n)?$"
)


def _features_bounds(lines: list[str]) -> tuple[int, int] | None:
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("[") or not stripped.endswith("]"):
            continue
        if stripped == "[features]":
            start = index + 1
            continue
        if start is not None:
            return start, index
    if start is None:
        return None
    return start, len(lines)


def _rewrite_codex_feature_flag(text: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    bounds = _features_bounds(lines)
    if bounds is None:
        return text, False

    start, end = bounds
    hooks_present = False
    deprecated_indexes: list[int] = []
    replacement = None

    for index in range(start, end):
        match = _FEATURE_ASSIGNMENT.match(lines[index])
        if not match:
            continue
        indent, name, value, comment, newline = match.groups()
        if name == "hooks":
            hooks_present = True
        elif name == "codex_hooks":
            deprecated_indexes.append(index)
            if replacement is None:
                replacement = f"{indent}hooks = {value.strip()}{comment}{newline or ''}"

    if not deprecated_indexes:
        return text, False

    rewritten = []
    for index, line in enumerate(lines):
        if index not in deprecated_indexes:
            rewritten.append(line)
            continue
        if hooks_present or index != deprecated_indexes[0]:
            continue
        rewritten.append(replacement or "hooks = true\n")
    return "".join(rewritten), True


def _ask_codex_config_migration(path: Path) -> str:
    while True:
        answer = input(
            "Codex config uses deprecated [features].codex_hooks. "
            "Migrate it to [features].hooks? [y/n/exit] "
        ).strip().lower()
        if answer in {"y", "yes"}:
            return "yes"
        if answer in {"n", "no"}:
            return "no"
        if answer in {"e", "exit", "x"}:
            print(path)
            return "exit"
        print("Please answer y, n, or exit.")


def _migrate_codex_feature_flag(path: Path, apply: bool, interactive: bool) -> int:
    if not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Could not read Codex config {path}: {exc}", file=sys.stderr)
        return 1

    rewritten, changed = _rewrite_codex_feature_flag(text)
    if not changed:
        return 0
    if not apply:
        print(f"Codex config uses deprecated [features].codex_hooks: {path}")
        return 0
    if not interactive:
        print(
            f"Codex config uses deprecated [features].codex_hooks; "
            f"run this script interactively to migrate it: {path}",
            file=sys.stderr,
        )
        return 0

    answer = _ask_codex_config_migration(path)
    if answer == "no":
        return 0
    if answer == "exit":
        return 1

    try:
        path.write_text(rewritten, encoding="utf-8")
    except OSError as exc:
        print(f"Could not write Codex config {path}: {exc}", file=sys.stderr)
        return 1
    print(f"Migrated Codex config feature flag: {path}")
    return 0


# --- Plugin enable/disable sync ------------------------------------------
#
# Codex's plugin enable/disable state lives in `[plugins."<id>"]` tables
# (`enabled = true|false`), see https://developers.openai.com/codex/plugins.
# `plugins` isn't in config-advanced.md's list of keys blocked from
# project-local `.codex/config.toml`, so — unlike the global feature-flag
# migration above — this is synced to the project-local config file, the
# same trust scope as `.codex/hooks.json`.

TOML_GENERATED_MARKER = (
    "# [plugins].*.enabled below is managed by scripts/°base/ai/settings/sync.py "
    "from ai/tool-settings/settings.json's enabledPlugins; other content here is preserved."
)

_TABLE_HEADER = re.compile(r'^\[([^\[\]]+)\]\s*$')
_ENABLED_ASSIGNMENT = re.compile(r"^(\s*)enabled\s*=\s*(true|false)\b(.*)$")


def parse_codex_plugins(text: str) -> dict[str, bool]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return {}
    result: dict[str, bool] = {}
    for plugin_id, entry in plugins.items():
        if isinstance(entry, dict):
            result[plugin_id] = bool(entry.get("enabled", True))
    return result


def _plugin_table_name(plugin_id: str) -> str:
    return f"plugins.{json.dumps(plugin_id)}"


def _find_table_bounds(lines: list[str], table_name: str) -> tuple[int, int] | None:
    start = None
    for index, line in enumerate(lines):
        match = _TABLE_HEADER.match(line.strip())
        if not match:
            continue
        if start is not None:
            return start, index
        if match.group(1).strip() == table_name:
            start = index + 1
    if start is None:
        return None
    return start, len(lines)


def render_codex_plugins(text: str, enabled_plugins: dict[str, bool]) -> str:
    """Surgically insert/update `[plugins."<id>"].enabled` for each entry in
    `enabled_plugins`, leaving all other content untouched. Mirrors
    `_rewrite_codex_feature_flag`'s table-bounds text editing above."""
    if not text.strip():
        text = TOML_GENERATED_MARKER + "\n"

    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    for plugin_id, enabled in enabled_plugins.items():
        table_name = _plugin_table_name(plugin_id)
        value = "true" if enabled else "false"
        bounds = _find_table_bounds(lines, table_name)
        if bounds is None:
            if lines and lines[-1].strip() != "":
                lines.append("\n")
            lines.append(f"[{table_name}]\n")
            lines.append(f"enabled = {value}\n")
            continue

        start, end = bounds
        updated = False
        for index in range(start, end):
            match = _ENABLED_ASSIGNMENT.match(lines[index])
            if match:
                lines[index] = f"{match.group(1)}enabled = {value}{match.group(3)}\n"
                updated = True
                break
        if not updated:
            lines.insert(start, f"enabled = {value}\n")

    return "".join(lines)
