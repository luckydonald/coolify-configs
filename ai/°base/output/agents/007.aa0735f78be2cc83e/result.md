## Summary of Findings

Phase 1 requires: branch classification, AI-content classification, a pure push-policy matrix, and a **real, working pre-push hook** that composes correctly with the existing git-lfs pre-push script. I traced the exact repo conventions to reuse (`ai/settings/sync.py` → `°settings_lib/cli.py` shim pattern, `°settings_lib` dynamic-import test pattern, `.pre-commit-config.yaml`/installer wiring) and — critically — I read pre-commit 4.6.0's actual `hook_impl.py`/`run.py` source (installed at `/usr/lib/python3.14/site-packages/pre_commit/commands/`) to resolve the composition question the task asked me not to guess on.

### Key research finding (resolves the flagged open question)

Running `pre_commit install --hooks-type pre-push` (without `-f`/`--overwrite`) is **safe for git-lfs**: pre-commit detects the existing non-pre-commit `pre-push` script, renames it to `.git/hooks/pre-push.legacy`, and its generated dispatcher (`hook_impl.hook_impl`) runs `retv, stdin = _run_legacy(...)` **first**, forwarding both argv and the full raw stdin to that legacy script — so `git lfs pre-push "$@"` keeps working unmodified.

**However**, this is *not* sufficient for our own multi-ref check logic:
- `hook_impl.py:_pre_push_ns` only computes `PRE_COMMIT_FROM_REF`/`PRE_COMMIT_TO_REF`/etc. from the **first** ref-update line with an early `return` — it silently ignores every other line when a single `git push` updates several branches at once.
- `pre_commit/util.py` runs the actual hook-entry subprocess with **stdin redirected to `/dev/null`** — the raw multi-line ref-update stdin never reaches a pre-commit-managed `local` hook's `entry:` command at all.

So a `.pre-commit-config.yaml` local hook with `stages: [pre-push]` **cannot** implement item 5's requirement to see every pushed ref and aggregate violations across all of them. This confirms the task's own escape hatch is the correct path: **bypass `pre_commit install --hooks-type pre-push` entirely** and hand-roll `.git/hooks/pre-push` ourselves, with our tracked script explicitly re-invoking `git lfs pre-push "$@"` (buffering stdin so it can be fed to both git-lfs and our checker).

## Detailed Plan

### 1. New library package: `scripts/°base/git/°split_lib/`

**`scripts/°base/git/°split_lib/__init__.py`** — empty (matches `°settings_lib/__init__.py`).

**`scripts/°base/git/°split_lib/branches.py`**
```python
UNCLEAN_RE = re.compile(r"^ai/UNCLEAN/(.+)$")
HISTORY_RE = re.compile(r"^ai/history/(.+)$")

class BranchFormat(str, Enum): CLEAN = "clean"; UNCLEAN = "unclean"; HISTORY = "history"

@dataclass(frozen=True)
class BranchClassification:
    ref: str; format: BranchFormat; base_name: str; is_history_master: bool

def strip_refs_heads(ref: str) -> str: ...
def classify_branch(ref: str, *, main_branch: str = "master") -> BranchClassification: ...
def unclean_name(base_branch: str) -> str: return f"ai/UNCLEAN/{base_branch}"
def history_name(base_branch: str) -> str: return f"ai/history/{base_branch}"
def base_name_from_unclean(ref: str) -> str | None: ...
def base_name_from_history(ref: str) -> str | None: ...
def detect_main_branch(repo_root: Path) -> str: ...  # origin/HEAD, else main/master fallback
```
`ai/history/{main_branch}` still classifies as `HISTORY`; `is_history_master` is a distinct flag callers can check.

