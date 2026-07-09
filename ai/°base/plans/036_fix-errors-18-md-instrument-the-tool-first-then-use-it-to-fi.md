# Fix errors/18.md: instrument the tool first, then use it to finish the stuck ssp split

## Context

`ai/°base/errors/18.md` captured a run of `split.py update-history-master --yes`
against the external `ssp` repo (`/home/user/Documents/PycharmProjects/abelmann/hansecom/ssp`)
that hit a cherry-pick conflict on commit `7afd08be7fc178abd1744706c781b583ce6f69d9`
and printed nothing but a bare `{'status': 'conflict', 'pending': {...}}` dict —
no explanation, no next steps, sandwiched between two giant unconditional ref
tables. The user wants: (1) the tool instrumented with much better logging
first, (2) then use that instrumented tool to actually finish the stuck `ssp`
split, learning from whatever that run exposes.

Live investigation of `ssp` (read-only so far) turned up more than the
original transcript showed:

- **The persisted state is stale.** `.git/BASE_SPLIT_HISTORY_MASTER_STATE`
  still records `pending: {cherry-pick, 7afd08be...}`, but `git status` shows
  no unmerged paths and there's no `.git/CHERRY_PICK_HEAD` — the real
  cherry-pick was already aborted outside the tool at some point, and the
  state file was never told. Currently on branch `_base_split_scratch` at tip
  `8ca28982` (clean, matches the state file's `"tip"` field), state's
  `"remaining"` list still starts at `7afd08be`.
- `--abort` is safe to run despite this: `git_ops.cherry_pick_abort`/
  `merge_abort` (`git_ops.py:246-247`, `260-261`) call `subprocess.run(...)`
  with no `check=True` and discard the result — a failed "nothing to abort"
  is silently swallowed. `_do_abort` (`history_master.py:529-541`) then
  cleans up the scratch branch and clears the state file unconditionally.
- Root cause of the original unhelpful output, traced through
  `scripts/°base/git/°split_lib/`:
  - `cli.py:_run_with_recovery` (lines 24-51) unconditionally prints the full
    `recovery.format_recovery_entry(...)` block *before* anything runs, and
    unconditionally prints `format_after_summary(...)` in a `finally` —
    regardless of outcome. That's the "spam."
  - `history_master.py:_run_steps` (426-437) catches `CherryPickConflict`/
    `MergeConflict` with a bare `except ...:` (no `as exc`), discarding the
    human-readable message those exceptions already carry. `_do_continue` has
    the same pattern at lines 559-560, 565-566, 586-596.
  - Nothing narrates progress while steps run, and no per-git-command detail
    is captured anywhere — silence until the final dict.
  - `cli.py:_update_history_master` (137-154) just does `print(result)` with
    no special-casing for `status == "conflict"`.
  - Nothing cross-checks the persisted state file against real git state
    (`CHERRY_PICK_HEAD`/`MERGE_HEAD`) before trusting it on `--continue`.

No test pins the current dict shape or CLI stdout format (confirmed via
`scripts/°base/tests/test_git_split_history_master.py` and `test_get_base.py`),
so all of this is safe to change. Per this repo's convention, `18.md` itself
is never edited — add a fresh `18.expected.md` once the fix is in.

## Part A — instrument the tool (do this first)

Goal: when we then point this at `ssp`'s ~600-commit replay, we get maximum
diagnostic insight into *everything* it does — without the console turning
back into a wall of noise. Split by verbosity, not by omission: nothing gets
left out, it just goes to the right place.

### 1. Real narration via `logging` (first use of `logging` in this repo's scripts)

Deliberate deviation from the rest of `scripts/°base` (which is `print()`-only)
because narration + simultaneous console/file output at different detail
levels is exactly what `logging` is for. Stays confined to `cli.py`;
`history_master.py` remains a pure library module taking a plain
`log: Callable[[str], None]` / a small `Reporter` shim — no `logging` import
there, so its tests stay logging-free.

Per-invocation setup in `cli.py`:
- `StreamHandler(sys.stdout)` at `INFO` — the readable console stream: one
  line per step ("[12/643] cherry-pick 7afd08be \"VGN-789: Timeline: CR-001...\"" → "ok"/"CONFLICT"),
  plus section headers and the final result block.
- `FileHandler(repo_root / recovery.RECOVERY_FILENAME)` at `DEBUG` — everything
  INFO has, *plus*: every underlying git command run (`git_ops` already
  shells out via `subprocess.run`; wrap/log each call's argv, cwd, returncode,
  and stdout/stderr — see #2), step timing, and the full ref
  snapshot/rollback tables from `recovery.py` (currently printed unconditionally
  to stdout; move to file-only at DEBUG).
- Both handlers use `Formatter("%(message)s")` — no level/time noise, reads
  like plain text/markdown, consistent with the existing `.rebase-recovery.tmp`
  style.
- Clear handlers in a `finally` so repeated in-process invocations (tests)
  don't accumulate them.

### 2. Log every git invocation, not just step outcomes

`git_ops.py`'s subprocess wrappers (`cherry_pick`, `cherry_pick_continue`,
`cherry_pick_abort`, `merge_abort`, etc.) currently run silently. Add a thin
`log: Callable[[str], None] | None = None` passthrough (or a module-level
logger set once by `cli.py`) so each call emits one DEBUG line: the argv, and
on non-zero returncode the stdout/stderr too. This is the difference between
"a conflict happened somewhere" and being able to reconstruct exactly what
git said, after the fact, from the log file alone — important for a
600-commit run we don't want to have to reproduce.

### 3. Thread the swallowed exception messages through

- `_run_steps` (426-437): bind `as exc` for both `CherryPickConflict` and
  `MergeConflict`, add `"message": str(exc)` to the returned conflict dict.
- `_do_continue`'s `except MergeConflict:` (586-596): same fix.
- The two ad-hoc `"detail"` conflict dicts at lines 559-560, 565-566 already
  carry a message string under `"detail"` — leave as-is, CLI checks both keys.

### 4. Validate persisted state against real git state before trusting it

In `update_history_master` / `_do_continue` (`history_master.py`), before
acting on a `pending: cherry-pick` state, check whether
`(repo_root / ".git" / "CHERRY_PICK_HEAD")` (or the merge equivalent)
actually exists. If the state file claims a pending conflict but git shows
none — exactly the situation found in `ssp` right now — don't blindly call
`cherry_pick_continue` (which would just fail confusingly): log it clearly
and either auto-clear the orphaned state (treating it as already resolved,
since the remaining plan can just be recomputed) or point the user at
`--abort`. Prefer surfacing this loudly over guessing silently.

### 5. `cli.py:_update_history_master` — sectioned, narrated output

Pass the logger into `update_history_master(...)`. On `status == "ok"`: one
concise `logger.info(...)` line. On `status == "conflict"`, `logger.warning(...)`
a clearly labeled block on top of the DEBUG-level detail already logged:
```
== CONFLICT ==
<pending message/detail text>

Choose one:

  [1] Resolve and continue
      - Resolve the conflict in the working tree (branch `_base_split_scratch`)
      - git add <resolved files>
      - scripts/°base/git/split.py --repo-root <repo_root> update-history-master --continue

  [2] Abort this run (keeps any already-pulled `master`)
      - scripts/°base/git/split.py --repo-root <repo_root> update-history-master --abort

  [3] Full manual rollback (only if [2] isn't enough, e.g. to also undo
      the `master` pull) -- see the ref table and `git update-ref` commands
      in .rebase-recovery.tmp for this run.
```
Other statuses (`aborted`, `dry_run`) keep their current simple output.

### Test coverage for Part A

- `test_git_split_history_master.py`: a test triggering a real cherry-pick
  conflict asserting `result["pending"]["message"]` contains the "--continue"
  text; a test for the stale-state detection (#4) — manually delete
  `CHERRY_PICK_HEAD` under a fake pending state, confirm the tool doesn't
  attempt a doomed `cherry_pick_continue`.
- A CLI-level test asserting: console (INFO) output for a conflict mentions
  both `--continue` and `--abort` but *not* the full ref table; the log file
  (DEBUG) contains the full ref table *and* at least one logged git argv line.

Add `ai/°base/errors/18.expected.md` showing the corrected, narrated console
output for this same scenario, matching the `N.md`/`N.expected.md` convention.

## Part B — fix `ssp` for real (manually and/or via the now-instrumented script)

Executed directly in `/home/user/Documents/PycharmProjects/abelmann/hansecom/ssp`
(outside this session's own repo — every mutating step called out and
checked before moving on). Keep notes as this happens — anything confusing,
surprising, or manual-intervention-requiring is exactly Part C's input.

1. `python3 <base>/scripts/°base/git/split.py --repo-root <ssp> update-history-master --abort`
   — clears the stale state file and `_base_split_scratch` branch.
2. Re-run `update-history-master --yes` with the new logging in place. Watch
   the terse console narration live; the full per-commit/per-git-call detail
   lands in `.rebase-recovery.tmp` for later reference. Expect it to reach
   the same conflict at `7afd08be` (nothing about that commit's content has
   changed) after sailing through everything before it.
3. At the conflict: inspect the conflicting files under
   `.claude/VGN-789/cr-001_timeline.*` and
   `api/application/applications/166/index.json` — read both sides and
   resolve mechanically (standard cherry-pick reconciliation: adopt the
   incoming commit's changes, adapted to whatever already changed
   underneath), `git add`, then `update-history-master --continue`. If the
   script's own recovery path doesn't cleanly get us there, resolve directly
   with plain git commands instead of fighting the tool — that mismatch is
   itself a Part C finding.
4. Repeat for any further conflicts. If a resolution isn't mechanical — needs
   a business-logic judgment call about the SSP application rather than
   "reconcile two diffs" — stop and ask rather than guess.
5. Once `status: ok` (or manually confirmed equivalent), verify:
   `ai/history/master` moved to a real new tip, `master` reflects the
   already-happened pull, and nothing else regressed versus the `errors/18.md`
   "before" table.
6. Leave the pile of pre-existing untracked debris in the worktree
   (`branches.*.txt`, `gitblah-0jep`, `notes/`, `openapi/`, etc.) untouched —
   unrelated to this task.

## Part C — feed what Part B taught us back into the script

A dedicated pass, after `ssp` is actually unstuck — not folded silently into
Part B. Go through the notes from Part B and turn each rough edge into a
concrete fix, for example (fill in for real once Part B has actually run):

- Any point where the log output (even the new instrumented version) still
  didn't say enough, or said too much.
- Any point where `--continue`/`--abort` didn't behave as documented, or a
  manual git command was needed instead of the tool.
- Any additional stale-state-like mismatches beyond the one already found
  (§Part A.4) between what the state file believes and what git actually
  shows.
- Whether the conflict-resolution step itself suggests the tool could detect
  or avoid this specific class of conflict earlier (e.g. duplicate content
  already present on the target line).

Each finding gets its own small, targeted fix + test — not a speculative
rewrite. If Part B turns up nothing beyond what Part A already fixed, say so
explicitly rather than inventing changes to fill this section.

## Verification

- Re-run the full `scripts/°base` test suite after the Part A code changes,
  before touching `ssp`, and again after any Part C fixes.
- Confirm the `ssp` run in Part B produces readable console output
  end-to-end, with the full detail recoverable from `.rebase-recovery.tmp`.
