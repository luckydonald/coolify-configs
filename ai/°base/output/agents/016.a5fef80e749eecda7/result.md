# Investigation Report

## 1. `ai/°base/AGENTS.md` — guidance on `errors/` and recovery

I read the file in full (`/home/user/git/luckydonald/base/ai/°base/AGENTS.md`, ~85 lines). It documents:
- What the repo is (a reusable git base namespaced under `°base`)
- Test/sync commands
- Directory layout table, settings/skills sync internals, AI artifact routing (hooks → `ai/°base/query.md`, `ai/°base/plans/`, etc.)
- Pre-commit hooks and commit message format

**Important finding: `AGENTS.md` does NOT mention the `errors/` directory at all**, and it contains **no documented recovery procedure** for `split.py update-history-master` conflicts. Neither is there any archiving/"mark resolved" convention documented anywhere for `ai/°base/errors/*.md`.

Based on cross-referencing `ai/°base/todo.md` and `ai/°base/query.md`, the actual convention (undocumented in AGENTS.md, but consistently followed in practice) is:
- Error transcripts get pasted into `ai/°base/errors/N.md` (or `.txt`/`.diff`) when something goes wrong, referenced from `query.md`/`todo.md` as `@ai/°base/errors/N.md`, asking Claude to "fix" it.
- "Fixing" an error file means: fixing the underlying script/code that caused the failure, and often adding an accompanying `N.expected.md` fixture used in a unit test (e.g. `10.md`/`10.expected.md`, `12.md`/`12.expected.md`, `15.md`/`15.expected.md`).
- **Error files are never deleted or moved** — I confirmed via `git log --diff-filter=D -- ai/°base/errors/` that **zero** files have ever been deleted from that directory across 41 commits touching it. They stay in place permanently as regression-test fixtures/history.

## 2. Pattern of past error fixes / commit `15b9685`

`git show 15b9685` ("[base] ai: Unused error files.") is misleading by name — it does **not** delete/archive/fix anything. It simply **adds** two previously-untracked transcript files (`16.txt`, `17.txt`) to the repo (44 lines added, 0 removed). These are leftover local transcripts from an earlier `rebase_strip_claude_authorship.py` failure (stale relocated script path, then an unhandled merge conflict on `ai/query.md`) that got committed later once picked up ("unused" = untracked/orphaned at the time).

Confirming the "fix" pattern from `query.md`/`todo.md` cross-references:
- `errors/1.md`, `2.md`, `3.md` — fixed inline in code (commit message trailer, uv/git-lfs path issues), files kept as reference, never removed.
- `errors/6.txt`, `7.txt` — fixed by making a path absolute; a follow-up asked to "Create a script for that into the init script dir".
- `errors/16.txt`/`17.txt` — explicitly called out in the *design comment* of `history_master.py` (line 3): "No `git rebase --exec` (see ai/°base/errors/16.txt, 17.txt for the two real failure classes...)" — i.e. these errors directly drove the architecture of the very script relevant to point 3/4 below (a Python-driven cherry-pick loop instead of `git rebase --exec`).
- `errors/18.md` (the current one) is referenced live in `ai/°base/query.md:3071`: `❯ /plan Fix @ai/°base/errors/18.md (also instruct me how to recover the old branches).` — this is the exact task you're investigating.

**Conclusion: there is no "mark resolved"/archival step. Error files are permanent build/log artifacts; "fixing" one means fixing the code and leaving the transcript in place (often paired with a new `.expected.md` test fixture).**

## 3. `scripts/°base/git/split.py` and `update-history-master`

Entry point `split.py` (`/home/user/git/luckydonald/base/scripts/°base/git/split.py`) is a thin shim importing `°split_lib.cli.main`. Actual logic lives in `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/`:

- `cli.py` — argparse wiring. The `update-history-master` subparser (lines 213–220) defines `--force-merge`, `--pull-master`, `--pull-base`, `--yes`, **`--continue`** (dest `continue_`), **`--abort`**, `--dry-run`. So **yes, there is a documented `--continue`/`--abort` flag pair**, mirroring git's own conflict UX.
- `history_master.py` — the actual implementation (28KB). Key points:

**Design rationale (top-of-file docstring, lines 1–13):** explicitly avoids `git rebase --exec` because of the two real failures logged in `ai/°base/errors/16.txt` and `17.txt` (a self-relocated script path, and an unhandled `ai/query.md` merge conflict). Instead it drives a plain Python loop using `git_ops.cherry_pick` / `cherry_pick_continue` / `cherry_pick_abort` per ordinary commit, plus an explicit merge-recreation procedure for "base-merges."