**`scripts/°base/git/°split_lib/classify.py`**
```python
AI_TOP_LEVEL_DIRS = ("ai", ".claude", ".codex")
AI_EXACT_PATHS = (".mcp.json", "AGENTS.md", "CLAUDE.md")
BASE_SEGMENT_NAME = "°base"
AI_SUBJECT_RE = re.compile(r"^(\[.*\]\s*)?ai:")  # exactly as confirmed with user, case-sensitive

def is_ai_base_path(path: str) -> bool: ...  # PurePosixPath(path).parts[0] in AI_TOP_LEVEL_DIRS,
                                              # or path in AI_EXACT_PATHS, or BASE_SEGMENT_NAME in parts

@dataclass(frozen=True)
class CommitClassification:
    sha: str; subject: str; paths: tuple[str, ...]
    is_ai_only_commit: bool; is_ai_tainted_commit: bool; is_code_containing_commit: bool

def classify_commit(sha: str, subject: str, paths: Sequence[str]) -> CommitClassification: ...
```

**`scripts/°base/git/°split_lib/git_ops.py`** (all subprocess/git glue, kept separate so `push_checks.py` stays pure):
```python
def repo_root(cwd: Path | None = None) -> str: ...
def rev_exists(sha: str, cwd: Path) -> bool: ...  # git cat-file -e {sha}^{commit}
def commits_new_to_remote(local_sha: str, remote_sha: str, remote_name: str, cwd: Path) -> list[str]:
    # deletion (local all-zero) -> []
    # remote_sha exists locally -> git rev-list --reverse {remote_sha}..{local_sha}
    # else (new branch / shallow) -> git rev-list --reverse {local_sha} --not --remotes={remote_name}
    #   (this exact fallback idiom is lifted from pre_commit/commands/hook_impl.py:_pre_push_ns)
def changed_paths_for_commit(sha: str, cwd: Path) -> list[str]: ...  # git diff-tree --no-commit-id --name-only -r {sha}
def subject_for_commit(sha: str, cwd: Path) -> str: ...  # git log -1 --format=%s {sha}
```

**`scripts/°base/git/°split_lib/push_checks.py`** (pure, no subprocess):
```python
@dataclass(frozen=True)
class RefUpdate: local_ref: str; local_sha: str; remote_ref: str; remote_sha: str

def is_zero_sha(sha: str) -> bool: return set(sha) == {"0"} and len(sha) in (40, 64)

def check_content_policy(branch: BranchClassification, commits: list[CommitClassification]) -> list[str]:
    # clean: block any commit where is_ai_tainted_commit
    # history: block any commit where is_code_containing_commit
    # unclean: no content restriction

def check_name_policy(branch: BranchClassification, remote_name: str) -> str | None:
    # unclean/history -> remote 'origin' literally -> violation message; else None

def evaluate_ref_update(ref_update, branch, remote_name, commits) -> list[str]:
    # combine name+content violations for one ref line
```

**`scripts/°base/git/°split_lib/cli.py`**
```python
def _parse_ref_lines(text: str) -> list[push_checks.RefUpdate]: ...
def _check_push(remote_name, remote_url, stdin_text, *, repo_root: Path) -> int:
    # for each RefUpdate: skip deletions; classify branch; git_ops.commits_new_to_remote;
    # classify each commit; push_checks.evaluate_ref_update; aggregate ALL messages
    # across ALL ref lines before printing one report and returning 1 if any.

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="split.py")
    sub = parser.add_subparsers(dest="command", required=True)
    check_push = sub.add_parser("check-push", ...)
    check_push.add_argument("--remote-name", required=True)
    check_push.add_argument("--remote-url", required=True)
    # dispatch to _check_push(args.remote_name, args.remote_url, sys.stdin.read(), repo_root=...)
```

### 2. CLI entry point shim

**`scripts/°base/git/split.py`** — exact mirror of `sync.py`:
```python
from __future__ import annotations
import importlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
main = importlib.import_module("°split_lib.cli").main

if __name__ == "__main__":
    raise SystemExit(main())
```

### 3. The pre-push hook script (bypasses pre-commit's pre-push stage — see finding above)

