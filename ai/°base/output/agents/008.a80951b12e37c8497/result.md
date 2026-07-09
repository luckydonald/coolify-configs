Now I have everything needed. Here's the report.

## 1. Git trailer usage

No existing trailer parser/writer exists in the repo. `git interpret-trailers` is never invoked anywhere. The only trailer-adjacent code is a naive substring check:

- `scripts/°base/git/hooks/commit/reject_co_authored_by.py:16-17` — rejects a commit message if it contains the literal substring `"Co-Authored-By"`. No parsing of trailer syntax (key/value split, positioning at end of body, etc.) — it's a blunt `in` check.
- `.claude/hooks/permission-check.py:105,147,162` — same idea at the permission-hook layer (matches `Co-Authored-By:` with a required colon in commit-message text via regex, per comments; no generic trailer machinery).
- All other `trailer` hits are prose in planning docs (`ai/°base/todo.md`, `ai/°base/query.md`, `ai/°base/plans/026_*.md`) proposing `X-Base-Split-Source`/`X-Base-Split-Kind` trailers — confirmed intent but not yet implemented — and unrelated OpenAI Codex doc references under `ai/references/https/developers.openai.com/codex/`.

**Conclusion: there is no reusable trailer helper.** Phase 2 will need to write its own, ideally via `git interpret-trailers --parse` / `--trailer "Key=Value"` (shelling out) rather than mirroring the substring-check style used by `reject_co_authored_by.py`, since that pattern is too naive for round-trip parsing.

## 2. Partial-tree / commit-tree construction precedent

No code anywhere builds a commit from a partial tree via the git plumbing commands you listed. Findings:

- `commit-tree`, `read-tree`, `mktree`, `checkout-index`: **zero hits** anywhere in the repo.
- `update-index`: one hit, `scripts/°base/ai/hooks/_lib.py:195` — `git update-index --cacheinfo 100644,<blob>,<relpath>`, used to stage a single merged blob into the index during a pre-commit merge-log hook (`_restore_staged`, lines 180-202). It hashes content via `git hash-object -w` (line 192) then stages it with `--cacheinfo`. This is single-file, index-level plumbing — a decent pattern to mirror for staging individual blobs, but not a full partial-tree/commit-tree builder.
- `diff-tree`: `scripts/°base/git/°split_lib/git_ops.py:54` — `git diff-tree --no-commit-id --name-only -r {sha}` to list changed paths for a commit (`changed_paths_for_commit`). This is Phase 1's only plumbing usage, and it's read-only (path listing), not tree construction.
- `git apply`: no real usage — the two hits in `scripts/°base/git/remote/fix_username.py:724,1517` are an unrelated TUI action button literally labeled `"apply"`, not `git apply`.
- `rebase_strip_claude_authorship.py` (full file at `scripts/°base/git/rebase_strip_claude_authorship.py`) does a full `git rebase <merge-base> --exec <callback>` (line 68) with an `--amend-step` callback that does `git commit --amend --no-edit --author ...` (line 46) to rewrite author identity — useful precedent for "rebase with an exec callback that touches each commit," but it always operates on full working-tree checkouts/commits, not partial trees.

**Conclusion: there is no precedent for partial-tree commit construction in this repo.** Phase 2 will need to introduce `git commit-tree`/`git read-tree`/`git update-index --cacheinfo` (or equivalent index manipulation) from scratch. The closest existing pattern to build on is `_lib.py`'s `hash-object -w` + `update-index --cacheinfo` blob-staging idiom (`scripts/°base/ai/hooks/_lib.py:190-197`).

## 3. Empty commit handling

