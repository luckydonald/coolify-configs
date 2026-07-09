I now have full context. Here is the report.

## 1. `ai/°base/AGENTS.md` (symlink → `CLAUDE.md`)

Full contents at `/home/user/git/luckydonald/base/ai/°base/AGENTS.md` (lines 1–118, mirrors root `CLAUDE.md`):

- **L5-7**: This repo is "a reusable git base that consuming projects adopt via checkout, rebase, or merge from `base/base`." Everything base-specific lives under a `°base` namespace to avoid colliding with consuming-repo files.
- **L9-29**: Commands — run tests via `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v`; sync AI tool settings via `python3 scripts/°base/ai/settings/sync.py` (and `--check` for the pre-commit gate).
- **L33-49**: Directory layout table — key rows: `scripts/°base/` (all helpers), `ai/°base/` (AI artifacts for the base repo itself: queries, plans, memory), `ai/skills/*/SKILL.md` (canonical skill source), `ai/tool-settings/settings.json` (tracked, tool-neutral settings shared by Claude+Codex), `.claude/settings.json`/`.codex/rules/generated.rules`/`.codex/config.toml`/`.mcp.json` (all generated — do not hand-edit, though some are folded back on next sync).
- **L51-92**: Detailed description of the settings/skills sync machinery (`sync.py`) — versioned schema (`v2`), structured permission entries (`{"type": "bash", ...}` etc.), MCP server/tool snippet composition, bidirectional Codex `.rules`/`config.toml` parsing, plugin key restructuring, and skill symlinking (`.claude/skills/<slug>` and `.agents/skills/<slug>` → canonical `ai/skills/<slug>/SKILL.md`).
- **L94-106**: AI artifact routing — `scripts/°base/ai/hooks/_lib.py`'s `resolve_log_path()` routes hook output depending on whether Claude is running inside the base repo itself vs. a consuming repo (detected via directory name `base` **and** origin URL matching `luckydonald/base`). Hook table:
  - `save-prompt/hook.py` (UserPromptSubmit) → `ai/°base/query.md`
  - `save-decision/hook.py` (AskUserQuestion post) → `ai/°base/query.md`
  - `save-plan/hook.py` (Write, ExitPlanMode, Stop) → `ai/°base/plans/NNN_*.md`
  - `record-memory/hook.py` (Write, Edit, SessionStart) → syncs memory hardlinks
- **L107-110**: Pre-commit hooks — `reject_co_authored_by.py` (blocks `Co-Authored-By:` in commit msgs) and `require_memory_delete_marker.py` (requires explicit marker on memory-file deletion).
- **L112-118**: Commit format: `[base] topic: ai: Run: Short summary.` — `[base]` is a where-tag, `topic` is a subsystem name (not a Conventional-Commit type).

**Important for the branch-splitting design**: this file itself explicitly documents that this repo is designed to be merged/rebased into consuming repos, and that AI-vs-code separation by directory/file convention already partially exists (`ai/°base/`, `scripts/°base/`, generated vs. hand-edited files) — this is exactly the boundary a clean/unclean/history split tool needs to know how to draw.

## 2. `ai/°base/todo.md` (current, uncommitted-modified state)

`/home/user/git/luckydonald/base/ai/°base/todo.md` — a running prompt log of completed and pending asks, each item terminated by `- [x] Done` or `- [ ] Done`. Most items are already done (explore-command logging, debug-dir routing, task-notification formatting, prompt-recovery-while-running fix, query.md modernization, MCP+envmcp sync support).

**The final entry (lines 59–163) is unfinished (`- [ ] Done` at line 163)** and is the actual spec for the branch-splitting tool this task is about designing. Key points verbatim/paraphrased:

- **Branch types** (lines 64-68):
  | type | name format | purpose |
  |---|---|---|
  | clean | `{branch}` | no base/AI mentions; safe to release publicly |
  | unclean | `ai/UNCLEAN/{branch}` | the actual working branch — commits AI, code, or mixed freely |
  | history | `ai/history/{branch}` | leftovers after stripping code from unclean; keeps AI instructions (e.g. `CLAUDE.md`) reusable, plus sync metadata |
