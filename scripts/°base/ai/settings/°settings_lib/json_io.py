from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _reorder_enabled_first(d: dict[str, Any]) -> list[tuple[str, Any]]:
    items = list(d.items())
    if "enabled" not in d:
        return items
    return [("enabled", d["enabled"])] + [(k, v) for k, v in items if k != "enabled"]


def _compact_json(value: Any) -> str:
    """Single-line rendering used for individual permission entries and
    whole `cmd` arrays; still applies enabled-first ordering at any depth."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k, ensure_ascii=False)}: {_compact_json(v)}" for k, v in _reorder_enabled_first(value)) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_compact_json(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def _dump_json(value: Any, indent: int = 0, key: str | None = None) -> str:
    """Pretty-print like `json.dumps(indent=2)`, except:
    - a dict's `"enabled"` key (if present) always renders first, at any depth
    - `permissions.allow`/`permissions.deny` array elements render single-line each
    - a `"cmd"` array (only ever `mcp.tools.*.*.cmd` / `mcp.servers.*.cmd` in this
      schema) renders as a single line
    """
    pad = "  " * indent
    pad_in = "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = _reorder_enabled_first(value)
        parts = [f'{pad_in}{json.dumps(k, ensure_ascii=False)}: {_dump_json(v, indent + 1, k)}' for k, v in items]
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if key == "cmd" and all(isinstance(item, str) for item in value):
            return _compact_json(value)
        if key in ("allow", "deny") and all(isinstance(item, dict) for item in value):
            parts = [f"{pad_in}{_compact_json(item)}" for item in value]
            return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
        parts = [f"{pad_in}{_dump_json(item, indent + 1)}" for item in value]
        return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
    return json.dumps(value, ensure_ascii=False)


def _backup_path(path: Path, timestamp: str) -> Path:
    """`settings.json` -> `settings.bak.<timestamp>.json`, `settings.local.json`
    -> `settings.local.bak.<timestamp>.json`, etc. Inserting before the final
    suffix keeps the `.local` marker intact so gitignore patterns like
    `**/*.local.*` still cover the backup."""
    name = path.name
    if "." in name:
        base, ext = name.rsplit(".", 1)
        return path.with_name(f"{base}.bak.{timestamp}.{ext}")
    return path.with_name(f"{name}.bak.{timestamp}")


def _backup_if_needed(path: Path, backup_timestamp: str | None) -> str | None:
    if backup_timestamp is None or not path.is_file():
        return None
    backup = _backup_path(path, backup_timestamp)
    backup.write_bytes(path.read_bytes())
    return str(backup)


def _write_json(path: Path, data: dict[str, Any], backup_timestamp: str | None = None) -> None:
    _backup_if_needed(path, backup_timestamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _dump_json(data) + "\n"
    path.write_text(text, encoding="utf-8")


def _same_json(path: Path, data: dict[str, Any]) -> bool:
    if not path.is_file():
        return False
    return _read_json(path) == data


def _unique(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for value in values:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _write_text_if_changed(path: Path, text: str, apply: bool, backup_timestamp: str | None = None) -> list[str]:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return []
    if apply:
        _backup_if_needed(path, backup_timestamp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return [str(path)]
