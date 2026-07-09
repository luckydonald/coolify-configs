# Plan: Unclean Reconstruction Direction of `sync-splits`

## Scope recap

Given `clean` (commits since divergence from `master`) and `history` (commits since divergence from `ai/history/master`), reconstruct/update `ai/UNCLEAN/{branch}` by walking both branches' commits, correlating them via `X-Base-Split-Source`/`X-Base-Split-Kind` trailers (produced by the sibling forward-direction implementation), and cherry-picking / merging them onto a base derived from `ai/history/master`.

Read files referenced: `scripts/°base/git/°split_lib/branches.py`, `classify.py`, `git_ops.py`, `push_checks.py`, `cli.py`, `split.py`, and `ai/°base/todo.md:59-163`.

---

## 1. Correlation / matching algorithm

### 1.1 Extracting trailers

Add a helper (new module, see §2) `read_commits(branch, since) -> list[CommitInfo]` where `CommitInfo` has `sha`, `subject`, `body`, `author`, `committer`, `date`, `paths`, and parsed trailers. Use:

```
git log --format='%H%x00%an%x00%ae%x00%ad%x00%cn%x00%ce%x00%cd%x00%B%x03' --date=iso-strict <since>..<branch>
```

(`%x03` as a record separator, `%x00` as field separator, since neither appears in normal commit text; still guard against them appearing in a hostile commit body by using `--no-patch` and treating the split defensively — if ambiguous, fall back to one `git show -s --format=%B <sha>` per commit, which is simpler and correctness > speed given branch commit counts are small). Recommend the **simple version**: one `rev-list --reverse` to get ordered SHAs, then per-sha `git log -1 --format=%B` to get the full body, then run that body through `git interpret-trailers --parse --no-divider` to extract `X-Base-Split-Source` and `X-Base-Split-Kind`. This mirrors `push_checks.py`'s existing per-commit-subprocess style (`subject_for_commit`, `changed_paths_for_commit`) rather than inventing a new batch format — consistent with the codebase's existing preference for simplicity over batching.