- **`ai/history/master`** (lines 71-73): holds AI history after a clean variant merges to main; becomes base for next unclean/history; main branch becomes base for next clean branch.
- **`update-history-master` subcommand** (lines 74-104):
  - Master history commit ordering: `master` commits first, then `base` (merge commits of `base/base` into `ai/history/master`), then `history` (merged history branches), then `merge` (empty marker commits referencing the clean-branch merge point).
  - Difficulty: after clean `master` updates, `ai/history/master` needs rebasing onto it while preserving track of already-merged clean branches with history/unclean variants (to cherry-pick).
  - Open question posed by the user: how to gracefully support merging/updating `base` itself given the rebase-and-flatten strategy.
  - `--force-merge=<branch>` (repeatable) option to force manual history-branch merge before inclusion in clean master.
  - Rough steps: checkout `master` → check up-to-date (ask to pull, default N) → checkout `base` → check up-to-date (ask to pull, default N) → checkout `ai/history/master` → rebase onto `master` → merge most recent `base/base` into `ai/history/master`.
  - Merge-vs-rebase problem: proposed approach is to manually rebase commit-by-commit, and when a `base/base` merge commit is hit, recreate it via a fresh merge (reapplying the old conflict resolution) rather than rebasing it.
- **`sync-splits` subcommand** (lines 105-138):
  - **Generating clean**: take unclean, strip AI content, drop AI-only commits entirely. Clean branch starts on `master`.
  - **Generating history**: take unclean, strip code content but *keep* commits even if empty; add metadata (in commits or dedicated metadata commits) to allow syncing clean+history back to unclean. History branch starts on `ai/history/master`.
  - **Generating unclean** (hardest): start from the specific `ai/history/master` commit the history branch is based on, then cherry-pick commits from clean and history in order. Three commit categories to reconcile: (1) code-only (exists only in clean, e.g. a quick hotfix without AI), (2) history-only (exists only in history, e.g. a `CLAUDE.md` update with no code), (3) code+history (exists in both, mergeable into one commit, e.g. prompt update + its implementation). Unclean branch starts on `ai/history/master`.
- **`rebase-branches-to-master` subcommand** (lines 140-150): rebases all three branch types onto their respective current masters — clean→clean master, history→`ai/history/master`, unclean→the just-rebased history branch. Must also handle the case where one or two of the three branches are missing.
- **Additional required safety checks** (lines 155-162):
  1. Branch push **name** check: block pushing branches named in unclean or history format to a remote called `origin`.
  2. Branch push **content** check: (a) block AI-containing commits from being pushed unless branch name is unclean-format; (b) block code-containing commits from being pushed unless branch name is history-format.

