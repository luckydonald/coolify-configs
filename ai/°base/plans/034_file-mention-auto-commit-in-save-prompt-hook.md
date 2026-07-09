# File-mention auto-commit in save-prompt hook

## Context

`scripts/°base/ai/hooks/save-prompt/hook.py` is the `UserPromptSubmit` hook — it
appends every prompt to `ai[/°base]/query.md` and auto-commits that file
(`append_and_commit` in `_lib.py`). When a user mentions a file in their prompt
(Claude's `@subdir/file.foo` mention syntax, or a plain backtick-quoted path like
`` `subdir/file.foo` ``), that file is relevant context for the task but isn't
otherwise captured anywhere. If it's untracked, it can get lost (never committed,
easy to `git clean`/lose); if it's already tracked, it's still useful to have it
staged so it rides along with whatever commit the task produces next.

This adds a second step to the prompt hook: after the regular query.md commit,
scan the raw prompt text for file mentions, resolve them against files that
actually exist, and:
- **untracked** file → `git add` (force-add, bypassing `.gitignore`, when the
  path is under `ai/`) + a dedicated commit `ai: referenced file for task added.`
- **already-tracked** file → `git add` only (staged, no separate commit), so the
  next real commit for the task picks it up.

Non-existent mentions and mentions that aren't file paths are silently ignored —
this is best-effort extraction, not a hard requirement that every `@`/backtick
token resolve.

Per feedback: don't grow `_lib.py` further. Instead, follow the existing
namespaced-library convention used by `scripts/°base/git/°split_lib/` and
`scripts/°base/ai/references/°dllink_lib/` — a `°`-prefixed package directory,
split across a few small files, loaded via `importlib.import_module("°...")`
(a `°` isn't a valid Python identifier character, so it can never be a normal
`import` statement — this is exactly why those two existing libs use
`importlib` instead of `import`).

## Design

### New package: `scripts/°base/ai/hooks/°reffiles_lib/`

```
°reffiles_lib/
├── __init__.py   # re-exports the public API
├── mentions.py   # regex extraction of @mention / backtick-quoted candidate paths
└── commit.py     # is_tracked() + stage-or-commit logic
```

**`mentions.py`** — pure text logic, no git/filesystem calls:
```python
import re

_AT_MENTION_RE = re.compile(r"(?<!\S)@([^\s`]+)")
_BACKTICK_MENTION_RE = re.compile(r"`([^`\n]+)`")
_TRAILING_PUNCT = ".,;:!?)]}\"'"

def extract_candidate_paths(prompt: str) -> list[str]:
    """@mention and backtick-quoted candidate paths containing '/', deduped, order-preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for regex, strip in ((_AT_MENTION_RE, True), (_BACKTICK_MENTION_RE, False)):
        for m in regex.finditer(prompt):
            candidate = m.group(1).strip().rstrip(_TRAILING_PUNCT)
            if "/" in candidate and candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out
```

**`commit.py`** — git side effects. Imports `base_ai_commit_subject` from the
sibling `_lib.py` (one dir up) using the same sys.path trick `_lib.py` itself
already uses for `merge_staged` (`_lib.py:19-22`), since `°reffiles_lib` can't be
imported as a dotted subpackage of `hooks` either:
```python
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib import base_ai_commit_subject  # noqa: E402

def is_tracked(relpath: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relpath], capture_output=True,
    )
    return result.returncode == 0

def handle_referenced_files(prompt: str, subproject: Path) -> None:
    """Best-effort: stage/commit files mentioned in `prompt` that exist on disk.
    Assumes cwd is already the git root (as `resolve_log_path` leaves it)."""
    from .mentions import extract_candidate_paths

    for candidate in extract_candidate_paths(prompt):
        abspath = (subproject / candidate).resolve()
        if not abspath.is_file():
            continue
        try:
            relpath = str(abspath.relative_to(Path.cwd()))
        except ValueError:
            continue  # outside the repo — skip

        if is_tracked(relpath):
            subprocess.run(["git", "add", "--", relpath], capture_output=True)
            continue

        add_cmd = ["git", "add"]
        if relpath.startswith("ai/"):
            add_cmd.append("-f")  # bypass .gitignore for AI artifacts
        subprocess.run([*add_cmd, "--", relpath], capture_output=True)
        subprocess.run(
            ["git", "commit", "--no-verify", "--only", relpath,
             "-m", base_ai_commit_subject("ai: referenced file for task added.")],
            capture_output=True,
        )
```

**`__init__.py`**:
```python
from .commit import handle_referenced_files, is_tracked
from .mentions import extract_candidate_paths

__all__ = ["extract_candidate_paths", "handle_referenced_files", "is_tracked"]
```

### `scripts/°base/ai/hooks/save-prompt/hook.py`

Add `import importlib` alongside the existing imports, load the lib the same
way `°split_lib`/`°dllink_lib` consumers do:
```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
reffiles_lib = importlib.import_module("°reffiles_lib")
```

Capture the raw prompt right after extraction (line ~689, before any
Codex/GitHub-worker stripping reassigns `prompt`):
```python
raw_prompt = prompt
```

Call `reffiles_lib.handle_referenced_files(raw_prompt, _subproject_root())` once,
right before the final `return 0` in `main()` — after the last
`append_and_commit(...)` call on the plain-content path (not in the
compact-prompt / task-notification early-return branches, since those aren't
user-typed mentions). Needs `_subproject_root` added to the existing `_lib`
import line.

## Files to change

- `scripts/°base/ai/hooks/°reffiles_lib/__init__.py` (new)
- `scripts/°base/ai/hooks/°reffiles_lib/mentions.py` (new)
- `scripts/°base/ai/hooks/°reffiles_lib/commit.py` (new)
- `scripts/°base/ai/hooks/save-prompt/hook.py` — capture `raw_prompt`, import
  and call `°reffiles_lib`.
- `scripts/°base/tests/test_ai_hooks_base_routing.py` — add cases (following
  the existing `init_repo`/`run_hook`/`last_subject` harness, plus a direct
  `importlib.import_module("°reffiles_lib.mentions")` unit test for the regex,
  mirroring `test_git_split_recovery.py`'s `importlib.import_module("°split_lib...")`
  pattern):
  1. `@sub/file.txt` mention, file exists and is untracked → separate commit
     with subject `"[base] ai: referenced file for task added."` (or unprefixed
     outside the base repo), file is tracked afterward.
  2. `` `sub/file.txt` `` backtick mention, file already tracked → gets staged
     (`git diff --cached --name-only` includes it) but `git log` still shows
     only the one query.md commit (no extra commit).
  3. Mentioned path under `ai/...` matching a `.gitignore` pattern → still gets
     force-added and committed.
  4. Mentioned path that doesn't exist on disk → ignored, no error, hook still
     exits 0.
  5. Unit test on `extract_candidate_paths` covering `@a/b.txt`, `` `a/b.txt` ``,
     trailing punctuation stripping, and non-path tokens (no `/`) being excluded.

## Verification

```bash
uv run --project scripts/°base python -m unittest scripts.°base.tests.test_ai_hooks_base_routing -v
```