**State/resume mechanism:**
- `STATE_FILENAME = "BASE_SPLIT_HISTORY_MASTER_STATE"`, stored at `<repo_root>/.git/BASE_SPLIT_HISTORY_MASTER_STATE` (`_state_path`, line 219-220) as JSON (`remaining` steps, `tip`, `force_merge`, `original_sha`, `pending`).
- On conflict, `update_history_master()` (and `_do_continue()`) writes this state file and returns `{"status": "conflict", "pending": {...}}` — exactly matching what's in `errors/18.md`.
- `CherryPickConflict` (lines 41-58) and `MergeConflict` (60-72) exception messages both explicitly instruct: *"Resolve the conflict in the working tree (currently checked out on `_base_split_scratch`), `git add` the resolved paths, then rerun update-history-master with `--continue` (or `--abort` to cancel)."*
- `_do_continue()` (lines 544–604): reads the state file, resumes the interrupted cherry-pick via `git_ops.cherry_pick_continue`, keeps processing `remaining` steps, and only clears the state file (`_clear_state`) once everything completes and the `ai/history/<branch>` ref is finally moved via `git_ops.move_ref(...)`.
- `_do_abort()` (lines 529–541): runs `git cherry-pick --abort` or `git merge --abort` depending on the pending kind, cleans up the scratch branch, and clears the state file — leaving the *actual* branch refs untouched (they were never moved yet at conflict time; only the scratch branch `_base_split_scratch` / `refs/heads/_base_split_scratch` is mid-operation).
- Guard at line 630–634: if a state file already exists, a plain (non-`--continue`/`--abort`) invocation refuses to run: *"A previous update-history-master run is mid-conflict. Run with `--continue` to resume, or `--abort` to cancel."*

**So the documented/coded recovery path for the exact conflict in `errors/18.md`** (`{'status': 'conflict', 'pending': {'kind': 'cherry-pick', 'step': {'kind': 'commit', 'sha': '7afd08be...'}}}`) is:
1. `cd` into the target repo (`/home/user/Documents/PycharmProjects/abelmann/hansecom/ssp`).
2. Resolve the conflict in the working tree — currently checked out on the scratch branch `_base_split_scratch`.
3. `git add` the resolved paths.
4. Re-run `scripts/°base/git/split.py update-history-master --continue` (with `--repo-root` as needed).
5. If instead you want to cancel: run the same command with `--abort`.

## 4. The printed `git update-ref` block — informational rollback, not the recovery mechanism

This comes from `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/recovery.py` — a separate, generic "crash-safe recovery logging" layer wrapping **every** mutating `split.py` subcommand (`cli.py`’s `_run_with_recovery`, lines 24–51).

Mechanism:
- Before running anything, `resolve_watched_refs()` (lines 24-47) enumerates every ref an invocation *could* touch: `main_branch`, its `ai/history/*`, and per base-branch: the branch itself, `ai/UNCLEAN/<branch>`, `ai/history/<branch>`, plus the three `refs/base-split/...` namespaced refs (`history-master-fork-point`, `unclean-cursor/clean`, `unclean-cursor/history`).
- `snapshot()` records each ref's current SHA (or `None` if it doesn't exist).
- `format_recovery_entry()` prints/logs (to `.rebase-recovery.tmp` in the repo root) a markdown block containing: a "Branch | Commit before" table, and a fenced `git update-ref` shell script that would fully undo the run — starting with `git rebase --abort || true`, `git cherry-pick --abort || true`, `git merge --abort || true`, then one `git update-ref <ref> <old-sha>` (or `git update-ref -d <ref>` if the ref didn't exist before) per watched ref, restoring it to its pre-run state.
- After the run (success or failure), it also prints a "before | now" comparison table (`format_after_summary`).

**This block is purely informational / a manual rollback recipe** — it is not executed automatically and is unrelated to the `--continue`/`--abort` state-file mechanism in point 3. It exists so that even if the whole process is killed (not just a normal conflict), the user has copy-pasteable commands to fully reset every ref `split.py` might have touched back to how it was **before this invocation ran** — i.e., this is exactly "how to recover the old branches" the user is asking about.

Concretely, for `errors/18.md`, lines 55-106 contain that block: it would reset `master`, `ai/history/master`, all `feature/...` branches, all `ai/UNCLEAN/...` and `ai/history/...` variants, and delete the newly created `refs/base-split/...` refs — restoring the entire repo's ref state to exactly what it was right before the `update-history-master --yes` run. Since the run hit a conflict mid-way (only `master` moved from `a493b18...` to `f6bec55...`, per the before/after table at lines 111-160 — everything else stayed put), running that block would be a full manual undo if the user prefers to abandon the whole run rather than resolve-and-`--continue`.

**No dedicated README documents the `refs/base-split/...` namespace** — I found no doc file explaining `history-master-fork-point` / `unclean-cursor/clean` / `unclean-cursor/history` beyond their usage in code (`branches.py`, `sync_unclean.py`) and this `recovery.py` snapshot list. The design plan at `/home/user/git/luckydonald/base/ai/°base/plans/027_git-branch-split-phase-2-sync-splits-unclean-reconstruction.md` is the closest thing to documentation of the overall `update-history-master` architecture (line 36 explains the no-`--exec` rationale and the `--continue`/`--abort` design intent, quoted above).

### Bottom line for answering the user's actual question ("Fix errors/18.md, also instruct me how to recover the old branches")
Two independent, complementary recovery paths exist:
1. **Resume the tool properly** (preferred if you want the split/history-master to actually complete): resolve the conflicting file(s) on the checked-out `_base_split_scratch` branch, `git add` them, then run `scripts/°base/git/split.py --repo-root <repo> update-history-master --continue`.
2. **Fully roll back to pre-run state** (if you'd rather abandon this run entirely and restore everything as if it never happened): run the `git update-ref`/abort block printed in `ai/°base/errors/18.md` lines 55-106 (or a fresh copy of that same content in `.rebase-recovery.tmp` at the target repo's root, which `recovery.py` also wrote there).