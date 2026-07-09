"""Pure text extraction of file-path mentions from a prompt."""
from __future__ import annotations

import re

_AT_MENTION_RE = re.compile(r"(?<!\S)@([^\s`]+)")
_BACKTICK_MENTION_RE = re.compile(r"`([^`\n]+)`")
_TRAILING_PUNCT = ".,;:!?)]}\"'"


def extract_candidate_paths(prompt: str) -> list[str]:
    """@mention and backtick-quoted candidate paths containing '/', deduped, order-preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for regex in (_AT_MENTION_RE, _BACKTICK_MENTION_RE):
        for m in regex.finditer(prompt):
            candidate = m.group(1).strip().rstrip(_TRAILING_PUNCT)
            if "/" in candidate and candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out
