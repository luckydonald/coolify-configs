"""Shared bot committer identity for tool-generated commits (split/history-master
generation, rebase-with-authorship-rewrite). Kept in one place so every script
that creates commits on the user's behalf agrees on who "the tool" is.
"""

from __future__ import annotations

BOT_NAME = "✨❯ Lucky Lucy"
BOT_EMAIL = "claude._.ai._.code@luckydonald.de"
BOT_AUTHOR = f"{BOT_NAME} <{BOT_EMAIL}>"