`--allow-empty` has **zero hits** anywhere in the repo (code, hooks, or docs). No existing precedent or reasoning to reuse — Phase 2 will need to decide this itself. Note the planning docs (`ai/°base/todo.md:105-138`, `ai/°base/plans/026_...md:56`) already anticipate needing to "strip code content but *keep* commits even if empty" for the `history` split and mention "dedicated metadata commits as a fallback," which implies `--allow-empty` (or the commit-tree equivalent — a commit whose tree is identical to its parent's) will be required, but nothing currently implements it.

## 4. filter-branch / filter-repo / BFG usage

`filter-branch`, `filter-repo`, and `bfg` all have **zero hits** anywhere in the repo. No existing history-rewriting tooling to reuse — Phase 1's `rebase_strip_claude_authorship.py` (analyzed above) is the only rebase/history-adjacent script, and it uses plain `git rebase --exec`, not filter-branch/filter-repo/BFG.

## 5. Phase 1 public API surface

**`scripts/°base/git/°split_lib/branches.py`**
```python
UNCLEAN_RE = re.compile(r"^ai/UNCLEAN/(.+)$")
HISTORY_RE = re.compile(r"^ai/history/(.+)$")

class BranchFormat(str, Enum): CLEAN = "clean"; UNCLEAN = "unclean"; HISTORY = "history"

@dataclass(frozen=True)
class BranchClassification:
    ref: str
    format: BranchFormat
    base_name: str
    is_history_master: bool

def strip_refs_heads(ref: str) -> str
def classify_branch(ref: str, *, main_branch: str = "master") -> BranchClassification
def unclean_name(base_branch: str) -> str
def history_name(base_branch: str) -> str
def base_name_from_unclean(ref: str) -> str | None
def base_name_from_history(ref: str) -> str | None
def detect_main_branch(repo_root: Path) -> str
```

**`scripts/°base/git/°split_lib/classify.py`**
```python
AI_TOP_LEVEL_DIRS = ("ai", ".claude", ".codex")
AI_EXACT_PATHS = (".mcp.json", "AGENTS.md", "CLAUDE.md")
BASE_SEGMENT_NAME = "°base"
AI_SUBJECT_RE = re.compile(r"^(\[.*\]\s*)?.*\bai:")

def is_ai_base_path(path: str) -> bool

@dataclass(frozen=True)
class CommitClassification:
    sha: str
    subject: str
    paths: tuple[str, ...]
    is_ai_only_commit: bool
    is_ai_tainted_commit: bool
    is_code_containing_commit: bool

def classify_commit(sha: str, subject: str, paths: Sequence[str]) -> CommitClassification
```

**`scripts/°base/git/°split_lib/push_checks.py`** (pure, no subprocess)
```python
ORIGIN_REMOTE_NAME = "origin"

@dataclass(frozen=True)
class RefUpdate:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str

def is_zero_sha(sha: str) -> bool
def check_content_policy(branch: BranchClassification, commits: list[CommitClassification]) -> list[str]
def check_name_policy(branch: BranchClassification, remote_name: str) -> str | None
def evaluate_ref_update(ref_update: RefUpdate, branch: BranchClassification, remote_name: str, commits: list[CommitClassification]) -> list[str]
```

**`scripts/°base/git/°split_lib/git_ops.py`** (subprocess glue only)
```python
def repo_root(cwd: Path | None = None) -> Path
def rev_exists(sha: str, cwd: Path) -> bool
def commits_new_to_remote(local_sha: str, remote_sha: str, remote_name: str, cwd: Path) -> list[str]
def changed_paths_for_commit(sha: str, cwd: Path) -> list[str]
def subject_for_commit(sha: str, cwd: Path) -> str
```

**`scripts/°base/git/°split_lib/cli.py`**
```python
def _parse_ref_lines(text: str) -> list[push_checks.RefUpdate]
def _check_push(remote_name: str, remote_url: str, stdin_text: str, *, repo_root: Path) -> int
def main(argv: list[str] | None = None) -> int
```
`main` currently registers only a `check-push` subparser (`--remote-name`, `--remote-url`, reads ref-update lines from stdin); Phase 2 subcommands (`sync-splits`, `update-history-master`, `rebase-branches-to-master`) will be added as sibling `add_parser` calls here per the plan's stated shim pattern (`ai/°base/output/agents/005.../result.md:53`).

## 6. Confirmation: no existing sync-splits/history-master/rebase implementation

Confirmed — every hit for `sync-splits`, `update-history-master`, `rebase-branches-to-master`, `history/master`, `force-merge` outside the known planning docs (`ai/°base/todo.md`, `ai/°base/query.md`, `ai/°base/plans/026_*.md`) is either:
- prose in `ai/°base/output/agents/*/prompt.md` and `.../result.md` (prior planning-agent transcripts, not implementation), or
- `scripts/°base/tests/test_git_split_branches.py:41,46` — tests that merely exercise `branches.classify_branch("ai/history/master", ...)`'s `is_history_master` flag; no sync/rebase logic.

No functional `sync-splits`/`update-history-master`/`rebase-branches-to-master` code exists anywhere in the repo. Phase 2 is starting from a clean slate on this front, consistent with plan 026's stated deferral.

## 7. Summary of Phase 1 plan (`ai/°base/plans/026_git-branch-split-foundation-phase-1-classification-push-prot.md`)

The plan implements only the foundation of a larger three-branch-variant design (clean/`ai/UNCLEAN/{branch}`/`ai/history/{branch}`), explicitly deferring the hardest parts — `sync-splits`, `update-history-master` (with `base/base` merge recreation), `rebase-branches-to-master`, and the git-trailer-based commit-correlation mechanism — to a follow-up plan (this is the plan Phase 2 continues). It confirms concrete rules for AI/base-content path classification (`ai/**`, `.claude/**`, `.codex/**`, exact files, any `°base` path segment) and three commit predicates (`is_ai_only_commit`, `is_ai_tainted_commit`, `is_code_containing_commit`) plus a push content-policy matrix (clean blocks AI-tainted commits, history blocks code-containing commits, unclean allows both) and a name policy blocking `unclean`/`history` pushes to a remote named `origin`. It specifies a new `scripts/°base/git/°split_lib/` package (mirroring the `ai/settings/°settings_lib` shim pattern) split into `branches.py`, `classify.py`, `push_checks.py` (pure logic), `git_ops.py` (subprocess glue), and `cli.py` (argparse), plus a hand-rolled `.git/hooks/pre-push` trampoline installed alongside pre-commit's hooks — deliberately bypassing pre-commit's own `pre-push` stage because `pre_commit`'s `hook_impl.py` only reads the first ref-update line and redirects stdin to `/dev/null`, making it unable to see multi-ref pushes. It lists new test files (`test_git_split_branches.py`, `test_git_split_classify.py`, `test_git_split_push_checks.py`) using stdlib `unittest` with dynamic imports, matching existing test conventions. Verification is manual/scripted: run the new unit tests, confirm the installer produces a working executable pre-push trampoline that still invokes `git lfs pre-push` first, and manually exercise push blocking/allowing scenarios in a scratch repo.