**`scripts/°base/git/hooks/push/pre_push.sh`** (tracked, executable):
```sh
#!/usr/bin/env sh
# Combined pre-push hook: preserves the existing git-lfs pre-push behavior,
# then enforces the base branch-split push-name/content policy (see
# scripts/°base/git/°split_lib/push_checks.py, ai/°base/todo.md lines 155-163).
#
# `.git/hooks/pre-push` is a tiny generated trampoline (written by
# scripts/°base/git/hooks/install) that execs this tracked script, so edits
# here take effect without re-running the installer.
#
# argv: <remote-name> <remote-url>
# stdin: 0+ lines of "<local ref> <local sha> <remote ref> <remote sha>"

set -u
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.pyenv/shims:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:$PATH"

remote_name="${1:-}"
remote_url="${2:-}"
repo_root="$(git rev-parse --show-toplevel)"

stdin_buf="$(mktemp)"
trap 'rm -f "$stdin_buf"' EXIT
cat >"$stdin_buf"

status=0

if command -v git-lfs >/dev/null 2>&1; then
    git lfs pre-push "$remote_name" "$remote_url" <"$stdin_buf" || status=$?
else
    echo "git-lfs was not found on PATH for the pre-push hook." >&2
    status=2
fi

python3 "$repo_root/scripts/°base/git/split.py" check-push \
    --remote-name "$remote_name" --remote-url "$remote_url" \
    <"$stdin_buf" || status=$?

exit "$status"
```

### 4. Installer diff (both `install/__init__.py` and `install/setup.py`)

Add, after the existing `pre_commit install --hooks-type commit-msg` call:
```python
def _install_pre_push_hook(repo_root: Path) -> None:
    marker = "Auto-generated by scripts/°base/git/hooks/install"
    hooks_dir = repo_root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    target = hooks_dir / "pre-push"
    if target.exists() and marker not in target.read_text(encoding="utf-8", errors="ignore"):
        backup = hooks_dir / "pre-push.pre-base-backup"
        if not backup.exists():
            shutil.copy2(target, backup)  # preserve whatever `git lfs install` wrote, just in case
    target.write_text(
        "#!/usr/bin/env sh\n"
        f"# {marker} — do not edit by hand.\n"
        "set -eu\n"
        'repo_root="$(git rev-parse --show-toplevel)"\n'
        'exec "$repo_root/scripts/°base/git/hooks/push/pre_push.sh" "$@"\n',
        encoding="utf-8",
    )
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
```
No changes needed to `.pre-commit-config.yaml` / `.pre-commit-hooks.yaml` under this design, since the pre-push enforcement deliberately does not go through pre-commit's `pre-push` hook-type.

### 5. Tests (stdlib `unittest`, dynamic-load pattern copied verbatim from `test_ai_settings_sync.py`)

All three new files use:
```python
LIB_ROOT = Path(__file__).resolve().parents[1] / "git"
sys.path.insert(0, str(LIB_ROOT))
branches = importlib.import_module("°split_lib.branches")
# ... classify, git_ops, push_checks, cli
```

