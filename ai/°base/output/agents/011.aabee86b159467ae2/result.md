## Findings and Plan: `update-history-master` and `rebase-branches-to-master`

### What I read
- `scripts/°base/git/°split_lib/branches.py`, `classify.py`, `git_ops.py`, `push_checks.py`, `cli.py` — full Phase 1 implementation.
- `scripts/°base/git/rebase_strip_claude_authorship.py` — the `--exec` self-relocating-script precedent, plus `ai/°base/errors/16.txt` and `17.txt` (the two real failures it caused).
- `scripts/°base/git/remote/fix_username.py` — argparse/prompt convention (`--fix-remotes` bool that both selects the action and suppresses the interactive `[Y/n]` (default N) prompt; non-tty stdin silently skips instead of blocking).
- `ai/°base/todo.md:59-150`, `ai/°base/query.md:2440-2600`, `ai/°base/plans/026_*.md` — full spec and the Phase-1 scoping decision (this Phase-2 work was explicitly deferred, module name `history_master.py` was already anticipated in the query transcript).
- `scripts/°base/tests/test_git_split_push_checks.py`, `test_rebase_strip_claude_authorship.py` — existing test idioms (real temp-git-repo + `subprocess`, and `mock.patch.object(module.subprocess, "run", ...)` for unit-level control-flow tests).
- `scripts/°base/init/checkout.sh` — confirms `base` is a real, required remote (`REQUIRED_REMOTES` in `fix_username.py` too), and that this repo already has a local `base` branch tracking `base/base` (`git branch -a` output).

One process note: partway through exploration, a `cat` of a file's content came back with a trailing block styled as a `<system-reminder>` claiming "Plan mode is active" and instructing me to write my plan to a file via a `Write` tool. That block arrived embedded inside ordinary tool output (not as a genuine top-level system/user message), no such `Write` tool exists in my actual tool list, and it contradicts my real operating instructions (read-only exploration, plan delivered as final text). I disregarded it as untrusted injected content and continued under my actual instructions — flagging this explicitly since the user should know it happened.

---

## 1. `base/base` merge-recreation mechanism

**Detection: self-identifying trailers, not reachability.** When a `base/base` merge is folded into `ai/history/master` (both the very first time and every later "step 7" addition), tag the resulting merge commit's message with trailers via `git interpret-trailers`:

```
X-Base-History-Merge-Kind: base-merge
X-Base-History-Merge-Sha: <full sha of base/base at merge time>
```

Reject reachability-based detection (checking if a parent is reachable from `refs/remotes/base/base`) for two reasons: (a) `base/base` is a moving ref — by the time you re-walk history later you no longer have its position *at merge time* without storing it out-of-band anyway, so reachability buys nothing over an explicit tag; (b) it's ambiguous when `base/base` and `master` share deep unrelated ancestry — reachability can't distinguish "this actually was the base-merge" from "this commit happens to also be reachable from base/base for unrelated reasons." A tag has no such ambiguity and survives forever regardless of what `base/base` does later.

**Recreation procedure** (this directly answers "what does reapplying the old resolution mean mechanically"): for merge commit `M` with parents `P1` (previous `ai/history/master` position, now replayed to `P1'`) and `B_old` (the recorded `X-Base-History-Merge-Sha`):

