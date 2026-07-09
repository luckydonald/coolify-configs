# Git branch-split Phase 2: sync-splits, unclean reconstruction, history-master

## Context

Phase 1 (already implemented, merged as `629b51c`) built the foundation for the clean/unclean/history branch-split feature (`ai/°base/todo.md:59-163`): branch/commit classification (`scripts/°base/git/°split_lib/branches.py`, `classify.py`) and a push-protection hook (`push_checks.py`, `.git/hooks/pre-push`). It deliberately deferred the machinery that actually *generates* and *keeps in sync* the three branch variants — that's this plan.

Three subsystems, each independently complex, all confirmed in scope for this plan:
- **(A) `sync-splits` forward**: `ai/UNCLEAN/{branch}` → `{branch}` (clean) and → `ai/history/{branch}` (history).
- **(B) `sync-splits` reverse ("unclean reconstruction")**: `{branch}` + `ai/history/{branch}` → `ai/UNCLEAN/{branch}`, the direction the user called "the most difficult one."
- **(C) `update-history-master` + `rebase-branches-to-master`**: keeping `ai/history/master` in sync with `master` and `base/base`, and rebasing all three variants onto their current masters.

(B) and (C) both consume trailers/refs that (A) produces — sequencing within implementation should build (A) first, then (B) and (C) can proceed in parallel since they don't depend on each other.

## Confirmed design decisions (from three independent design passes + user sign-off)

- **Trailer schema** (all via `git interpret-trailers`, new shared module `°split_lib/trailers.py`):
  | Trailer | On | Value | Purpose |
  |---|---|---|---|
  | `X-Base-Split-Source` | clean/history commits | unclean sha | which unclean commit this was derived from |
  | `X-Base-Split-Kind` | clean/history commits | `code`\|`history`\|`mixed` | classification of the source commit |
  | `X-Base-Split-Counterpart-Tree` | history commits (when a clean counterpart exists) | clean commit's tree sha | integrity check linking the pair |
  | `X-Base-Split-Clean-Branch` | the merge/squash commit landing in `master` | base branch name | **required** so `update-history-master` can find newly-merged split branches (works for squash merges too, per user decision — replaces subject-parsing, which breaks on squash) |
  | `X-Base-History-Merge-Kind` / `X-Base-History-Merge-Sha` | `ai/history/master` merge commits that fold in `base/base` | `base-merge` / base/base sha at merge time | self-identifying marker so later rebases can detect and recreate this merge without fragile reachability checks |
  | `X-Base-History-Merge-Replayed-From` | added when a base-merge is recreated | old merge commit sha | audit trail |
  | `X-Base-Split-Merge-Marker-For` | empty marker commits on `ai/history/master` | the clean-branch merge commit sha | marks where a branch's replayed history commits end, per the `master`/`base`/`history`/`merge` ordering in the spec |

