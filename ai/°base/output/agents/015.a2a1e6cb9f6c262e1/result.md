Now I have enough to give the full report.

## Report

### 1. `sync_unclean.py` — `reconstruct_unclean` trace when `history_tip is None`, `clean_tip` is not None

File: `/home/user/git/luckydonald/base/scripts/°base/git/°split_lib/sync_unclean.py`

- **Guard clause** (L519-521): only bails out if *both* are `None`. With `clean_tip` set and `history_tip is None`, execution continues normally — this path is reachable and not short-circuited.
- **`_new_shas_since_cursor`** (L477-489): handles `tip is None` gracefully — `if tip is None: return []` (L478-479) — so `new_history_shas` becomes `[]` and `history_infos = []` (L532). No crash, no special-casing needed for a `None` history tip.
- **`bucket_commits`** (L123-146): with `history_infos == []`, the `for side, infos in (("clean", clean_infos), ("history", []))` loop simply does nothing for `"history"`. Every clean commit's key is computed via `_key_for_info` (L114-120): since there is no `ai/history/{branch}` (and no unclean lineage yet either), each clean commit's `X-Base-Split-Source` trailer either won't resolve locally or won't exist, so `_key_for_info` falls back to `("unmatched", info.sha)` for every clean commit (unless a clean commit happens to carry a resolvable `X-Base-Split-Source` sha that is itself a real local commit — see caveat below). So yes: with zero history commits, all clean commits land as unmatched/code-only buckets, each `{"clean": info, "history": None}`. This part behaves correctly.
- **Base commit for creating `ai/UNCLEAN/{branch}` when it doesn't exist** (L562-568):
```python
unclean_tip = git_ops.rev_parse(unclean_ref, cwd)
if unclean_tip is None:
    base_tip = git_ops.rev_parse(main_branch, cwd)
    assert base_tip is not None, f"main branch {main_branch!r} not found"
    unclean_tip = base_tip
    if not dry_run:
        git_ops.create_branch(unclean_ref, unclean_tip, cwd)
```
It always bases the brand-new `ai/UNCLEAN/{branch}` on `main_branch`'s tip (e.g. `master`), **not** on `clean_tip` or any fork point of `feature`. This is correct/consistent for the normal Phase-2 flow (unclean starts where the branch's own ancestor `master` was), matching the same pattern used in `sync_splits.ensure_branch_started` (bases new `clean`/`history` off `main_branch`/`history_main_ref` too).

**Caveat / real gap for the bootstrap scenario:** `_key_for_info` (L118) calls `git_ops.rev_exists(info.source, cwd)`. If `feature` is a plain, real pre-existing clean-format branch with real human commits, those commits almost certainly have **no** `X-Base-Split-Source` trailer at all (`trailers.read_trailer_value` returns `None`), so `info.source is None` and the key falls to `("unmatched", info.sha)` — fine. But there's a subtler issue: `merge_base` in `_new_shas_since_cursor`'s fallback path (L486, `base = git_ops.merge_base(tip, lower_bound_ref, cwd)`) is used to bound `new_clean_shas` to only commits since `feature` forked from `master`. That's fine and works whether or not history exists.