Note: `ai/°base/query.md` (line ~2449, tail) contains this exact same prompt verbatim (it's the raw prompt-log mirror of the same `/plan` request) — no additional detail beyond todo.md.

Also of note: the current git branch is `feature/unclean-ai-split` (confirmed via `git status`), and there are three already-made commits on it: `d2a8e00`, `e1529a7`, `4faf2ac`, all titled `[base] ai-split: Initial plan v0.0.0[...]`, which are simply incremental edits to `todo.md` building up this same spec (no code yet).

## 3. `ai/°base/decisions/`

Directory listing: `001_reverse_scope.json`, `002_reverse_scope.json` (both untracked, per `git status`).

**These are NOT related to the branch-splitting tool.** Both are `AskUserQuestion`-style decision-log JSON files about an unrelated feature: a "reverse-order display" toggle (likely for AllMyStorage, given the "settings already have a server sync api" note), with the single question `reverse_scope`: "Should the reverse-order setting be user-wide display preference or saved per container?" — three options (User-wide/Recommended, Per container, Browser only).

- `001_reverse_scope.json`: `answers: []` (unanswered draft), `autoResolutionMs: 60000`.
- `002_reverse_scope.json`: same question, but `answers` now populated: `{"reverse_scope": {"answers": ["User-wide (Recommended)", "user_note: As the settings already have a server sync api, yes, please."]}}`.

These appear to be artifacts of the `save-decision` hook (per AGENTS.md's hook table) from a separate, unrelated task running concurrently/recently in this session/repo — likely stray/misrouted output, not part of the branch-splitting design. Worth flagging to the user that they may be noise rather than "recent relevant context."

## 4. Errors 16/17 + `rebase_strip_claude_authorship.py`

- **`ai/°base/errors/16.txt`**: Shows a failure in the `AllMyStorage` project (which uses this base) running `./scripts/°base/git/rebase_strip_claude_authorship.py`: the script rebases, and its `--exec` callback re-invokes itself via `python3 <same relative path>`, but that path doesn't exist inside the rebase's transient working tree state, producing `FileNotFoundError` → `CalledProcessError`.
- **`ai/°base/errors/17.txt`**: Same script, different failure — a real rebase conflict in `ai/query.md` (merge conflict merging in a commit titled "Added a script to rebase onto origin/mane and rewrite claude[bot] authorship"), leaving the rebase interrupted mid-flight with a similar traceback/exit code.

These errors are almost certainly why the current script (read at `/home/user/git/luckydonald/base/scripts/°base/git/rebase_strip_claude_authorship.py`) now copies itself to a tempdir before re-invoking (see below) — i.e., 16/17 already prompted a fix for the self-referential `--exec` bug, though the underlying "rebase conflicts on `ai/query.md`" problem (error 17) is structurally the same class of problem the new clean/unclean/history split tool needs to solve robustly (AI-artifact files causing conflicts during rebase/merge across branch variants).

**`scripts/°base/git/rebase_strip_claude_authorship.py` (full, 86 lines)** — summary:
- Purpose: rebase the current branch onto its merge-base with `origin/mane` (hardcoded upstream), rewriting any `claude[bot]` author/committer identity (email `41898282+claude[bot]@users.noreply.github.com`) to a fixed human identity (`✨❯ Lucky Lucy <claude._.ai._.code@luckydonald.de>`) along the way.
- `main()`: if invoked with `--amend-step`, just calls `amend_step()` and exits (this is the internal callback). Otherwise: `git fetch origin mane`; compute `git merge-base HEAD origin/mane`; **copy itself into a fresh tempdir** (`tempfile.mkdtemp(prefix="rebase-strip-claude-")`) so the `--exec` callback has a stable path independent of what the rebase does to the working tree; run `git rebase <merge_base> --exec "<python> <tmp_copy> --amend-step"`. On failure, prints recovery instructions (`git rebase --continue`/`--abort`) and leaves the tmp copy on disk for the exec step to keep working; on success, cleans up the tempdir.
- `amend_step()`: checks HEAD's author/committer email; if either matches the claude[bot] email, runs `git commit --amend --no-edit --author "<NEW_AUTHOR>"` with `GIT_COMMITTER_NAME`/`GIT_COMMITTER_EMAIL` env overrides — i.e., strips bot authorship per-commit during the rebase, commit by commit.
- Test coverage: `/home/user/git/luckydonald/base/scripts/°base/tests/test_rebase_strip_claude_authorship.py` mocks `subprocess.run` to assert the rebase uses a **copy** of the script (not the original path) as the `--exec` target, and that recovery messaging fires on rebase failure without swallowing the traceback.

**Relevance to the new tool**: this script is a narrow, single-purpose precursor to the clean/unclean/history split — it already solves "rewrite git history author identity for AI commits during a rebase via a self-relocating `--exec` step," and already hit the exact category of problem (conflicts in AI-artifact files like `ai/query.md` during a rebase across AI/non-AI commit boundaries) that the full split tool will need a systematic answer for. The `--exec`-copies-itself-to-tempdir trick is a reusable pattern worth carrying into the new tool's rebase machinery.

## 5. Other `ai/°base/` docs relevant to conventions / subproject linking / branch strategy

Directory tree (top-level dirs under `ai/°base/`): `by-feature/`, `plans/` (25 plan files, numbered), `memory/`, `errors/` (17 numbered error reports), `decisions/`, `output/` (agent + debug logs).

- **`ai/°base/memory/MEMORY.md`**: single index entry — `[lplp: never drop ai: auto-commits](feedback_lplp_never_drop_ai_autocommits.md)` — "fold every chained `ai:` auto-commit into the new code commit; never `git reset --hard` past them, even smoke-test ones." Directly relevant: the branch-split tool's "drop AI-only commits" logic for generating **clean** must not literally discard commit content via destructive git ops — this memory rule signals a strong repo norm against losing `ai:` commits, which should inform how "drop ai-only commits" is implemented (likely: exclude from clean, but always preserve in history/unclean, never hard-delete).

- **`scripts/°base/init/link-subproject-claude.sh`** (150 lines) — this is the "dumper/init script" referenced in the recent commit `4d00ee5 [base] [dumper] init script: ... Extended subproject linking to .codex, ai/tool-settings, .mcp.json, and AGENTS.md`. Full behavior:
  - Purpose: idempotent per-subfolder setup for monorepos where `base` is merged at the repo root but Claude/Codex is also launched from inside a subfolder. Creates **relative symlinks** at `<subfolder>/.claude`, `.codex`, `ai/tool-settings`, `.mcp.json` pointing at the monorepo-root counterparts, plus an `AGENTS.md → CLAUDE.md` symlink (matching root layout).
  - `link_shared(rel)`: generic — resolves `git_root/<rel>` vs `sub_dir/<rel>`; if target is already the correct symlink, no-op; if it's a wrong symlink or a real file, backs it up first (`{stem}.YYYY-MM-DD_HH-MM-SS.bak{ext}`, via `git mv` if tracked, else plain `mv`); then creates the relative symlink and `git add`s it.
  - `link_agents_claude()`: mirrors root convention — if `AGENTS.md` is already a correct symlink to `CLAUDE.md`, no-op; if it's a real file and `CLAUDE.md` doesn't yet exist, moves it (`git mv` if tracked) to `CLAUDE.md`; then symlinks `AGENTS.md → CLAUDE.md`. Backs up conflicts the same way.
  - Explicitly Linux/Mac only (no Windows support, confirmed by a query.md prompt: "All Linux/Mac, no Windows checkouts.").
  - Called manually: `cd monorepo/some_project && ../scripts/°base/init/link-subproject-claude.sh`.
  - **Relevance to the split tool**: this establishes the existing pattern for how "base infra files" (settings, hooks, MCP config, AGENTS.md) are distinguished from "project content" via symlink-vs-real-file and a `.bak` backup convention — the same distinction (base/AI infra vs. product code) the clean/unclean/history split needs to draw, just at the git-history level instead of the filesystem level.

- **`scripts/°base/init/checkout.sh`** — one-shot repo setup (installs pre-commit + git-lfs hooks, removes stale yorkie hooks, ensures `base`/`empty` git remotes exist, patches hook PATH for IDE/Xcode git, calls `fix_username.py --fix-lfs-locks-only`). Confirms the repo maintains required remotes named `base` (pointing to `github.com/luckydonald/base.git`) — i.e., the upstream `base/base` remote referenced throughout `todo.md`'s spec (e.g., "merge the most recent `base/base` into `ai/history/master`") is a real, already-configured remote convention in this repo, not a hypothetical.

- **Commit-convention docs**: no separate "commit conventions" doc beyond what's in `AGENTS.md` (`[base] topic: ai: Run: Short summary.` format, `[dumper]`/`[tunnel2tunnel]`/`[AllMyStorage]` where-tags seen in `git log` are evidently other where-tags for other consuming/sub-projects using the same base). Files matching "subproject/dumper/init/linking" grep hits were mostly plan files (`003`, `007`, `010`, `012`, `014`) and `by-feature/git_remote_fix/{init,query}.md` — these are about the `git_remote_fix` feature (unrelated remote-fixing script), not additional branch-strategy docs; no further branch-strategy documentation exists beyond the `todo.md` spec itself.

## Summary of what feeds the new tool's design

1. `todo.md` (§159-163 above) is the authoritative, still-open spec for exactly this tool — treat it as the primary requirements doc.
2. `rebase_strip_claude_authorship.py` is a working precedent for self-relocating `--exec` rebase scripts and already demonstrates the AI-artifact-file-conflict problem (errors 16/17) the split tool must handle systemically, not just for `ai/query.md` alone.
3. `link-subproject-claude.sh` + `checkout.sh` establish existing repo conventions (symlink-based infra separation, backup-on-conflict naming, required `base`/`empty` remotes) that a clean/unclean/history tool should reuse/extend rather than reinvent.
4. `MEMORY.md`'s "never drop `ai:` commits" rule constrains how "drop AI-only commits" for the clean branch must be implemented (exclude, don't destroy).
5. The `decisions/001-002_reverse_scope.json` files are unrelated noise from a different (AllMyStorage) task and should not inform this design.