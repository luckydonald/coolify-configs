"""AI/base content classification for paths and commits."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Sequence

AI_TOP_LEVEL_DIRS = ("ai", ".claude", ".codex")
AI_EXACT_PATHS = (".mcp.json", "AGENTS.md", "CLAUDE.md")
BASE_SEGMENT_NAME = "°base"

# Matches this repo's real commit convention, e.g.:
#   "ai: updated prompt"
#   "[base] topic: ai: Run: ..."
#   "[dumper] init script: ai: Run: ..."
# but not "aisle: fix typo" or "said: hello".
AI_SUBJECT_RE = re.compile(r"^(\[.*\]\s*)?.*\bai:")


def is_ai_base_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    if parts[0] in AI_TOP_LEVEL_DIRS:
        return True
    if path in AI_EXACT_PATHS:
        return True
    if BASE_SEGMENT_NAME in parts:
        return True
    return False


@dataclass(frozen=True)
class CommitClassification:
    sha: str
    subject: str
    paths: tuple[str, ...]
    is_ai_only_commit: bool
    is_ai_tainted_commit: bool
    is_code_containing_commit: bool


def classify_commit(sha: str, subject: str, paths: Sequence[str]) -> CommitClassification:
    paths = tuple(paths)
    ai_flags = [is_ai_base_path(path) for path in paths]

    is_ai_only = bool(paths) and all(ai_flags)
    is_code_containing = any(not flag for flag in ai_flags)
    is_ai_tainted = is_ai_only or any(ai_flags) or bool(AI_SUBJECT_RE.match(subject))

    return CommitClassification(
        sha=sha,
        subject=subject,
        paths=paths,
        is_ai_only_commit=is_ai_only,
        is_ai_tainted_commit=is_ai_tainted,
        is_code_containing_commit=is_code_containing,
    )
