from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from . import paths
from .json_io import _write_text_if_changed

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)
_META_LINE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


def _parse_markdown_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    match = _FRONTMATTER.match(text)
    if not match:
        return None
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line_match = _META_LINE.match(line)
        if not line_match:
            continue
        key, value = line_match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = json.loads(value)
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        metadata[key] = value
    return metadata, match.group(2)


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _is_generated_markdown(text: str) -> bool:
    return paths.GENERATED_MARKER in text[:1000]


def _skill_name_from_path(path: Path) -> str:
    if path.name == "SKILL.md":
        return path.parent.name
    return path.stem


def _skill_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    return slug or "skill"


def _read_skill_source(path: Path, skip_generated: bool = True) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if skip_generated and _is_generated_markdown(text):
        return None
    parsed = _parse_markdown_frontmatter(text)
    metadata = parsed[0] if parsed else {}
    body = parsed[1] if parsed else text
    name = metadata.get("name") or _skill_name_from_path(path)
    description = metadata.get("description") or ""
    return {
        "name": name,
        "description": description,
        "body": body,
        "text": text,
        "mtime": path.stat().st_mtime,
        "path": path,
    }


def _iter_skill_source_paths() -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    if paths.SHARED_SKILLS.is_dir():
        result.extend((path, "shared") for path in sorted(paths.SHARED_SKILLS.glob("*/SKILL.md")))
    if paths.AGENTS_SKILLS.is_dir():
        result.extend((path, "codex_skill") for path in sorted(paths.AGENTS_SKILLS.glob("*/SKILL.md")))
    if paths.CLAUDE_SKILLS.is_dir():
        result.extend((path, "claude_skill") for path in sorted(paths.CLAUDE_SKILLS.glob("*/SKILL.md")))
    if paths.CLAUDE_COMMANDS.is_dir():
        result.extend((path, "claude_command") for path in sorted(paths.CLAUDE_COMMANDS.glob("*.md")))
    if paths.CODEX_COMMANDS.is_dir():
        result.extend((path, "codex_command") for path in sorted(paths.CODEX_COMMANDS.glob("*.md")))
    return result


def _collect_skill_sources() -> tuple[dict[str, dict[str, Any]], dict[str, set[Path]]]:
    selected: dict[str, dict[str, Any]] = {}
    claude_paths: dict[str, set[Path]] = {}

    for path, kind in _iter_skill_source_paths():
        source = _read_skill_source(path)
        if source is None:
            continue
        name = str(source["name"])
        if kind.startswith("claude_"):
            claude_paths.setdefault(name, set()).add(path)
        current = selected.get(name)
        if current is None or float(source["mtime"]) >= float(current["mtime"]):
            selected[name] = source

    return selected, claude_paths


def _render_canonical_skill(name: str, description: str, body: str) -> str:
    body = body.lstrip("\n")
    return (
        f"---\n"
        f"name: {_yaml_scalar(name)}\n"
        f"description: {_yaml_scalar(description)}\n"
        f"---\n\n"
        f"{body}"
    )


def _write_symlink_if_changed(link_path: Path, target_path: Path, apply: bool) -> list[str]:
    relative_target = Path(os.path.relpath(target_path, start=link_path.parent))
    if link_path.is_symlink() and Path(os.readlink(link_path)) == relative_target:
        return []
    if apply:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        if link_path.is_symlink() or link_path.exists():
            link_path.unlink()
        link_path.symlink_to(relative_target)
    return [str(link_path)]


def _write_dir_symlink_if_changed(link_path: Path, target_path: Path, apply: bool) -> list[str]:
    """Like _write_symlink_if_changed, but symlinks a whole skill directory rather than a
    single file — so multi-file skills (references/, scripts/, ...) mirror completely instead
    of only their SKILL.md being visible at the mirrored location."""
    relative_target = Path(os.path.relpath(target_path, start=link_path.parent))
    if link_path.is_symlink() and Path(os.readlink(link_path)) == relative_target:
        return []
    if apply:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        if link_path.is_symlink():
            link_path.unlink()
        elif link_path.is_dir():
            shutil.rmtree(link_path)
        elif link_path.exists():
            link_path.unlink()
        link_path.symlink_to(relative_target, target_is_directory=True)
    return [str(link_path)]


def _promote_extra_skill_files(source_skill_md: Path, shared_dir: Path, apply: bool) -> list[str]:
    """The first time a skill is discovered outside its canonical ai/skills/<slug>/ directory
    (e.g. authored directly under .claude/skills/<slug>/SKILL.md), copy any sibling files or
    directories — references/, scripts/, assets/ — into the canonical directory alongside it.
    Only SKILL.md's *text* gets re-rendered by the caller; everything else just needs to exist
    in one place so the directory symlinks can pick it up. Never overwrites files that already
    exist in the canonical directory, so this is a no-op once a skill is already canonical."""
    changed: list[str] = []
    source_dir = source_skill_md.parent
    try:
        if source_dir.resolve() == shared_dir.resolve():
            return changed
    except OSError:
        pass
    if not source_dir.is_dir():
        return changed
    for item in sorted(source_dir.iterdir()):
        if item.name == "SKILL.md":
            continue
        dest = shared_dir / item.name
        if dest.exists():
            continue
        changed.append(str(dest))
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
    return changed


def _render_claude_command_shim(name: str, description: str, shared_path: Path) -> str:
    command_description = description or f"Invoke the {name} skill."
    return (
        f"---\n"
        f"name: {_yaml_scalar(name)}\n"
        f"description: {_yaml_scalar(command_description)}\n"
        f"---\n\n"
        f"{paths.GENERATED_MARKER}\n\n"
        f"Use the `{name}` skill for this request. The canonical source is "
        f"`{shared_path.as_posix()}`.\n"
    )


def _sync_skills(apply: bool) -> list[str]:
    changed: list[str] = []
    sources, claude_paths = _collect_skill_sources()
    if not sources:
        return changed

    for name, source in sorted(sources.items()):
        slug = _skill_slug(name)
        shared_dir = paths.SHARED_SKILLS / slug
        shared_path = shared_dir / "SKILL.md"
        description = str(source["description"])
        skill_text = _render_canonical_skill(name, description, str(source["body"]))

        changed.extend(_promote_extra_skill_files(Path(source["path"]), shared_dir, apply))
        changed.extend(_write_text_if_changed(shared_path, skill_text, apply))

        codex_skill_dir = paths.AGENTS_SKILLS / slug
        claude_skill_dirs = {paths.CLAUDE_SKILLS / slug}
        claude_command_paths = {paths.CLAUDE_COMMANDS / f"{slug}.md"}
        for path in claude_paths.get(name, set()):
            if path.name == "SKILL.md":
                claude_skill_dirs.add(path.parent)
            elif path.suffix == ".md":
                claude_command_paths.add(path)

        command_shim = _render_claude_command_shim(name, description, shared_path)
        changed.extend(_write_dir_symlink_if_changed(codex_skill_dir, shared_dir, apply))
        for path in sorted(claude_skill_dirs):
            changed.extend(_write_dir_symlink_if_changed(path, shared_dir, apply))
        for path in sorted(claude_command_paths):
            changed.extend(_write_text_if_changed(path, command_shim, apply))

    return changed