The bigger gap: nothing in `reconstruct_unclean` ever bases the *content* of the synthesized `ai/UNCLEAN/{branch}` on `clean_tip`'s actual pre-fork state relative to `main_branch` other than by walking `new_clean_shas` (bounded by merge-base with `main_branch`) and reapplying each commit's diff via `build_merged_commit`/`tree_ops.apply_path_changes`. Since every clean commit is "unmatched" it goes through the `else: solo = clean_info` branch in `build_merged_commit` (L400-405), which applies `solo.paths` (the clean commit's own diff) onto `prev_tree`, i.e. tree starts from `master`'s tree and each clean commit's diff is replayed on top verbatim. **This is exactly the "genesis create UNCLEAN purely from clean when there's no history yet" behavior — and it looks correct for a scenario where the branch truly has no AI-authored/history-only content to recover; you get an `ai/UNCLEAN/{branch}` that is essentially a byte-identical copy of `clean`'s post-fork commits.** That is the expected/desired outcome for "bootstrap from clean-only, no history exists" — everything code-side reconstructs correctly.

- **`check_order_consistency`** (L214-244): with `unclean_source_order == []` (since `unclean_ref` doesn't exist yet → `read_unclean_source_order` returns `[]` per L250-251), `check_order_consistency` returns immediately (L226: `if force or not unclean_source_order: return`). No issue.
- **Divergence detection** (L546-558): with no matched keys (`key_to_sha` will be `{}` since unclean_ref doesn't exist), `divergence_candidates` is `{}`, so `detect_divergences` returns `[]`. No issue.
- **Ordering** (L581-586, `_order_buckets`): all items are unmatched, so `order_key` (L158-170) returns `(1, preceding_matched_index, date_ts, 0 if is_clean else 1)` for every item — sorts purely by (nonexistent preceding matched anchor = `-1` for all, then) commit date. Since `history_infos` is empty, `preceding_matched_index` for clean items always returns `-1` (no matched history sibling ever found, L183-188 loop finds nothing because no keys are ever in `unclean_source_order`), so ties break by date — i.e. clean commits get built strictly in their own chronological order. Correct.

**Verdict for #1: (a) — mostly already works.** Given `clean_tip is not None` and `history_tip is None`, `reconstruct_unclean` will bucket every clean commit as unmatched/code-only and faithfully rebuild `ai/UNCLEAN/{branch}` by replaying each clean commit's diff onto a base forked from `main_branch`. This is true **whether or not `ai/history/{branch}` is created empty first** — the code already tolerates `history_tip is None` outright; creating an empty history branch first is not actually required by `reconstruct_unclean` itself (it would just make `history_tip` non-`None` pointing at some sha equal-ish to `main_branch`'s history fork, contributing zero `history_infos` either way since there'd be no new commits past whatever cursor/merge-base).

### 2. `history-master-fork-point` ref — is it written anywhere?

Searched all of `scripts/°base/git/°split_lib/*.py`, tests, and `ai/°base/plans/`:

```
history_master.py:34:  FORK_POINT_REF_TEMPLATE = "refs/base-split/history-master-fork-point/{branch}"
history_master.py:473: fork_point = git_ops.rev_parse(FORK_POINT_REF_TEMPLATE.format(branch=branch_name), cwd)
history_master.py:474-482: fallback comment + merge_base fallback when ref is None
plans/027_...md:28:    documents the ref as "written once, when `history` is first created"
```

No `git_ops.create_branch`, `update-ref`, or any write call targets this ref anywhere — not in `history_master.py`, not in `sync_splits.py::ensure_branch_started` (L52-69, confirmed below), not in `sync_unclean.py`, not in `cli.py`, not in any test file. `history_master.py`'s own comment at L475-482 explicitly acknowledges this: *"Fallback when (A)'s fork-point ref hasn't been written for this branch (older branch, or (A) hasn't run yet): the plan reserves that ref for (A) to write, not for (C) to read, but (C) has no other source of truth..."*

**Verdict for #2: it's a documented-but-unimplemented gap.** The plan (L28) explicitly assigns writing this ref to (A) `sync_splits.py`, but `ensure_branch_started` (the only place in (A) that creates `history` branches) never writes it.

### 3. `sync_splits.py` — `ensure_branch_started`

```python
def ensure_branch_started(ref: str, base_ref: str, cwd: Path, *, dry_run: bool = False) -> str:
    tip = git_ops.rev_parse(ref, cwd)
    if tip is not None:
        return tip
    base_tip = git_ops.rev_parse(base_ref, cwd)
    assert base_tip is not None, f"base ref {base_ref!r} does not exist"
    if not dry_run:
        git_ops.create_branch(ref, base_tip, cwd)
    return base_tip
```
(L52-69)

Called at L150-151 in `sync_branch`:
```python
clean_tip = ensure_branch_started(clean_ref, main_branch, cwd, dry_run=dry_run)
history_tip = ensure_branch_started(history_ref, history_main_ref, cwd, dry_run=dry_run)
```

- When `history_ref` (`ai/history/{branch}`) doesn't exist, it's created at `history_main_ref`'s tip (`branches.history_name(main_branch)` = `ai/history/master`'s current tip) — L147-151.
- It writes **no** metadata whatsoever beyond creating the branch ref itself. No `refs/base-split/history-master-fork-point/{branch}` write, no other bookkeeping ref. This confirms #2: `sync_splits.py::ensure_branch_started` is the exact place the plan designates for writing the fork-point ref (since it's the only code that creates `history` branches "fresh"), and it doesn't do it.

**Verdict for #3: (c) doesn't do it at all** — needs new code (a one-line `git_ops.create_branch`/update-ref call for `FORK_POINT_REF_TEMPLATE.format(branch=base_branch)` set to `base_tip`, right after/inside `ensure_branch_started`, gated on "did we just create it fresh").

### 4. `branches.py` / `history_master.py` — how `ai/history/master` itself is created/detected

- `branches.history_name(base_branch)` (`branches.py:63-64`): pure string function, `f"ai/history/{base_branch}"`. Called with `main_branch` gives `ai/history/master`.
- `branches.detect_main_branch(repo_root)` (`branches.py:77-102`): best-effort — tries `refs/remotes/origin/HEAD` symbolic-ref, else checks `main`/`master`/`mane` in order, defaults to `"master"`.
- `ai/history/master` creation is entirely inside `history_master.py::update_history_master` (L568-674): on `first_run` (`old_history_sha is None`, L612), it builds a replay plan from scratch (L419-422: `old_master_sha = None`, `replay_start_tip = master_tip`, `steps = []`), runs it (which is a no-op for steps besides possibly folding `base/base` and any pre-existing merged clean branches), then **creates** the branch: `git_ops.create_branch(history_ref, tip, cwd)` at L663-664. This is the only place `ai/history/master` gets created.
- **Implication for the bootstrap command:** a bootstrap-for-`{branch}` command must first check `git_ops.rev_parse("refs/heads/" + branches.history_name(main_branch))` (or unprefixed via `rev_parse` since it resolves branch names) exists; if not, it must run/require `update_history_master(...)` first (or otherwise fail with a clear message) before it can fork `ai/history/{branch}` off it, exactly as the user suspected.

### 5. Plan `027_...md` — key confirmed design points for a follow-up bootstrap plan

Full text read (above). Key constraints a bootstrap plan must respect:
- Refs live under `refs/base-split/...` (not `refs/heads/`) — L27.
- `refs/base-split/history-master-fork-point/{branch}` is defined as written **once**, when `history` is *first created* — L28. A bootstrap command creating `ai/history/{branch}` for the first time is exactly the moment this should be written (same obligation `sync_splits.ensure_branch_started` currently fails to fulfill — see #2/#3).
- Cursor refs `refs/base-split/unclean-cursor/clean|history/{branch}` (L29) are maintained by `reconstruct_unclean` itself (`sync_unclean.py` L523-526, L593-599) — no bootstrap-specific handling needed; they'll be created naturally on first `reconstruct_unclean` run.
- Commit-metadata policy (L34): author info always from source commit, committer is bot identity, committer date "now" — any new bootstrap-created commits (e.g. an empty seed commit for `ai/history/{branch}`) should follow this if it creates any commits (though per §1 findings, no new commits are actually required beyond an empty branch ref — `git_ops.create_branch` is a pure ref-create, no commit).
- Ordering/divergence rules (L42, L44) are unaffected by bootstrap — they already tolerate zero pre-existing matched keys, as traced in §1.
- **Not stated anywhere in the plan:** any procedure for "branch already has real commits in clean-only form, no unclean/history exist" — this whole bootstrap scenario is out of scope of plan 027; it's new territory, consistent with your "phase 3" framing.

### 6. `test_git_split_sync_unclean.py` — existing coverage of "history missing entirely"

`SyncUncleanTestBase.setUp` (lines 50-64) unconditionally creates `ai/history/master` (L62) and `ai/history/feature` (L64) alongside plain `feature` (L63), for every single test class/method in the file (`MergedPairTests`, and all others per the earlier grep: `test_code_and_history_pair_merge`, `test_merge_prefers_clean_subject_and_keeps_history_body`, `test_code_only_cherry_pick_no_source_trailer`, `test_history_only_cherry_pick_no_source_trailer`, `test_dangling_trailer_falls_back_to_unmatched`, `test_divergence_detected_but_not_rewritten_by_default`, `test_divergence_rewritten_with_allow_diverge_rewrite`, `test_idempotent_rerun_makes_no_changes`, `test_duplicate_trailer_collision_raises`).

**Verdict for #6: genuine gap — not tested today.** No test ever calls `reconstruct_unclean` with `ai/history/{branch}` absent; every test's `history_tip` is non-`None` from setUp. The "clean exists with real commits, `ai/history/{branch}` and `ai/UNCLEAN/{branch}` both absent" scenario has zero test coverage.

---

## Overall answer

- **Code-mechanics of `reconstruct_unclean` reconstructing `ai/UNCLEAN/{branch}` purely from a clean-only branch (no history at all)**: **(a) already works** — `_new_shas_since_cursor` handles `None` tip, `bucket_commits`/`_key_for_info` correctly bucket everything as unmatched, and the unclean-branch-creation base-sha logic (fork off `main_branch`) is unaffected by history being absent. This holds true whether or not you pre-create an empty `ai/history/{branch}` — that step is not load-bearing for `reconstruct_unclean` itself.
- **The fork-point ref (`refs/base-split/history-master-fork-point/{branch}`)**: **(c) doesn't exist at all** — plan-documented, never written by any code path (`sync_splits.ensure_branch_started` is the designated writer per the plan and doesn't write it; `history_master.py` only reads it with an explicit merge-base fallback for exactly this "not written" case). A bootstrap command for phase 3 needs to write this ref itself when it creates `ai/history/{branch}` fresh — this is a real, confirmed gap independent of the clean-only bootstrap scenario, and worth fixing in `sync_splits.py` too so future normal-flow branches get it as originally designed.
- **Prerequisite `ai/history/master` existence**: bootstrap must ensure `update_history_master` has run at least once (creates the branch on `first_run`) before forking a per-branch `history` off it — `branches.history_name`/`detect_main_branch` are simple helpers with no bootstrap logic of their own.
- **Test coverage**: zero — the exact "history and unclean both missing, only clean with real commits" scenario is untested in `test_git_split_sync_unclean.py`; a new test (and likely a new `bootstrap_branch`/`bootstrap-split` function/CLI command per the plan's naming conventions) is needed for phase 3, plus writing the fork-point ref should be added to `sync_splits.ensure_branch_started` as a small independent fix.