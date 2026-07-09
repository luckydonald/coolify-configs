#!/usr/bin/env python3
"""Rebase the current branch onto its merge-base with origin/mane, rewriting any
claude[bot] author/committer identity to Lucky Lucy's along the way.

Usage:
    python3 rebase_strip_claude_authorship.py            # run the rebase
    python3 rebase_strip_claude_authorship.py --amend-step  # internal: used as --exec callback
"""

from __future__ import annotations

import importlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_identity = importlib.import_module("°split_lib.identity")
NEW_AUTHOR, NEW_NAME, NEW_EMAIL = _identity.BOT_AUTHOR, _identity.BOT_NAME, _identity.BOT_EMAIL

UPSTREAM = "origin/mane"
CLAUDE_EMAIL = "41898282+claude[bot]@users.noreply.github.com"


def capture(*args: str) -> str:
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


def shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def amend_step() -> None:
    """Rewrite HEAD's author/committer if it's the claude[bot] identity."""
    author_email = capture("git", "log", "-1", "--format=%ae")
    committer_email = capture("git", "log", "-1", "--format=%ce")
    if CLAUDE_EMAIL not in (author_email, committer_email):
        return

    env = os.environ.copy()
    env["GIT_COMMITTER_NAME"] = NEW_NAME
    env["GIT_COMMITTER_EMAIL"] = NEW_EMAIL
    subprocess.run(
        ["git", "commit", "--amend", "--no-edit", "--author", NEW_AUTHOR],
        check=True,
        env=env,
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if "--amend-step" in args:
        amend_step()
        return 0

    subprocess.run(["git", "fetch", "origin", "mane"], check=True)
    merge_base = capture("git", "merge-base", "HEAD", UPSTREAM)
    print(f"Rebasing onto merge-base {merge_base} with {UPSTREAM}, stripping claude[bot] authorship...")

    script_path = Path(__file__).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="rebase-strip-claude-"))
    exec_script = tmp / script_path.name
    shutil.copy2(script_path, exec_script)
    # The relocated copy still imports °split_lib.identity by path relative to
    # its own location, so it needs a sibling copy of the package too.
    shutil.copytree(script_path.parent / "°split_lib", tmp / "°split_lib")
    exec_cmd = shell_join([sys.executable, str(exec_script), "--amend-step"])
    try:
        subprocess.run(["git", "rebase", merge_base, "--exec", exec_cmd], check=True)
    except subprocess.CalledProcessError as exc:
        print(
            "\nRebase stopped before claude[bot] authorship stripping completed.",
            file=sys.stderr,
        )
        print(f"Kept the rebase --exec callback at: {exec_script}", file=sys.stderr)
        print("Resolve conflicts, stage the resolved files, then run:", file=sys.stderr)
        print("  git rebase --continue", file=sys.stderr)
        print("To abandon the rebase, run:", file=sys.stderr)
        print("  git rebase --abort", file=sys.stderr)
        return exc.returncode
    shutil.rmtree(tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