- **Refs** (namespace `refs/base-split/...`, not under `refs/heads/` so they're unaffected by the existing push-name policy and never accidentally pushed):
  - `refs/base-split/history-master-fork-point/{branch}` — the `ai/history/master` sha a branch's `history` forked from (written once, when `history` is first created).
  - `refs/base-split/unclean-cursor/clean/{branch}` and `.../history/{branch}` — last clean/history sha successfully incorporated into `unclean` during reconstruction.
  - Forward-direction (A) cursor is **not** a ref — it's read directly off the target branch tip's own `X-Base-Split-Source` trailer, since the branch tip is already atomic and authoritative (no second source of truth to drift).

- **Tree-splitting mechanics** (shared module `°split_lib/tree_ops.py`, used by both (A) and (B)): per-commit deltas via `git diff-tree -M -r --no-commit-id --name-status <sha>^ <sha>` (root commits diff against the empty-tree constant), applied onto the *target branch's* evolving tree using a scratch index (`GIT_INDEX_FILE=<tmp>`), never the real working tree/index. Renames are decomposed into an independent delete-of-old-path + add-of-new-path, each filtered separately — this is what correctly handles a rename crossing the AI/code boundary. This mirrors the existing `hash-object -w` + `update-index --cacheinfo` blob-staging idiom already used in `scripts/°base/ai/hooks/_lib.py:180-202`, extended to full tree construction via `write-tree`/`commit-tree`.

- **Commit metadata policy**: author name/email/date always preserved verbatim from the source commit; committer identity is the tool's own bot identity (reuse the `NEW_NAME`/`NEW_EMAIL` "Lucky Lucy" constants already defined in `scripts/°base/git/rebase_strip_claude_authorship.py`, factored into a small shared `identity.py` so both scripts use one definition); committer date is "now." Subject preserved verbatim for (A). For (B)'s code+history merges, prefer clean's subject/body when it differs from history's (clean is externally reviewed), appending any non-boilerplate history content as a trailing paragraph rather than discarding it.

- **`update-history-master` architecture**: no `git rebase --exec` — Phase 1's precedent (`rebase_strip_claude_authorship.py`) already caused two real failures this way (`ai/°base/errors/16.txt`, `17.txt`: a stale self-relocated script path, and an unhandled conflict on `ai/query.md`), and this command needs genuine per-commit branching (is this a base-merge? does it need message-preserving continuation?) that `--exec` can't express. Instead: a manual Python-driven walk using `git cherry-pick` per ordinary commit and an explicit merge-recreation procedure for base-merges (§ below), with its own small resumable state file (`.git/BASE_SPLIT_HISTORY_MASTER_STATE`) and `--continue`/`--abort` flags, mirroring git's own conflict UX.

- **base/base merge-recreation**: detected via the self-identifying `X-Base-History-Merge-*` trailers (not ref-reachability, which is ambiguous and doesn't survive `base/base` moving). Recreation = `git merge --no-commit --no-ff <recorded-base-sha>` against the new rebased tip; if it conflicts on paths that also conflicted originally, resolve *only those paths* by reusing the original merge commit's resolved blob (`git show <old-merge>:<path>`) — never a wholesale tree replace (would clobber unrelated new changes) or a raw patch-apply (undefined base, conflicts unpredictably).

- **Newly-merged-branch detection for `update-history-master`**: scan new `master` commits for the `X-Base-Split-Clean-Branch` trailer (per user's decision — a merge/squash-time process requirement, documented as such, not enforced by git itself). `--force-merge=<branch>` widens the search and bypasses the idempotency skip, for recovering from cases where the trailer was missed.

- **Unclean reconstruction (B) ordering**: matched clean/history commit pairs (same `X-Base-Split-Source`) are ordered by their position in the *original* unclean lineage (ground truth when known); genuinely new commits with no trailer are slotted in relative to the nearest preceding matched commit on their own branch, tiebroken by commit date. If a matched pair's order actually disagrees with the known original order (someone reordered commits on `clean` or `history` directly), reconstruction hard-refuses with a clear diagnostic rather than guessing, requiring `--force` to proceed (falls back to trusting `clean`'s order).

- **Divergence handling (B)**: if `clean`/`history` content for an already-reconciled commit no longer matches what a fresh split would produce (edited during review), default behavior is dry-run: report the divergence and exit non-zero without touching `unclean`. `--allow-diverge-rewrite` opts into the fix: amend the corresponding `unclean` commit in place and replay (rebase) every descendant on top — chosen over "add a fixup commit on top" because a fixup commit would itself look like a brand-new untracked change on the next forward split, compounding drift. This rewrites `unclean` history, which is acceptable since `unclean` is explicitly the disposable/reconstructable variant (Phase 1's push-policy already treats it that way).

- **Missing-branch handling for `rebase-branches-to-master`**: skip + clearly report; never auto-synthesize a missing variant via `sync-splits` as a side effect of a rebase command. `unclean`'s rebase target is `history`'s *just-rebased* tip, so if `history` is missing or its own rebase failed, `unclean`'s rebase is skipped too (real dependency, not independent).

- **Non-interactive pull prompts**: mirror the existing convention in `scripts/°base/git/remote/fix_username.py` (`--fix-remotes`-style flags) — dedicated flags (`--pull-master`, `--pull-base`) both select the action and suppress their own prompt; with no flag, prompt `[y/N]` on a tty, silently skip on non-tty; `--yes` is a blanket override.

## Files to add/change

**`scripts/°base/git/°split_lib/`**:
- `trailers.py` — `read_trailers(sha, cwd) -> dict[str, list[str]]`, `write_trailers(message: str, trailers: dict[str, str]) -> str`, both wrapping `git interpret-trailers --parse` / `--trailer ... --in-place`.
- `tree_ops.py` — `filtered_diff_for_commit(sha, cwd)`, `build_filtered_tree(parent_tree, sha, cwd, *, keep)`, `read_tree_paths(sha, cwd)`, `apply_path_changes(base_tree, changes, cwd)` — the scratch-index (`GIT_INDEX_FILE`) plumbing shared by (A) and (B).
- `identity.py` — shared bot committer identity, factored out of `rebase_strip_claude_authorship.py` (which switches to importing it).
- `sync_splits.py` — (A) forward direction: `ensure_branch_started`, `find_last_synced_source`, `commits_to_replay`, `sync_branch(base_branch, ...)`.
- `sync_unclean.py` — (B) reverse direction: trailer-based bucketing/correlation, ordering sort key, merged-commit construction, divergence detection, `reconstruct_unclean(base_branch, ...)`.
- `history_master.py` — (C) part 1: base-merge detection/recreation, the master/base/history/merge ordered walk, `--force-merge`, resumable state file handling.
- `rebase_to_master.py` — (C) part 2: three-way rebase with missing-branch skip logic.
- `git_ops.py` — extended with plumbing needed by the above: `cherry_pick`, `merge_no_commit`, `commit_tree`, `commit_empty`, `create_branch`, `move_ref`, `is_ancestor`, `rev_list_reverse`, `ls_tree`, `write_tree_from_index`.
- `cli.py` — extended with `sync-splits <branch> --direction={to-clean-history,to-unclean} [--dry-run] [--force] [--allow-diverge-rewrite]`, `update-history-master [--force-merge=BRANCH ...] [--pull-master] [--pull-base] [--yes] [--continue] [--abort] [--dry-run]`, `rebase-branches-to-master [branch] [--yes] [--dry-run]`.

**Tests** (`scripts/°base/tests/`), all on the existing real-temp-git-repo + `unittest` convention:
- `_git_test_helpers.py` — factor out the `git()`/`make_commit()` helpers already duplicated across Phase 1 test files, shared by all new test files.
- `test_git_split_tree_ops.py` — pure/plumbing-level tree-splitting tests, including the boundary-crossing-rename scenarios.
- `test_git_split_sync_splits.py` — (A): pure-code/pure-ai/mixed commits, fresh-branch creation, idempotent incremental re-run, all-ai-only branch (clean created but empty).
- `test_git_split_sync_unclean.py` — (B): code+history merge, code-only/history-only cherry-picks, divergence detection (report-only and `--allow-diverge-rewrite`), idempotent re-run, dangling-trailer fallback, ordering-conflict refusal.
- `test_git_split_history_master.py` — (C) part 1: first-ever run, subsequent run with new master commits, base/base merge-recreation (fake `base` remote), idempotent re-run, `--force-merge` recovery from a missing trailer.
- `test_git_split_rebase_to_master.py` — (C) part 2: each missing-branch combination.

## Explicitly out of scope / known limitations (documented, not silently ignored)

- Symlink/submodule paths crossing the AI/code boundary are expected to work mechanically (same `ls-tree` mode/blob copy) but are untested — flag in code comments, add a test only if/when it becomes a real issue.
- The `X-Base-Split-Clean-Branch` trailer requirement is a *process* requirement (whoever merges a split-participating PR, or a bot, must add it) — not enforced by git itself; `--force-merge` is the manual recovery path when it's missed.

## Verification

1. `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v` — all new + existing tests pass.
2. End-to-end scratch-repo walkthrough per subsystem: (A) create an `ai/UNCLEAN/*` branch with pure-code/pure-ai/mixed commits, run `sync-splits --direction=to-clean-history`, inspect resulting `clean`/`history` trees and trailers directly with `git show`/`git log --format=%B`. (B) run `sync-splits --direction=to-unclean` against that output, confirm the reconstructed `unclean` tip's tree matches the original. (C) seed a fake `base` remote with a merge conflict, run `update-history-master`, confirm the recreated merge and the ordered `master`/`base`/`history`/`merge` commit sequence with `git log --format=%H%x09%B ai/history/master`.