Trailer keys are case-insensitive per git trailer conventions; normalize to the exact case `X-Base-Split-Source` / `X-Base-Split-Kind` when reading (git's `--parse` already downcases inconsistently across versions — test this explicitly).

### 1.2 Bucketing

For each commit `c` in `clean` (oldest→newest, since divergence from `master`) and each commit `c` in `history` (oldest→newest, since divergence from `ai/history/master`... but see §6 for the exact base commit), compute a **correlation key**:

- If `X-Base-Split-Source` trailer present AND that sha still exists in the *known unclean lineage* (see §1.4 — "known" meaning: reachable from the unclean commit recorded the last time this branch pair was reconstructed, not just "exists anywhere in repo," since a stale/garbage sha could technically resolve via `cat-file -e` to an unrelated object) → key = that sha (the **original unclean sha**).
- Else → key = `("unmatched", c.sha)`, i.e. its own sha, unique to itself. This is the "no trailer / dangling trailer" bucket (case b).

Build a dict: `key -> {"clean": CommitInfo|None, "history": CommitInfo|None}`.

- A key with both `clean` and `history` populated → **code+history pair** (case c) → single merged unclean commit.
- A key with only `clean` → **code-only** (case b-clean).
- A key with only `history` → **history-only** (case b-history).
- Two *different* unmatched keys should never collide by construction since they're keyed by their own sha.

Edge case: two clean commits or two history commits sharing the same `X-Base-Split-Source` (e.g. someone split one unclean commit's amend into two clean commits, or duplicated a trailer by hand). Treat as an **error**: refuse to reconstruct that key, emit a diagnostic naming both shas, exit nonzero. Do not guess which one is "real." This is rare enough (manual trailer tampering) that erroring is correct over silently picking one.

### 1.3 Determining total order — the hard part

The spec says "cherry-pick commits from clean and history in order," but clean and history are two independently-committed branches; their *local* per-branch order may not agree once you interleave matched and unmatched commits. Concretely define the ordering algorithm:

**Primary order key: position in the original unclean lineage, when known.**

Maintain (from the last successful reconstruction, persisted — see §3's cursor) an ordered list of "original unclean shas that were split," call it `unclean_source_order: list[sha]`, in unclean-commit order. For any matched key (case a/c), its order key is `index_in(unclean_source_order, key)`.

For unmatched commits (case b), there is no unclean-lineage position — they were never on unclean. Their order key must come from **branch position interleaved with matched neighbors**: assign each unmatched clean commit the fractional order key `(index_of_preceding_matched_clean_commit_in_unclean_source_order) + epsilon * (offset among consecutive unmatched commits, using clean's own commit order)`, and symmetrically for history. Concretely:

1. Walk `clean`'s commit list in order, tagging each with the unclean-order-index of the nearest **preceding** matched commit (or `-1` if none yet).
2. Do the same for `history`.
3. Merge-sort: sort by `(preceding_matched_index, source_kind_tiebreak, within-run_sequence)`. Within a run of unmatched commits that share the same `preceding_matched_index`, clean-side unmatched commits and history-side unmatched commits need a deterministic relative order since there's no branch-crossing signal — use **commit date** (`%cd`/`%ad`, committer date) as the tiebreak, and if dates tie, put history-only commits after clean-only commits (arbitrary but deterministic, documented).
4. Special-case the very first run (`preceding_matched_index == -1`, i.e. before the first matched commit): sort purely by commit date across both branches.

**Detecting genuinely irreconcilable ordering (the scenario the user describes: clean-side order says X before Y, history-side order says Y before X, for two *matched* keys X and Y):**

This *cannot* happen if `unclean_source_order` is used as the single source of truth for matched-commit order — matched commits are ordered by where their source sits in the **original unclean branch**, not by clean's or history's own commit order. So a clean-branch commit ordering that disagrees with history's ordering for the *same pair of unclean-derived commits* is simply irrelevant to the algorithm; both get placed according to `unclean_source_order`, and clean's/history's local git-log order is not consulted for matched pairs at all.

However there is a real irreconcilable case: if `unclean_source_order` itself is not available (e.g. the previous unclean branch was deleted/force-pushed away, or this is the very first reconstruction and both clean and history were produced by hand rather than by a forward split) then matched commits have no independent order signal, and clean-order vs history-order can genuinely disagree on relative order for the *same* commit-pair boundary (this reduces to the unmatched-commit tiebreak logic in step 3, using commit date). **Recommendation: use committer-date as universal fallback tiebreak, but detect and warn** when clean's local ordering of two matched keys disagrees with history's local ordering of the same two keys AND `unclean_source_order` has no opinion (never happened before) — in that situation, print a warning listing both candidate orderings and pick the date-based order, but do not silently proceed without printing this warning, since it's a genuine ambiguity a human should sanity-check.

**Flagging as unresolved ambiguity:** what to do when `unclean_source_order` *does* have both keys, and clean's own local order of the two corresponding clean commits (before considering history at all) actually disagrees with that stored order — e.g., a `clean` reviewer did an interactive rebase reordering clean commits. This means clean's commit sequence no longer topologically matches the original unclean sequence. This is a real possibility (case a "diverged" scenario, extended to reordering, not just content edits) and I do not have a fully confident answer for what "correct" reconstructed unclean order should be here — reordering commits to match `unclean_source_order` could silently discard an intentional reordering the reviewer wanted; reordering to match clean's new order could break the pairing logic for code+history merges if history wasn't reordered to match. **Recommendation:** detect this (compare `clean`'s topological order of matched-commit shas against `unclean_source_order` restricted to those shas — if not a sub-sequence match, they disagree) and hard-refuse with a clear error naming the reordered commits, requiring `--force` (which then falls back to "trust clean's new order, re-derive history's contribution by matching keys wherever they still fall") rather than silently picking one. This is flagged explicitly as the residual hard case per the user's callout — I'd want this confirmed with the user/sibling agent designer before implementation rather than silently baking in a resolution.

### 1.4 "Known unclean lineage" — resolving stale/dangling trailer shas

A trailer's sha might not exist locally at all (shallow clone, gc'd, or branch history force-pushed away) or might exist but belong to an unrelated object (extremely unlikely but worth guarding). Validation step per matched key: `git cat-file -e <sha>^{commit}` (reuse `push_checks.rev_exists`-style helper already in `git_ops.py`, generalize it out of `push_checks.py` into `git_ops.py` if not already — checking: `rev_exists` is already in `git_ops.py`, good, reuse directly). If the sha doesn't resolve to a commit, downgrade that commit to the "unmatched" bucket rather than erroring — case b covers "no matching unclean," and a dangling reference is functionally equivalent to "no matching unclean" for reconstruction purposes (there is nothing to compare against for divergence-detection anyway).

---

## 2. Merging a code+history pair into one unclean commit

### 2.1 Shared plumbing module — propose `tree_ops.py`

Since both the forward split (clean = code-subset tree, history = ai-subset tree, both derived by *removing* paths from an unclean tree) and this reconstruction (recombining two partial trees into one) are two directions of the same tree-surgery operation, propose a shared module `scripts/°base/git/°split_lib/tree_ops.py` with primitives usable by both directions:

- `read_tree_paths(sha) -> dict[str, str]` — path → git object mode+sha (via `git ls-tree -r --format='%(objectmode) %(objecttype) %(objectname)\t%(path)' <sha>`), used to know what changed.
- `diff_paths(old_sha, new_sha) -> list[PathChange]` where `PathChange` has `path`, `old_mode/old_blob`, `new_mode/new_blob` (added/modified/deleted), via `git diff-tree --no-commit-id -r --raw <old>..<new>` — this is exactly what the forward direction needs to decide code-vs-ai split, and what reconstruction needs to know "what did clean commit C actually change relative to its parent" and "what did history commit H actually change relative to its parent."
- `apply_path_changes(base_tree_sha, changes: list[PathChange]) -> new_tree_sha` — build a new tree object by reading `base_tree_sha`, overlaying `changes` (add/modify/delete specific blobs at specific paths), and writing it with `git mktree`/`git update-index --index-info` + `git write-tree` against a scratch index (use `GIT_INDEX_FILE` pointed at a temp file, `git read-tree base_tree_sha` into it, then `git update-index` per changed path, then `git write-tree`; this avoids touching the actual working tree/checkout at all, which matters since `sync-splits` shouldn't require a clean or specific working-tree state).
- `make_commit(tree_sha, parents: list[str], author, committer, message) -> sha` via `git commit-tree`.

This is a design proposal to raise with the sibling forward-direction agent for actual code sharing; document it as such rather than assuming it will be accepted, since I cannot see that agent's in-progress design. If they've picked a different plumbing shape, adapt to theirs rather than force a merge — the important shared contract is just "diff paths between two trees" and "apply a path-level patch onto a third tree," which is genuinely direction-agnostic.

### 2.2 Building the merged unclean commit for a code+history pair

Given matched key `unclean_source_sha`, clean commit `C`, history commit `H`, and `unclean_prev` = the tip of the unclean branch built so far (its tree = `T_prev`):

1. `changes_C = diff_paths(C.parent, C)` — the paths clean commit C actually touched (its own diff, not the full tree). Using per-commit diffs (not full-tree state) is essential: this correctly handles the case where a later commit in the sequence touches the *same* file that an earlier code+history commit also touched — we're replaying deltas, not full snapshots, exactly matching cherry-pick semantics.
2. `changes_H = diff_paths(H.parent, H)` similarly.
3. Sanity check: `changes_C` should only touch non-AI-base paths (per `classify.is_ai_base_path`) and `changes_H` should only touch AI-base paths — if either violates this (e.g. clean commit C somehow touches `ai/`), that's a policy violation worth warning about (could happen if `clean` was hand-edited during review to add something into an AI path) but not necessarily fatal for reconstruction — apply it anyway (it's real intended content) but print a warning, since silently dropping real committed content would be worse than a policy warning.
4. `combined_changes = changes_C + changes_H` (concatenate; if the *same path* appears in both — e.g., clean and history both technically touched the exact same file, which given the ai-base-path partition should be structurally impossible for typical content, but a rename could straddle both — if a genuine path collision occurs, error out and require manual resolution rather than silently picking one side's version).
5. `new_tree = apply_path_changes(T_prev, combined_changes)`.
6. `new_commit_sha = make_commit(new_tree, parents=[unclean_prev], author=?, committer=?, message=?)` (metadata resolution below).
7. **Re-attach trailers**: the new unclean commit itself does not need an `X-Base-Split-Source` trailer (it *is* the unclean commit — trailers of that shape live on clean/history commits pointing back at it). What it does need, to support *future* reconstructions/divergence checks, is enough information for the forward-direction re-split to later re-derive matching clean/history commits. That's the forward-direction agent's concern, not this one — flag as an integration point: reconstruction just needs to produce a normal unclean commit; the forward split will assign it a fresh identity (its sha) as the new `X-Base-Split-Source` value next time it runs.

### 2.3 Commit metadata resolution (author/committer/date/subject/body)

Design choice: **prefer whichever side is the "richer"/original content, defaulting to reconstructing what the original unclean commit likely looked like, since that's the ground truth this operation is trying to restore.**

Concretely:
- **Author identity + author date**: take from whichever of `C`/`H` has a `X-Base-Split-Kind` trailer indicating it's the "primary" side — but since kind is `code|history|mixed` referring to what *that specific* clean/history commit contains (not which one is authoritative), there's no built-in priority signal. Recommend: **take author identity+date from `C` (the code side) by default**, since author identity/date for real work is conventionally tied to when the code was written, and code commits are less likely to have been mechanically rewritten by the split tooling itself. Document this as a default, and if `C.author != H.author`, warn (they should typically match since both were split from the same original commit, unless the history side was later hand-edited by someone else during review).
- **Committer identity + committer date**: standard git convention — this should be whoever/whenever ran the reconstruction (i.e., "now," the person running `sync-splits`), matching how git itself treats commits created via tooling (cherry-pick, rebase) as getting a fresh committer stamp while preserving the original author stamp. This also naturally reflects that this is a *new* unclean commit object even if its content is old.
- **Subject/body**: **prefer `C`'s message verbatim if `C`'s subject differs from `H`'s** (the common "someone reworded the clean commit during review" case named in the prompt) — reasoning: the clean-side message is externally visible/reviewed and more likely to have been intentionally corrected; the history-side message is often auto-generated boilerplate (e.g., commit-subject convention noted in `classify.py`'s `AI_SUBJECT_RE`, things like "ai: updated prompt"). If `H`'s message contains meaningful non-boilerplate content not present in `C`'s (heuristic: strip trailers, compare non-trivial line count), append it as an extra paragraph in the body under a `---` separator rather than discarding it, so review-added history detail isn't silently lost. If `C.subject == H.subject` (unedited, matches the original unclean subject that was split into both), just use it as-is with no need for the append heuristic. This is a judgment call with reasonable alternatives (e.g. always concatenating both bodies) — flag as a design decision the user may want to weigh in on rather than something with one obviously-correct answer.
- Preserve `Co-Authored-By`/other trailers from both, deduped, minus the `X-Base-Split-*` trailers themselves (those belong to clean/history, not to the reconstructed unclean commit).

---

## 3. Handling genuinely new commits with no trailer (case b)

### 3.1 Cherry-pick semantics

Code-only commit `C` (unmatched): its own diff (`changes_C` from §2.2 step 1) only touches non-AI paths, so it can be applied directly onto the running unclean tree as a pure delta — no tree-merging needed, just `apply_path_changes(T_prev, changes_C)` then `make_commit(..., parents=[unclean_prev])`, author/committer/message taken directly from `C` unchanged (this is a true cherry-pick, content-for-content). Symmetric for history-only `H`.

Using the diff-path-and-reapply primitive (rather than literal `git cherry-pick`) is important here because a literal `git cherry-pick` would try to apply the patch against `C`'s parent context assuming the same tree shape, which will conflict as soon as `T_prev` (the unclean tree) has diverged (it always has — it additionally contains the AI-base paths and any deltas from history commits that clean doesn't know about). Applying at the blob/path level sidesteps this — this is not a 3-way merge, it's a deterministic tree overlay, which is safe because code-only commits by construction only ever touch non-AI paths and are known not to conflict with AI-base paths that history commits touch. The remaining conflict risk is two commits (from possibly-different runs) touching the *same* non-AI path with a base-content mismatch — this can occur if unclean's current tree at that path differs from what `C`'s parent expected (e.g. unclean has since been directly hand-edited at that path, or the "already applied" detection in §3.2 has a bug and a commit gets replayed with stale base assumptions). Detect this by comparing `T_prev`'s current blob at each touched path against `C.parent`'s blob at that path — if they don't match, this is a real conflict; do not silently overlay a delta on top of unexpected content. **Recommendation:** on mismatch, abort with a clear message identifying the path and both commits, and require `--force` (which would fall back to invoking a real `git merge-file`/3-way text merge and leaving a conflict marker if unresolved, mirroring `git cherry-pick`'s own conflict UX) rather than silently overlaying.

### 3.2 Idempotency / "already applied" detection via per-branch cursors

Persist reconstruction progress so re-running `sync-splits --reconstruct-unclean` (or whatever the flag ends up being, see §7) doesn't double-apply. Store, on the unclean branch tip itself (not in a side file, so it travels with the branch and survives force-pushes/clones — consistent with the trailer-based approach already used elsewhere in this design) two trailers on the last reconstructed commit's message... but that's per-commit, not per-run. Better: store cursor state as **refs**, mirroring how git itself tracks rebase progress, e.g.:

- `refs/base-split/unclean-cursor/clean/{branch}` → last clean sha successfully incorporated into unclean.
- `refs/base-split/unclean-cursor/history/{branch}` → last history sha successfully incorporated.

These are plain lightweight refs (not tags, not under `refs/heads/`), updated atomically after each successful reconstruction run via `git update-ref`. On the next run, only commits *after* these cursors (via `<cursor>..<branch>` rev-list ranges) are considered "new" and subject to the bucketing algorithm in §1; anything at-or-before the cursor is assumed already reconciled — **except** case (a)'s divergence check (§4), which specifically re-examines already-cursored commits to detect post-hoc edits, so the cursor alone isn't sufficient to skip work entirely, only to skip re-bucketing/re-ordering already-settled history.

Rationale for refs over trailers-on-unclean-commits: cursors are per-branch metadata about the *reconstruction process*, not properties of any single commit, and don't need to be human-visible in commit messages; a ref is the idiomatic git-native place for "where did we last get to" (same category as `FETCH_HEAD`, rebase's stored state, etc.), and it composes cleanly with force-pushes to clean/history since the cursor just needs updating, not rewriting history.

Flag: these refs need to be included in whatever the CI/hook fetches, and must not accidentally get pushed to `origin` (they're not under `refs/heads/`, so the existing push-name-policy in `push_checks.py` — which only classifies `refs/heads/*`-shaped branch names — is naturally unaffected, but worth double-checking `git push` invocations elsewhere in the tooling use explicit refspecs rather than `--all`/`--mirror` that could leak these).

---

## 4. Detecting divergence (case a)

Scenario: unclean already has a commit `U` whose trailer-derived correlation says clean commit `C` (and/or history commit `H`) came from it, but `C`'s current content no longer matches what a fresh forward-split of `U` would produce for the code side (someone amended `C` directly during review).

### 4.1 Detection mechanism

For every matched key already covered by the cursor (i.e., "should already be reconciled"), recompute what the *current* `C`/`H` pair's combined tree-delta would be per §2.2, and compare against `U`'s actual delta from its unclean parent (`diff_paths(U.parent, U)`). If they match (byte-for-byte blob equality per path), nothing to do — case a's "already reconciled" branch. If they differ, this is divergence.

Note this comparison must be robust to **metadata-only differences** (e.g. clean's subject was rewritten, but the tree content is identical) — those should NOT trigger divergence-handling, only tree/content differences should. So: diff at the tree/path level, not commit-message level, for the "did content diverge" test; a pure message reword on clean with no tree change is not divergence requiring an unclean update (though it might be worth separately noting, since a future re-split from the amended unclean commit would carry over unclean's old message, potentially reverting the reviewer's reword — see §4.3 caveat).

### 4.2 Recommended reaction: amend `U` in place, then propagate forward via a rebase of unclean commits on top of it (not "add a sync-back commit on top")

Reasoning:
- Adding a new commit on top of unclean that "fixes up" `U`'s content would leave two commits (the stale original `U` plus a patch), which pollutes unclean's history and, more importantly, breaks the very trailer-correlation this whole system depends on — the *next* forward split of unclean would treat the fixup commit as a brand-new commit with no trailer, producing yet another downstream clean/history pair unrelated to the one that caused it, compounding drift rather than resolving it.
- Amending `U`'s content directly at its position, then replaying (rebasing) every unclean commit after `U` on top of the amended version (since their trees are keyed off the old `T_prev` chain), correctly reflects "the source of truth for this content is now clean/history's edited version" and keeps a linear, single-commit-per-logical-change unclean history — matching what a human directly editing unclean would have produced if they'd made the same edit there instead of on clean.
- This does rewrite unclean history (changes `U`'s sha and every descendant's sha). Since unclean is explicitly a working/scratch branch (per `push_checks.py`'s policy: unclean must never be pushed to `origin`, and has no content restrictions — i.e. it's designed to be disposable/reconstructable), history-rewriting here is consistent with its documented nature, unlike rewriting `clean` or `history` which are meant to be more stable/reviewable.

### 4.3 Confirmation gate — recommend an explicit flag, not fully automatic

I recommend divergence-triggered rewriting require an explicit opt-in flag (e.g. `--allow-diverge-rewrite`, default off) rather than happening silently on every run, for two reasons:
1. Rewriting unclean's history changes shas that any local checkout/working branch based on unclean would need to be aware of (even though unclean is "disposable" in principle, a developer might currently be sitting on a local branch built on top of it).
2. The metadata-resolution judgment calls in §2.3 (which subject to prefer, whether to append) mean the rewritten `U` might not be exactly what a user expects; presenting a diff/summary of "unclean commit `U` (`abc123`) will be rewritten because clean/history changed since last sync" and requiring confirmation (or `--yes`) before proceeding is safer than silent automatic rewriting, especially since this is explicitly called out by the user as the trickiest area. Without the flag, the tool should detect divergence, print a report (which commits, what changed), and exit nonzero without modifying unclean — a dry-run-by-default posture.

This mirrors the existing codebase's general caution pattern (`git_ops.py`/`push_checks.py` treat unclean as unrestricted but still explicit/auditable rather than silently mutating).

---

## 5. Ordering strategy — concrete sort key (consolidating §1.3)

Final concrete sort key per grouped item, ascending:

```
sort_key(item) = (
    unclean_source_order.index(item.key) if item.key in unclean_source_order else float("inf"),
    # tiebreak group for unmatched items sharing the same "preceding matched index":
    item.preceding_matched_index,          # -1 if before all matched items
    item.committer_date,                   # ISO 8601 string, sorts correctly
    0 if item.origin_branch == "clean" else 1,  # clean-before-history tiebreak on exact date tie
)
```

Where matched items get `unclean_source_order.index(...)` as the dominant key (making the second/third/fourth components irrelevant for them, since indices are unique), and unmatched items get `inf` for the first component and fall through to the interleaving logic. This directly implements: matched pairs are ordered by their position in the *original* unclean branch (ground truth, when known); unmatched/new commits are slotted in based on which matched commit immediately preceded them on their own branch, then date, then a clean-before-history tiebreak.

As discussed in §1.3, the case of **matched-pair order disagreeing with a *known* `unclean_source_order`** (i.e. clean or history got reordered relative to the ground truth) is treated as an error requiring `--force`, not resolved by this sort key silently.

---

## 6. Starting point

Confirm the spec's intent concretely: `history` branches, when first created by the forward direction, must fork from a specific commit of `ai/history/master` (not just "whatever `ai/history/master` happens to be right now" — that could move via `update-history-master` while a feature branch's `history` sits stale). Recommend the forward-direction agent record this via a **ref**, symmetric to §3.2's cursor design: `refs/base-split/history-master-fork-point/{branch}` → the `ai/history/master` sha the branch's `history` diverged from. This reconstruction direction then reads that ref (rather than re-deriving via `git merge-base history/{branch} ai/history/master`, which is fragile if `ai/history/master` has since been rebased/moved past that point — merge-base could resolve to the *wrong*, more-recent common ancestor after a rebase, silently truncating the intended base). If that ref doesn't exist (e.g. `history` was hand-created without going through the forward tool), fall back to `git merge-base` with a warning that the fallback is being used and may be wrong if `ai/history/master` has moved.

This is a cross-cutting concern the forward-direction sibling agent needs to also account for (it's the one creating `history` branches and thus the one that should write this ref) — flag as a coordination point, not something this reconstruction-direction plan can unilaterally guarantee.

The reconstructed unclean branch's first parent is exactly that recorded `ai/history/master` fork-point commit — matching the spec's "Start from `history/master` (the specific commit... the branch's history is based on)" literally.

---

## 7. CLI shape

Recommend **one `sync-splits` subcommand with a `--direction` flag**, rather than a separate subcommand, since both directions operate on the same conceptual triple (clean/unclean/history) and users likely want "sync everything, pick whichever direction is missing/stale" as the common case eventually (even though today we're implementing them as explicit choices). Concrete shape for now:

```
split.py sync-splits <branch> --direction=to-unclean [--allow-diverge-rewrite] [--dry-run] [--force]
split.py sync-splits <branch> --direction=to-clean-history   # sibling agent's direction
```

- `<branch>` is the base branch name (e.g. `feature/ABC-123`), used with `branches.unclean_name`/`history_name` to resolve actual ref names — reusing the existing helpers in `branches.py` directly.
- `--direction=to-unclean` invokes this design's logic.
- `--dry-run` (default-on behavior per §4.3, or an explicit flag if the sibling agent's direction defaults to writing) prints the planned commit sequence (bucket assignments, merge/cherry-pick groupings, and any detected divergence) without creating/moving any ref.
- `--force` overrides the hard-refusal cases (§1.3's topological-reorder mismatch, §3.1's path-content mismatch) — always paired with a clear printed explanation of what's being forced and why it was flagged.
- `--allow-diverge-rewrite` gates the case-a rewrite per §4.3; without it, divergence is reported but not acted on.
- No new argparse subparser needed beyond adding `sync-splits` to `cli.py`'s existing `subparsers.add_subparser(...)` pattern (mirroring how `check-push` is registered) — this is a small, additive change to `cli.py`'s `main()`.

If the sibling agent has already committed to a different shape (e.g. two separate subcommands `to-clean`/`to-unclean`), defer to whatever's simpler to keep both directions' CLI surface consistent — flagging this as coordination-needed rather than a hard requirement.

---

## 8. Tests

All using the existing `tests/test_git_split_*.py` real-temp-repo convention (`tempfile.TemporaryDirectory` + `git init` + the `git()`/`make_commit()` helpers already established in `test_git_split_push_checks.py`), add `scripts/°base/tests/test_git_split_sync_unclean.py` with scenarios:

1. **Code+history pair merge**: build a fake "original unclean" commit touching both a code path and `ai/`-path file, hand-craft resulting `clean` commit (code delta) and `history` commit (ai delta) each carrying `X-Base-Split-Source: <original-sha>` trailers (via `git commit --trailer` or `git interpret-trailers`), run reconstruction, assert the new unclean commit's tree matches the original's tree exactly, and that author/committer/message resolution follows §2.3's rules (test both subject-match and subject-diverged sub-cases).
2. **Code-only commit**: a clean commit with no trailer (a hotfix), assert it's cherry-picked onto unclean unchanged, in the correct position relative to surrounding matched commits (date-based interleave, §1.3/§5).
3. **History-only commit**: symmetric, a `CLAUDE.md`-only history commit with no trailer.
4. **Divergence detection, no rewrite by default**: amend a clean commit's content after it was "already reconciled" (simulate cursor via manually setting the `refs/base-split/unclean-cursor/*` ref), run reconstruction without `--allow-diverge-rewrite`, assert it reports divergence and exits nonzero without touching unclean.
5. **Divergence rewrite with flag**: same setup, run with `--allow-diverge-rewrite --yes`, assert unclean's corresponding commit is amended and any descendant commits are correctly rebased on top (tree content check on the new tip).
6. **Idempotent re-run**: run reconstruction twice with no new commits in between; assert the second run is a no-op (cursor refs unchanged, unclean ref unchanged) — guards against the double-apply risk named in §3.2.
7. **Dangling trailer sha (§1.4)**: a clean commit whose `X-Base-Split-Source` points at a sha not present in the repo (simulate by fabricating a trailer with a random 40-hex sha) — assert it's treated as unmatched/code-only rather than erroring.
8. **Ordering ambiguity warning (§1.3 fallback)**: two unmatched commits (one clean, one history) with identical committer dates and no preceding matched commit — assert deterministic clean-before-history tiebreak and that no crash occurs.
9. **Duplicate trailer collision (§1.2)**: two clean commits sharing the same `X-Base-Split-Source` value — assert reconstruction refuses with a clear error naming both shas.

---

## Summary of flagged unresolved ambiguities (explicit, not silently resolved)

- **§1.3**: matched-commit relative order disagreeing with a *known* `unclean_source_order` (i.e., clean or history got reordered by a human relative to the original unclean sequence) — recommend hard-refuse + `--force` fallback to "trust clean's order," but this is a judgment call I'm not fully confident is the right default; needs explicit confirmation.
- **§2.3**: subject/body merge heuristic when clean and history messages diverge (prefer clean's subject, append non-boilerplate history content) — reasonable but not the only defensible choice (e.g. always concatenating, or always preferring whichever side has a "richer" trailer kind).
- **§4.3**: divergence handling defaults to dry-run/report-only, requiring an explicit `--allow-diverge-rewrite` flag before rewriting unclean history — recommended over silent auto-rewrite or silent auto-refuse, but genuinely a product decision the user should confirm.
- **§6/§7**: cross-cutting coordination points (the `ai/history/master` fork-point ref, and the exact CLI subcommand/flag shape) depend on decisions the sibling forward-direction agent is making concurrently and aren't fully within this plan's control.

### Critical Files for Implementation
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/branches.py
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/classify.py
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/git_ops.py
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/cli.py
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/push_checks.py
- /home/user/git/luckydonald/base/ai/°base/todo.md
- /home/user/git/luckydonald/base/scripts/°base/tests/test_git_split_push_checks.py