- **`test_git_split_branches.py`**: clean/unclean/history classification incl. `refs/heads/` stripping; `ai/history/master` → `is_history_master=True` when `main_branch="master"`, `False` when `main_branch="main"`; malformed `ai/UNCLEAN/` (empty suffix) falls through to CLEAN; round-trip `base_name_from_unclean(unclean_name(x)) == x` and same for history; `detect_main_branch` against a real `tempfile.TemporaryDirectory()` repo created with `git init -b main`.
- **`test_git_split_classify.py`**: `is_ai_base_path` true/false table incl. `°base` segment anywhere (`scripts/°base/x`, `ai/°base/x`) vs. false-positive guards (`ai-notes.txt`, `claude-thing/x`, bare `base/x` without the degree sign); `classify_commit` matrix (ai-only, mixed, pure-code, empty-paths-with-`ai:`-subject); `AI_SUBJECT_RE` cases including the confirmed **non-match** for `"[base] topic: ai: Run: ..."` (see open question below — test documents current behavior, doesn't silently "fix" it).
- **`test_git_split_push_checks.py`**: pure-function table test over all `(format, ai_tainted?, code_containing?)` combos against the 3×2 matrix; `check_name_policy` for origin+unclean/history (blocked) vs. origin+clean and non-origin+anything (allowed).
- **End-to-end** (real temp git repo via `subprocess`+`tempfile.TemporaryDirectory()`, calling `cli._check_push(...)` directly rather than through real stdin/argv): unclean→non-origin with mixed commit (allowed); unclean→origin (blocked, name-only); clean→non-origin with pure-code commit (allowed); clean→non-origin with `ai:`-subject-only commit (blocked, content); history→non-origin with code-touching commit (blocked, content); history→origin with ai-only commits (blocked with **both** name and content messages present); deleted branch (`local_sha` all-zero) → always allowed/skipped; brand-new branch (`remote_sha` all-zero, no remote configured) exercised via the `--not --remotes=` fallback.

## Open Design Questions (flagged, not guessed on)

1. **Pre-commit pre-push stage is fundamentally single-ref** (confirmed by reading `pre_commit/commands/hook_impl.py`). Recommend NOT wiring this feature through `pre_commit install --hooks-type pre-push` / `.pre-commit-config.yaml` at all — hand-roll `.git/hooks/pre-push` directly as designed above. This deviates from the literal ask in item 6 ("installer... needs a second `pre_commit install --hooks-type pre-push` call") but is the one the user's own fallback clause anticipated. Needs explicit confirmation before implementing.
2. **`AI_SUBJECT_RE` doesn't match the repo's own documented commit convention.** Real commits look like `[base] topic: ai: Run: ...` (verified via `git log`, e.g. `4d00ee5 [base] [dumper] init script: ai: Run: ...`), but the confirmed regex `^(\[.*\]\s*)?ai:` only matches when `ai:` follows immediately after at most one bracket tag — it will *not* match when a `topic:` segment sits between the bracket and `ai:`. Since `is_ai_tainted_commit` is already true whenever any changed path is AI/base content, this mostly matters for the narrow edge case of a commit whose subject says "ai:" but touches zero AI/base paths. Confirm whether the regex should be loosened (e.g. search for `ai:` anywhere after an optional bracket+arbitrary-topic prefix) or left as specified.
3. **`is_ai_only_commit` for a commit with zero changed paths** (relevant to phase 2's deliberately-empty history commits): the literal spec ("every changed path is AI/base content") is vacuously true for an empty path set, which would also flip `is_ai_tainted_commit` to true for any empty commit regardless of subject. Flagging so the user can decide whether to special-case empty commits now (`is_ai_only = bool(paths) and all(...)`) or leave vacuous-true and let phase 2 handle it.
4. **Branch deletion + name policy**: should deleting an `ai/UNCLEAN/*`/`ai/history/*` ref *from* `origin` also be blocked by the name check, or should deletions (a cleanup action) always be exempt? Plan currently exempts all-zero `local_sha` (deletions) from both checks entirely.

### Critical Files for Implementation
- /home/user/git/luckydonald/base/scripts/°base/ai/settings/sync.py (shim pattern to mirror for `split.py`)
- /home/user/git/luckydonald/base/scripts/°base/ai/settings/°settings_lib/cli.py (argparse + relative-import package pattern)
- /home/user/git/luckydonald/base/scripts/°base/git/hooks/install/__init__.py (installer to extend with pre-push wiring)
- /home/user/git/luckydonald/base/scripts/°base/tests/test_ai_settings_sync.py (exact dynamic-import test-loading convention to replicate)
- /home/user/git/luckydonald/base/scripts/°base/ai/hooks/_lib.py (`base_ai_commit_subject`/real `ai:` commit convention source of truth)
- /home/user/git/luckydonald/base/.pre-commit-config.yaml (confirms no existing pre-push stage; no edits planned here per finding #1)