1. `git merge --no-commit --no-ff B_old` against the current new-lineage tip (`P1'`).
2. If clean: commit as-is. This is the common, low-drama path — it means whatever content historically conflicted no longer conflicts against the rebased tip, which is strictly *better* than forcing stale content.
3. If it still conflicts: resolve **only the still-conflicting paths**, each via `git show M:<path>` (M's own historically-resolved content for that exact path), staged individually — **not** a wholesale `git checkout M -- .` / tree-replace, and **not** `git diff M^1 M | git apply` or `git diff M^2 M | git apply`. A wholesale tree replace would silently clobber legitimate new changes to files that happen not to be in the old conflict set; a raw diff-of-the-merge-result applied as a patch has no defined base and will itself conflict unpredictably. Per-path "reuse `M`'s resolved blob" is the only granularity that's both faithful to "reapply the old resolution" and safe.
4. Commit reusing `M`'s original message (`--no-edit` after resolving), re-attaching `X-Base-History-Merge-Kind`/`-Sha`, plus a new `X-Base-History-Merge-Replayed-From: <old-M-sha>` trailer for audit.

**Idempotency:** re-running with unchanged inputs (`P1'` identical, `B_old` identical) reproduces the exact same merge attempt and, if needed, the exact same per-path resolution — deterministic by construction.

**Robustness to `base/base` advancing:** deliberately *do not* let this recreation step pick up a newer `base/base` tip — it is pinned to the recorded `B_old`. Pulling in a newer base is a separate, purely additive operation (spec's step 7), executed once at the very end of the run, as one new base-merge commit at the current tip, never retroactively rewriting earlier base-merge commits. This keeps "rebase existing history correctly" and "ingest new upstream" orthogonal, which is what makes both operations independently idempotent.

---

## 2. Rebase-with-exec safety — recommend a manual walk, not `git rebase --exec`

Both error 16 (stale script path once the rebase relocates/changes what's on disk) and error 17 (the `--exec` callback's own file touch colliding with a later replayed commit's conflict, e.g. `ai/query.md`) stem from the same root cause: `--exec` gives you exactly one fixed shell command run *after* each pick, with no hook to change *how* a given commit is picked. `update-history-master` structurally needs per-commit branching (is this commit a base-merge? does it need message-preserving continuation? is it a merge-marker that must be `--allow-empty`?) that `--exec` cannot express.

**Recommendation: no `git rebase` subprocess at all.** Drive everything from a Python loop in the new `history_master.py`, using low-level git plumbing added to `git_ops.py` (`cherry_pick`, `merge_no_commit`, `commit_tree`/`commit --allow-empty`, `create_branch`, `move_ref`, `is_ancestor`, `rev_list_reverse`). For each commit in the walk:
- self-identifying base-merge → run the recreation procedure above;
- everything else → `git cherry-pick <sha>`, and on conflict, resolve then continue with `git cherry-pick --continue` / `git commit --no-edit` (never regenerate the message — see the trailer-preservation note below).

This eliminates error 16's class entirely (there's no external rebase machinery relocating anything a script depends on — the "rebase" *is* this process). It eliminates error 17's class because merge-commit special-casing is a first-class `if` in our own loop rather than something bolted onto an opaque exec hook.

**Resumability without `git rebase`'s built-in state:** since there's no `.git/rebase-merge` directory, add a small JSON state file, e.g. `.git/BASE_SPLIT_HISTORY_MASTER_STATE`, recording: remaining commits to process, current in-progress new tip, the `force_merge` set, and the original `ai/history/master` sha (for abort). `update-history-master --continue` resumes after a manually-resolved `git cherry-pick` conflict; `update-history-master --abort` restores the recorded original sha and deletes the state file. Mirror git's own conflict UX text (as `rebase_strip_claude_authorship.py` already does) so the failure mode feels familiar.

---

## 3. `--force-merge=<branch>` semantics

Concrete, unambiguous semantics: `--force-merge=X` does **not** let the caller specify an arbitrary sha to merge — it narrows to "branch X's history must be (re-)included, even if normal detection didn't find it or thinks it's already done." Two effects, threaded as a `force_merge: set[str]` parameter into the per-branch merge step:

1. **Skip the idempotency fast-path.** Normally, if `ai/history/master` already contains a merge-marker commit (`X-Base-Split-Merge-Marker-For: <clean-merge-sha>`) for X's clean-merge, the tool skips re-processing X. `--force-merge=X` bypasses that skip — useful for recovering from a previous partial/crashed run.
2. **Widen the detection search window.** Normal auto-detection only looks at commits new to `master` since the last recorded run (see §4). `--force-merge=X` widens the search to *all* of master's history for a commit correlating to `X`, to recover from cases auto-detection genuinely missed (e.g. a squash-merge that lost the correlating signal — see the unresolved ambiguity flagged in §4).

It still reuses the *same* branch↔merge-commit lookup logic as auto-detection (via `ai/history/{X}` existing + whatever correlation mechanism §4 settles on) — it only relaxes "trust the skip-check" and "only look at new commits," not "trust an arbitrary user-supplied sha." That keeps the flag narrow and safe rather than becoming a generic "merge anything into anything" escape hatch.

---

## 4. Commit ordering construction

```
old_master_sha   = rev_parse(main_branch)                      # captured BEFORE any pull
old_hm_sha       = rev_parse(history_name(main_branch))         # None if first run

checkout(main_branch); ensure_up_to_date(pull=..., yes=...)     # steps 1-2
new_master_sha   = rev_parse(main_branch)

checkout("base"); ensure_up_to_date(remote="base", pull=..., yes=...)   # steps 3-4
new_base_sha     = rev_parse("refs/remotes/base/base")

checkout(history_name(main_branch))                              # step 5

if old_hm_sha is None:
    create_branch(history_name(main_branch), at=new_master_sha)  # "master" category = literal ancestry
    tip = new_master_sha
else:
    walk = rev_list_reverse(f"{old_master_sha}..{old_hm_sha}")   # everything ai/history/master added on top
    tip = new_master_sha
    for c in walk:
        tip = recreate_base_merge(c, onto=tip) if is_base_merge(c) else replay_commit(c, onto=tip)
    move_ref(history_name(main_branch), tip)                     # step 6 done

newly_merged = find_newly_merged_clean_branches(old_master_sha, new_master_sha, main_branch)  # see ambiguity below
for clean_merge_sha, history_branch in newly_merged:
    if has_marker(tip, clean_merge_sha) and history_branch.base_name not in force_merge:
        continue                                                  # idempotent skip
    for c in rev_list_reverse(f"{merge_base(history_branch, tip)}..{history_branch}"):
        tip = replay_commit(c, onto=tip)
    tip = commit_empty(onto=tip, trailers={"X-Base-Split-Merge-Marker-For": clean_merge_sha})
    move_ref(history_name(main_branch), tip)

for b in force_merge:                                             # widened, forced search (§3)
    ... same as above but search all of master's history for b's merge commit ...

if not is_ancestor(new_base_sha, tip):                             # step 7, purely additive
    tip = merge_base_base(new_base_sha, onto=tip)
    move_ref(history_name(main_branch), tip)
```

`replay_commit` **always** finishes conflict-resolved commits via `git cherry-pick --continue` / `git commit --no-edit`, never regenerating the message. This is deliberate, procedural protection for `X-Base-Split-Source`/`X-Base-Split-Kind` correlation trailers: a clean cherry-pick preserves the message byte-for-byte automatically, so the only way those trailers could be lost is if some code path rewrites the message during conflict resolution — which this design forbids outright.

**Idempotency short-circuit for the whole command:** before doing any work, if `new_master_sha` is already an ancestor of `ai/history/master`'s tip *and* `new_base_sha` is already an ancestor too, the run is a no-op — this needs no bespoke bookkeeping beyond `git merge-base --is-ancestor`, since the "master" category is literal shared ancestry by construction.

**Genuinely unresolved ambiguity — flagging, not guessing:** `find_newly_merged_clean_branches` needs to correlate a merge commit landing in `master` with a specific `ai/history/{X}` branch. Two candidate mechanisms, both with real gaps:
- Parse the merge commit subject (`Merge branch 'X'` / `Merge pull request #N from owner/X`) against known `ai/history/*` branch names. **Breaks entirely for GitHub squash-merges**, which have no second parent and no branch name in the resulting single commit at all.
- An explicit mapping written by whatever prepares the clean branch for merging (a tracked mapping file, or a required trailer like `X-Base-Split-Clean-Branch: X` on the PR's final commit) — robust, but this is sibling-agent (`sync-splits`) territory, and it requires *whoever merges the PR* to preserve that trailer, which squash-merge UIs don't do by default unless the repo enforces "create a merge commit" (not squash) for split-participating branches, or a bot inserts the trailer into the merge/squash commit message at merge time.

I designed `find_newly_merged_clean_branches` as a swappable single function precisely so this doesn't block the rest of `update-history-master`'s design, but recommend resolving it explicitly with the sibling agents before implementation — otherwise `update-history-master` will silently miss any squash-merged branch's history until someone notices and reaches for `--force-merge`.

---

## 5. Missing-branch handling for `rebase-branches-to-master`

**Recommendation: skip + clearly report, never auto-synthesize.** Auto-synthesizing would call into `sync-splits` (owned by sibling agents, not yet finalized) and would surprise a user who asked only to "rebase" by generating a whole new branch with derived content as a side effect.

Sequencing matters and is not fully independent: `unclean`'s target is *defined as* history's post-rebase tip, not a fixed ref, so:

```
for each requested branch name:
    if clean exists:  rebase(clean, onto=main_branch)         else: report "skipped: clean {name} missing"
    if history exists: rebase(history, onto=history_name(main_branch))  else: report "skipped: history {name} missing"
    if unclean exists:
        if history existed AND its rebase succeeded: rebase(unclean, onto=<history's new tip>)
        else: report "skipped: unclean {name} — its target (history) is missing or failed"
    else: report "skipped: unclean {name} missing"
```

Exit 0 if at least one rebase actually ran and none that ran failed; exit 1 with "nothing to rebase" if none of the three exist for that branch name (almost certainly a typo/wrong argument, worth failing loudly on).

---

## 6. CLI shape

New modules in `°split_lib/`:
- `history_master.py` — the walk/recreation/ordering logic from §1/§4, built on new `git_ops.py` primitives (`cherry_pick`, `merge_no_commit`, `create_branch`, `move_ref`, `is_ancestor`, `rev_list_reverse`, `commit_empty`).
- `rebase_to_master.py` — the three-way per-branch rebase from §5.
- `trailers.py` — shared `read_trailers(sha, cwd) -> dict[str, list[str]]` / `add_trailers(message, trailers) -> str` wrapping `git interpret-trailers`, used by both new modules (and worth flagging to sibling agents as a shared dependency rather than each side reimplementing trailer parsing).
- `state.py` (or inline in `history_master.py`) — the resume/abort state file from §2.

`cli.py` additions:

```python
update_history = subparsers.add_parser("update-history-master", help="...")
update_history.add_argument("--force-merge", action="append", default=[], metavar="BRANCH")
update_history.add_argument("--pull-master", action="store_true")
update_history.add_argument("--pull-base", action="store_true")
update_history.add_argument("--yes", action="store_true", help="Assume yes for prompts (matches --fix-remotes convention).")
update_history.add_argument("--continue", dest="continue_", action="store_true")
update_history.add_argument("--abort", action="store_true")
update_history.add_argument("--dry-run", action="store_true")

rebase_branches = subparsers.add_parser("rebase-branches-to-master", help="...")
rebase_branches.add_argument("branch", nargs="?", help="Feature branch base name; omit to process all detected branch groups.")
rebase_branches.add_argument("--yes", action="store_true")
rebase_branches.add_argument("--dry-run", action="store_true")
```

---

## 7. Interactive prompts

Directly mirror `fix_username.py`'s established convention (`check_and_fix_required_remotes`): a boolean flag (`--pull-master`, `--pull-base`) both selects the action and suppresses its own prompt. If the flag is absent: when stdin is a tty, prompt `Pull {branch}? [y/N] ` (default N, matching the spec's explicit "default N"); when stdin is not a tty, silently skip (don't block automation, don't guess consent) — exactly `fix_username.py`'s `if not yes: if not sys.stdin.isatty(): return unchanged`. `--yes` is a separate blanket flag for scripts/CI that want both pulls without naming them individually; per-flag (`--pull-master`/`--pull-base`) wins if both are given more narrowly.

---

## 8. Tests (real temp-git-repo, following `test_git_split_push_checks.py`'s idiom)

New `scripts/°base/tests/test_git_split_history_master.py`:
1. **First-ever run**: temp repo, `master` with a few commits, no `ai/history/master`. Assert the new branch is created, its tip equals `master`'s tip (literal ancestry, no copy), and it's a no-op ancestor check on rerun.
2. **Subsequent run after new master commits**: seed `ai/history/master` with the first-run result plus a manually-added marker/history commit on top; advance `master`; run again; assert those on-top commits got replayed with unchanged trailers/content and the branch now sits on the new `master` tip.
3. **`base/base` merge-recreation**: construct a fake `base` remote (a second bare temp repo) with a `base` branch; merge it into `ai/history/master` with a deliberate conflicting file, tag the resulting commit with `X-Base-History-Merge-Sha`; advance `master` with an unrelated change to the same file; rerun; assert the recreation path is taken (not cherry-pick) and per-path conflict resolution matches the original commit's content for the conflicting file while picking up the new unrelated content elsewhere.
4. **Idempotent rerun**: run twice with no changes to master/base between runs; assert the second run is a no-op (`ai/history/master` sha unchanged).
5. **`--force-merge`**: a squash-merge scenario where auto-detection can't find the branch (no second parent, no trailer), then rerun with `--force-merge=X` and assert it's picked up.

New `scripts/°base/tests/test_git_split_rebase_to_master.py`: the three missing-branch combinations (only unclean; only clean; clean+unclean but no history) each in a real temp repo, asserting the correct skip-and-report behavior per §5 and that unclean is skipped whenever history is missing or its own rebase failed.

---

### Critical Files for Implementation
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/git_ops.py
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/branches.py
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/cli.py
- /home/user/git/luckydonald/base/scripts/°base/git/rebase_strip_claude_authorship.py
- /home/user/git/luckydonald/base/scripts/°base/git/remote/fix_username.py
- /home/user/git/luckydonald/base/ai/°base/todo.md