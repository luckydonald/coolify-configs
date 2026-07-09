# Plan: `sync-splits` — clean & history generation (unclean → clean, unclean → history)

Scope confirmed: only clean-generation and history-generation logic. Unclean-reconstruction and `update-history-master`/`rebase-branches-to-master` are explicitly out of scope (owned by sibling agents).

## 0. Files touched/added

New modules in `scripts/°base/git/°split_lib/`:
- `trailers.py` — pure trailer read/write helpers (parse commit message body+trailers, format new trailers).
- `tree_split.py` — pure-ish plumbing helpers for building filtered trees (some take `cwd`, i.e. subprocess glue, kept separate from `git_ops.py` because it's a distinct concern — tree construction vs. simple read queries). Given the existing split between `git_ops.py` (glue) and `push_checks.py` (pure), I will keep `tree_split.py` as glue (it shells out constantly) and put the pure decision logic (which paths go where, which commits get created) into a new `sync_splits.py` that composes `tree_split` + `classify` + `branches`.
- `sync_splits.py` — orchestration: cursor discovery, commit-by-commit replay loop, dataclasses for the plan of what to create, calling `tree_split` to do it.
- `cli.py` — extended with a `sync-splits` subparser wired to `sync_splits`.

New tests: `scripts/°base/tests/test_git_split_tree_split.py`, `test_git_split_sync_splits.py`, `test_git_split_trailers.py` (or fold trailers tests into sync_splits tests — trailers.py is small enough it may not need its own file, but keep symmetry with existing one-module-one-test-file convention).

## 1. Trailer schema (exact keys)

All values are plain trailer values (no embedded newlines; git trailers are single-line `Key: value`).

| Trailer key | Present on | Value | Purpose |
|---|---|---|---|
| `X-Base-Split-Source` | every generated `clean` and `history` commit | full 40/64-hex unclean SHA | the unclean commit this generated commit was derived from |
| `X-Base-Split-Kind` | every generated `clean` and `history` commit | `code` \| `history` \| `mixed` | classification of the *source* unclean commit (`code` = pure-code, `history` = ai-only, `mixed` = both) — same value on the clean and history counterpart of the same source commit, so unclean-reconstruction can recognize pairs by matching `X-Base-Split-Source` even before parsing trees |
| `X-Base-Split-Counterpart-Tree` | history commits only, and only when `Kind` is `code` or `mixed` (i.e. whenever a clean commit was/will be created for this source) | git tree SHA of the **clean** commit's tree | lets unclean-reconstruction verify "the clean commit I'm about to cherry-pick still matches the tree I recorded when history was generated" — a cheap integrity check without needing to look up the clean commit by trailer scan |
| `X-Base-Split-Branch` | one dedicated metadata commit per history branch, at its root (see §5) | the clean/unclean base branch name (e.g. `feature/ABC-123`) | records which base branch this history-branch line belongs to, independent of the ref name (refs can be renamed/deleted; the commit itself stays self-describing) |
| `X-Base-Split-Root-Unclean` | same dedicated root metadata commit | the unclean SHA this history branch's replay started from (i.e. the parent-commit boundary — either the unclean root commit for a brand-new branch, or "N/A" if history existed already from a previous sync... see §5 for exact semantics) | anchors "which unclean-branch commit is this whole history branch built on top of," per the spec: "which base commit this whole history branch stems from" |

No dedicated metadata commit is needed for `clean` — clean's root is simply `master`'s tip at branch-creation time, which is directly observable as the clean branch's actual git parentage (first-parent chain hits a commit that's an ancestor of `master`). History's tree, by contrast, doesn't get this for free because `ai/history/master`'s tip *is* itself a meaningful ancestor, so a plain "first commit not reachable from ai/history/master" walk already tells you the boundary — meaning the `X-Base-Split-Root-Unclean` root-commit is arguably redundant with git parentage too. **I'm including it anyway** because (a) it's cheap, (b) it makes the boundary discoverable via `git log --grep`/trailer-scan without doing a merge-base walk, and (c) unclean-reconstruction (owned by the sibling agent) may want it as a stable anchor independent of how history/master itself gets rebased later by `update-history-master`. Flagging this as a judgment call, not a hard requirement — the sibling agent designing unclean-reconstruction should confirm they actually want it before we commit to maintaining it.

**Trailer ordering/formatting**: use `git interpret-trailers --trailer "Key: value" --trim-empty` to append trailers to a commit message: `git interpret-trailers --trailer "X-Base-Split-Source: <sha>" --trailer "X-Base-Split-Kind: <kind>" [--trailer "X-Base-Split-Counterpart-Tree: <tree>"]` piped a base message, producing the final commit message text passed to `commit-tree -F -`. Use `--parse` to read trailers back out of an existing commit's `%B` when scanning for the cursor.

## 2. Cursor tracking (idempotency)

**Mechanism: trailer-scan on the target branch's tip, not a separate ref.**

Rationale: a separate ref (`refs/base-split/<branch>/clean-cursor`) adds a second source of truth that can drift from the branch tip (e.g. if someone manually commits to `clean`, or force-pushes, or the ref update fails after the branch commit succeeds). The branch tip itself is authoritative and atomic — once the commit exists, the cursor exists; there's no two-step commit that can partially fail. Given no existing ref-namespace precedent in this repo, and that this data is 1:1 derivable from the branch itself, avoid introducing one.

Concretely:

```python
def find_last_synced_source(target_ref: str, cwd: Path) -> str | None:
    """Return the X-Base-Split-Source trailer value of target_ref's tip, or None
    if target_ref doesn't exist yet or its tip has no such trailer (shouldn't
    happen for branches we created, but guards against manual commits)."""
```

Implementation: `git log -1 --format=%H%x00%B <target_ref>` (rev-parse existence check first via `git_ops.rev_exists`-style `git rev-parse --verify --quiet <target_ref>`), then `git interpret-trailers --parse` on the body, extract `X-Base-Split-Source`.

To resume replay: once we know the last-synced unclean SHA, compute the remaining commits to replay with:
```
git rev-list --reverse <last_synced_sha>..<unclean_tip>
```
— exactly mirroring the existing `commits_new_to_remote` idiom in `git_ops.py` (`rev-list --reverse A..B`), for consistency. If `last_synced_sha` is `None` (branch doesn't exist yet), replay is `git rev-list --reverse <root>..<unclean_tip>` where `root` is determined per §5 (new-branch case): actually for a brand new branch we want *all* unclean commits on that branch, i.e. `git rev-list --reverse <base>..<unclean_tip>` where `<base>` is where `unclean` itself branched off (typically `ai/history/master`'s historical ancestor, or simply: walk from unclean's root). In practice: use `git merge-base --fork-point`-style reasoning is unreliable across rebases; simplest robust approach — **do not try to detect unclean's own start point from history; instead always replay unclean commits that are reachable from `unclean_tip` and not yet reachable from the target's recorded cursor.** For a brand-new target branch, that means replaying literally every commit `git rev-list --reverse <unclean_tip's root>..<unclean_tip>`, using `git rev-list --max-parents=0 <unclean_tip>` to find the root only as a fallback if we truly need a lower bound — but actually we don't need a lower bound at all for a new branch: just `git rev-list --reverse <unclean_tip>` and replay every commit from the very first, since clean/history are starting fresh anyway (their own tree starts empty except for the master/history-master base — see §5). **Correction/simplification**: since `sync-splits` always creates clean starting from `master` and history starting from `ai/history/master`, and unclean itself was originally created by branching off `ai/history/master` (per the todo.md spec — "unclean starts on `ai/history/master`" is stated for the *reconstruction* direction, but for a genuinely fresh feature branch, unclean is presumably branched by the user off current `master`/`ai/history/master` by convention) — the correct universal replay range for a new target is `git rev-list --reverse <merge_base(unclean_tip, master)>..<unclean_tip>` for clean, and `git rev-list --reverse <merge_base(unclean_tip, ai/history/master)>..<unclean_tip>` for history. This correctly limits replay to "commits unique to this feature branch," not the shared history already in master. **This is the one place I recommend explicit confirmation from the user before implementing**, since it depends on how unclean branches are actually created in practice (a convention not yet codified in code) — flagging as an open edge case rather than guessing silently.

## 3. Tree-splitting algorithm

Core primitive, in `tree_split.py`:

```python
def filtered_diff_for_commit(sha: str, cwd: Path) -> list[tuple[str, str, str | None, str | None]]:
    """Return (status, path, old_path_or_None, ...) tuples for sha vs its first
    parent, using raw diff-tree output, unfiltered (filtering happens by caller)."""
```

Concretely:
```
git diff-tree -r --no-commit-id --name-status -M <sha>^ <sha>
```
(root commits: diff against the empty tree via `git diff-tree -r --no-commit-id --name-status --root <sha>`, or equivalently `git diff-tree -r --no-commit-id --name-status -M <empty-tree-sha> <sha>` where the empty tree sha is the well-known constant `4b825dc642cb6eb9a060e54bf8d69288fbee4904`). Using `-M` (rename detection) is required per the task's rename-crossing-boundary requirement — see below. Status codes: `A`, `M`, `D`, `R100`/`R<NN>` (rename, two paths tab-separated), `C<NN>` (copy, rare, treat like add-of-new-path plus no-op on old path).

Then **filter and translate to a per-target-tree update list**:

```python
def build_filtered_tree(
    parent_target_tree: str,   # tree-ish of the target branch's current tip (clean or history)
    sha: str,                  # unclean commit being replayed
    cwd: Path,
    *,
    keep: Callable[[str], bool],  # classify.is_ai_base_path for history, `not is_ai_base_path` for clean
) -> str:
    """Return a new tree SHA = parent_target_tree with the subset of sha's
    changes whose paths satisfy `keep` applied on top."""
```

Algorithm:
1. `git read-tree <parent_target_tree>` into a **fresh temporary index** — use `GIT_INDEX_FILE=<tmp>` env var per invocation rather than the repo's real index, so this never disturbs the user's working tree/index (no existing precedent for `GIT_INDEX_FILE`, but it's the standard way to do index plumbing headlessly and is safer than touching `.git/index`; explicitly note this as a new-to-this-repo technique).
   ```
   GIT_INDEX_FILE=<tmp_index> git read-tree <parent_target_tree>
   ```
2. Get the raw diff for `sha` (rename-aware, as above), and for each entry whose path(s) pass `keep`:
   - `A path` / `M path`: look up the blob mode+sha at `path` in `sha`'s tree via `git ls-tree <sha> -- <path>` (gives `<mode> blob <blob-sha>\t<path>`), then stage it:
     ```
     GIT_INDEX_FILE=<tmp_index> git update-index --add --cacheinfo <mode>,<blob-sha>,<path>
     ```
   - `D path`: remove it:
     ```
     GIT_INDEX_FILE=<tmp_index> git update-index --force-remove <path>
     ```
   - `R<NN> old_path new_path`: **rename handling, including boundary-crossing renames** (the required test scenario, e.g. `src/x.py` → `ai/x.py`). Decompose a rename into an independent delete-of-old + add-of-new, each filtered by `keep` *separately*:
     - if `keep(old_path)`: emit a `D old_path` against the target tree.
     - if `keep(new_path)`: emit an `A new_path` (mode/blob from `sha`'s tree at `new_path`) against the target tree.
     - This means a rename crossing the boundary (code→ai) becomes, on the **clean** tree: a pure delete of `old_path` (no add, since `new_path` fails `keep`). On the **history** tree: a pure add of `new_path` (no delete, since `old_path` never existed in history's tree — `is_ai_base_path` was false for it). This is exactly correct and requires no special-casing beyond "evaluate `keep` independently on old and new path and treat as two ops," which is simpler than trying to preserve the rename as a rename in the split tree (git has no native concept of "half a rename" and there's no benefit to forcing one — the target trees are semantically different content spaces, so a rename in unclean's tree does not imply a rename in either split tree).
   - `C<NN> old_path new_path` (copy): same decomposition, but never delete `old_path` (copies don't remove the source). If `keep(new_path)`, add it; ignore `old_path` entirely since it was untouched by this commit as far as diff-tree copy detection goes (default `diff-tree` doesn't detect copies without `-C`; using only `-M` avoids ever seeing `C` status, so this branch is dead code in practice — note it defensively but don't rely on it triggering).
3. If **no** filtered ops applied at all (e.g. clean-generation for an ai-only commit, or history-generation for... never happens since every commit gets a history entry, but could happen if a pure-code commit's diff has zero ai-paths and we still need the tree — which is fine, tree is just unchanged):
   ```
   GIT_INDEX_FILE=<tmp_index> git write-tree
   ```
   still works and simply returns back `parent_target_tree`'s tree unchanged (identical tree) — this is exactly the "empty diff, same tree as parent" case history needs for pure-code source commits.
4. `GIT_INDEX_FILE=<tmp_index> git write-tree` → returns the new tree SHA. Clean up: delete `<tmp_index>` file afterward (use `tempfile.NamedTemporaryFile(delete=False)` + `unlink` in a `finally`, consistent with the scratchpad-cleanup style already used in `_lib.py`'s `_restore_staged`).

This design deliberately walks **only the single commit's delta** (`diff-tree <sha>^ <sha>`), not the full tree at `sha`, and applies it onto the *previous target-branch tree* (clean's or history's last commit's tree) — satisfying the requirement that unclean's full tree at any point doesn't correspond to clean/history's tree, only per-commit deltas do. Each replay step is O(commit's changed files), not O(repo size), and correctness follows inductively: target tree after commit N = target tree after commit N-1 with commit N's filtered delta applied — exactly mirrors what N's own delta did to unclean's tree, projected through the `keep` filter.

Edge case flagged as unresolved: **mode-only changes** (e.g. chmod +x) on a kept path — `diff-tree --name-status` reports these as `M` too, and the "look up mode+blob from ls-tree at sha" step naturally picks up the new mode, so this is actually handled correctly by construction; no special case needed. Flagging only because I want to be explicit it's covered, not because it's a gap.

Edge case genuinely unresolved: **symlinks and submodules** (gitlink mode `160000`) — `update-index --cacheinfo` with the mode taken verbatim from `ls-tree` should work identically (mode `120000` for symlinks, `160000` for submodules with the commit-sha as "blob" sha), but I have not verified this repo has any submodules to test against, and submodule gitlinks crossing the ai/code boundary is an exotic enough scenario that I'd want a real test before trusting it blindly.

## 4. Commit construction

```python
def make_split_commit(
    parent: str | None,       # target branch's current tip sha, or None for first commit on a fresh branch
    tree: str,
    source_sha: str,
    kind: str,                # "code" | "history" | "mixed"
    *,
    author_name: str, author_email: str, author_date: str,
    committer_name: str, committer_email: str, committer_date: str,
    subject: str,
    body: str,                # original body minus any trailers (stripped via `--parse`/`--only-trailers` inverse — see below)
    extra_trailers: dict[str, str],
    cwd: Path,
) -> str:                     # returns new commit sha
```

Metadata policy — exact choices and rationale:
- **Author name/email/date: preserved verbatim** from the unclean source commit (`%an`, `%ae`, `%ad` via `git log -1 --format=%an%x00%ae%x00%ad -- <sha>`, using `--date=raw` to get a re-injectable `<epoch> <tz>` string). Reason: the human (or AI-acting-as-human) who wrote the code should stay attributed; splitting content should not erase authorship, and clean/history are supposed to be believably-normal git history.
- **Committer name/email: the tool's own identity** (reuse the same "Lucky Lucy" bot identity pattern already established in `rebase_strip_claude_authorship.py`'s `NEW_NAME`/`NEW_EMAIL` constants, or a dedicated "base-split" identity — recommend introducing a small shared constant, maybe in a new `identity.py` or directly in `sync_splits.py`, e.g. `SPLIT_BOT_NAME`/`SPLIT_BOT_EMAIL`). Reason: committer identity conventionally records *who ran the tool/rewrite*, distinct from authorship; this also makes generated split-commits trivially greppable/attributable as tool output versus organic commits (useful for debugging and for the reconstruction step to trust/distrust).
- **Committer date: current time at generation time** (not copied from source) — because committer-date-changes-on-rewrite is standard git convention (e.g. rebase updates committer date but not author date), and because the cursor/replay semantics don't depend on committer date at all, only on the `X-Base-Split-Source` trailer.
- **Subject: preserved verbatim** from unclean's subject — this is what makes clean/history readable as sensible standalone histories; do not regenerate/summarize it.
- **Body: preserved verbatim, with pre-existing trailers stripped and split-trailers appended.** Use `git interpret-trailers --parse <(printf '%s' "$body")` to detect if unclean's own commit already carries trailers (e.g. from its own history if this is a re-run — shouldn't normally happen since we're reading unclean, not clean/history, but defensive); strip pre-existing `X-Base-Split-*` trailers if present (shouldn't be, but idempotency-safety) via `git interpret-trailers --trailer "X-Base-Split-Source" --trailer-delete`-equivalent (actually `interpret-trailers` doesn't have a delete flag pre-2.44 reliably — simpler: just don't special-case this, since unclean commits are never themselves the output of a previous sync-splits run, only their *source*; this concern only matters if the tool were ever run on its own output, which it isn't). So: keep it simple — body = unclean's full `%b` (everything after the blank line following the subject) unmodified, then append the new trailers via `git interpret-trailers --trailer ...`.

Building the commit object (mirrors `hash-object -w` style already used in `_lib.py`, extended to full commits):
```
git commit-tree <tree> [-p <parent>] -F <message-file>
```
with environment variables set for author/committer rather than relying on config defaults:
```python
env = {
    **os.environ,
    "GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email, "GIT_AUTHOR_DATE": author_date,
    "GIT_COMMITTER_NAME": committer_name, "GIT_COMMITTER_EMAIL": committer_email, "GIT_COMMITTER_DATE": committer_date,
}
subprocess.run(["git", "commit-tree", tree, *(["-p", parent] if parent else []), "-F", str(message_file)], cwd=cwd, env=env, capture_output=True, text=True, check=True)
```
No `-p` at all for the very first commit on a fresh clean/history branch is wrong per §5 — clean/history always start *with* a parent (master's tip / ai/history/master's tip), so `-p` is always supplied except in the degenerate case master/ai-history-master itself doesn't exist yet (shouldn't happen in a real repo, but a brand-new empty repo has no master commit at all — treat as an explicit precondition failure, error out rather than silently creating a rootless clean branch).

The message file (`-F <path>`) is built as: `f"{subject}\n\n{body}\n"` piped through `git interpret-trailers --in-place --trailer "X-Base-Split-Source: {source_sha}" --trailer "X-Base-Split-Kind: {kind}" [...]`. Write it to a scratch temp file in the scratchpad-style pattern (`tempfile.NamedTemporaryFile`), run `interpret-trailers --in-place <path>`, read it back for `-F`.

Empty body handling: `interpret-trailers` requires a blank-line-separated trailer block; if body is empty, this still works correctly (it appends a blank line + trailer block after the subject) — this is the standard "trailers with no body" case git handles fine, no special-casing needed.

Finally: `git update-ref refs/heads/<clean-or-history-branch> <new-commit-sha>` (create-or-move; use `git update-ref refs/heads/<ref> <new_sha> <old_sha_or_absent>` — for the very first update when the branch doesn't exist, omit the old-value arg; for subsequent updates in the same run, pass the previous new-commit-sha as old-value to catch races/bugs defensively, matching the "check expected old value" safety idiom git plumbing supports).

## 5. New branch creation

```python
def ensure_branch_started(ref: str, base_ref: str, cwd: Path) -> str:
    """Return ref's current tip sha, creating it pointing at base_ref's tip if
    ref doesn't exist yet."""
```
- `clean` (`<branch>`): if `git rev-parse --verify --quiet refs/heads/<branch>` fails, create it via `git update-ref refs/heads/<branch> $(git rev-parse master-branch-name)` — no commit created, it's literally master's tip; commits are added on top starting from the first replayed unclean commit whose filtered diff is non-empty-worthy (i.e., first non-ai-only commit).
- `history` (`ai/history/<branch>`): analogous, but based on `ai/history/master`'s tip. **Additionally**, immediately after creating the ref, create the one dedicated root metadata commit described in §1 (`X-Base-Split-Branch`, `X-Base-Split-Root-Unclean`) as an empty-tree-diff commit on top of `ai/history/master`'s tip, *before* replaying any unclean commits — this commit's tree is byte-identical to `ai/history/master`'s tree (no filtered delta at all, it's pure metadata), constructed the same way as the "no ops" case in §3 step 3 (write-tree on an untouched index = same tree as parent). This satisfies "a single trailer per commit doesn't capture which base commit this whole history branch stems from" by giving it a home in a real commit rather than overloading the first real replayed commit's trailers.
- Use `branches.detect_main_branch(repo_root)` (existing function) to resolve `master`'s actual name, and `branches.history_name(main_branch)` (existing) to resolve `ai/history/master`'s ref name — both already implemented, just call them.

## 6. Commit-by-commit replay loop (orchestration)

```python
@dataclass(frozen=True)
class SyncSplitsResult:
    branch: str
    clean_ref: str | None          # None if nothing to do for clean (n/a — always attempted)
    clean_commits_created: int
    clean_commits_skipped_ai_only: int
    history_ref: str
    history_commits_created: int

def sync_branch(base_branch: str, *, repo_root: Path, main_branch: str) -> SyncSplitsResult:
    unclean_ref = branches.unclean_name(base_branch)
    clean_ref = base_branch
    history_ref = branches.history_name(base_branch)

    clean_tip = ensure_branch_started(clean_ref, main_branch, repo_root)
    history_tip = ensure_branch_started(history_ref, branches.history_name(main_branch), repo_root)
    # (history_tip creation also emits the root metadata commit per §5, updating history_tip)

    unclean_tip = <resolve ref>
    last_clean_source = find_last_synced_source(clean_ref, repo_root)
    last_history_source = find_last_synced_source(history_ref, repo_root)

    clean_replay_shas = commits_to_replay(unclean_ref, last_clean_source, repo_root)   # see §2
    history_replay_shas = commits_to_replay(unclean_ref, last_history_source, repo_root)

    for sha in clean_replay_shas:
        cls = classify.classify_commit(sha, git_ops.subject_for_commit(sha, repo_root), git_ops.changed_paths_for_commit(sha, repo_root))
        if cls.is_ai_only_commit:
            continue  # excluded from clean, still present on unclean/history
        tree = tree_split.build_filtered_tree(clean_tip_tree, sha, repo_root, keep=lambda p: not classify.is_ai_base_path(p))
        clean_tip = make_split_commit(clean_tip, tree, sha, kind_for(cls), ...)

    for sha in history_replay_shas:
        cls = classify.classify_commit(...)
        tree = tree_split.build_filtered_tree(history_tip_tree, sha, repo_root, keep=classify.is_ai_base_path)
        counterpart_tree = <clean commit's tree sha if a clean commit exists for this sha> else None
        history_tip = make_split_commit(history_tip, tree, sha, kind_for(cls), extra_trailers={"X-Base-Split-Counterpart-Tree": counterpart_tree} if counterpart_tree else {}, ...)
```

`kind_for(cls)`: `"history"` if `is_ai_only_commit`, `"code"` if not `is_ai_tainted_commit` (pure code, per Phase 1's own three-way split: ai_only / tainted-but-not-ai-only(=mixed) / not tainted at all(=code)), else `"mixed"`.

**Important subtlety on `X-Base-Split-Counterpart-Tree`**: since clean and history are replayed in two independent loops (potentially at different cursors if run incrementally at different times — e.g. if a previous `sync-splits` run updated history but crashed before updating clean), the history loop cannot always assume the clean commit for the same source sha already exists on `clean` in *this* run. Resolve by **running the clean loop to completion first, then the history loop**, and looking up the counterpart tree by trailer-scanning `clean_ref`'s new commits (which we just built and have the tree SHAs for locally, no need to scan) — i.e., accumulate a local `dict[source_sha, clean_tree_sha]` while building clean commits in loop 1, and consult it in loop 2. For source commits whose clean counterpart was created in a *previous* run (not this one), we still have it in `clean_ref`'s history — but the "always run clean before history in the same invocation" approach only covers the current run's overlap window; a fully general lookup would need a trailer-index scan of the whole `clean_ref` history for `X-Base-Split-Source: <sha>`, which is O(clean branch length) per lookup unless cached. **Recommendation**: build a `dict[source_sha, clean_tree]` once per `sync_branch` call by walking `clean_ref`'s full history once (`git log --format=%H%x00%(trailers:key=X-Base-Split-Source,valueonly)%x00%T <clean_ref>`), merged with the newly-created commits from loop 1. This is O(branch length) once per run, acceptable for realistic branch sizes (dozens to low hundreds of commits), and avoids repeated re-scans.

Order: **clean loop before history loop**, always, within one `sync_branch` invocation.

## 7. CLI shape

```python
sync_splits = subparsers.add_parser(
    "sync-splits", help="Generate/update clean and history branches from an unclean branch."
)
sync_splits.add_argument("branch", nargs="?", help="Base branch name (clean-format). If omitted, sync all branches with an existing ai/UNCLEAN/* counterpart.")
sync_splits.add_argument("--dry-run", action="store_true", help="Report what would be created without writing any refs/commits.")
```
Dispatch in `main()`:
```python
if args.command == "sync-splits":
    root = git_ops.repo_root()
    main_branch = branches.detect_main_branch(root)
    targets = [args.branch] if args.branch else sync_splits.discover_unclean_branches(root)
    for b in targets:
        result = sync_splits.sync_branch(b, repo_root=root, main_branch=main_branch, dry_run=args.dry_run)
        print(f"{b}: clean +{result.clean_commits_created} (skipped {result.clean_commits_skipped_ai_only}), history +{result.history_commits_created}")
    return 0
```
`discover_unclean_branches`: `git for-each-ref --format=%(refname:short) refs/heads/ai/UNCLEAN/` then strip the prefix via `branches.base_name_from_unclean`.

`--dry-run` implementation note: since every git operation here is plumbing (`commit-tree`, `write-tree`, `update-ref`) rather than working-tree mutation, dry-run is straightforward — run everything through `write-tree`/`commit-tree` (these don't touch refs, they're pure object-creation, harmless to run and leave dangling objects) but skip the final `update-ref` calls, and skip creating the branch ref in `ensure_branch_started`. Dangling unreferenced objects from a dry run are harmless (subject to normal git gc) — acceptable tradeoff for a dry-run mode versus building a fully separate "simulate without any git calls" path.

## 8. Tests (`test_git_split_sync_splits.py`, `test_git_split_tree_split.py`)

Following the existing real-temp-repo `subprocess` + `tempfile.TemporaryDirectory()` pattern from `test_git_split_push_checks.py`, with a shared `git()`/`make_commit()` helper (import or duplicate from that file — recommend factoring `git()`/`make_commit()` into a small shared test-helper module, e.g. `scripts/°base/tests/_git_test_helpers.py`, since three test files will now want it).

Concrete scenarios:
1. **Pure-code commit**: unclean has one commit touching only `src/x.py`. Assert clean gets a new commit with tree containing `src/x.py`, trailers `X-Base-Split-Source`=sha, `X-Base-Split-Kind: code`. Assert history gets a new commit with tree identical to its parent (no `src/x.py`), same-tree-as-parent (assert `git rev-parse <hist_commit>^{tree} == git rev-parse <hist_commit>^^{tree}`), trailers `Kind: code`, `X-Base-Split-Counterpart-Tree` == the clean commit's tree sha.
2. **Pure-ai commit**: unclean commit touching only `ai/query.md`. Assert clean does **not** get a new commit (clean tip unchanged, `is_ai_only_commit` skip). Assert history **does** get a commit with tree containing `ai/query.md`, `Kind: history`, and **no** `X-Base-Split-Counterpart-Tree` trailer (no clean counterpart exists).
3. **Mixed commit**: unclean commit touching both `src/x.py` and `ai/query.md` in one commit. Assert clean gets a commit with only `src/x.py`; history gets a commit with only `ai/query.md`; both carry `Kind: mixed`; history's carries `Counterpart-Tree` pointing at clean's tree.
4. **Boundary-crossing rename**: commit that does `git mv src/x.py ai/x.py` (add prior commit creating `src/x.py`, then a rename commit). Assert the rename commit's effect on clean is a pure deletion (clean tree loses `src/x.py`, gains nothing) and on history is a pure addition (history tree gains `ai/x.py`, had nothing before). This is the key test proving §3's decompose-rename-into-independent-add/delete logic.
5. **Reverse-direction rename** (`ai/y.md` → `src/y.md`): symmetric check — clean gains it, history loses it (history tree previously had `ai/y.md`, drops it since new path isn't kept; the earlier history commit that introduced it must exist for this to be meaningful, so seed with a prior ai-only commit creating `ai/y.md` first).
6. **Non-crossing rename** (`src/a.py` → `src/b.py`, both code): assert clean handles it as a normal rename-equivalent result — old path gone, new path present with the same blob content (assert blob sha equality) — but confirm it's implemented as decompose-into-delete+add rather than a "true" git rename (no special assertion needed on rename-vs-delete+add distinction in the *result* since trees don't encode renames, only content — good, this test is really just confirming content correctness, not detecting an implementation difference).
7. **Fresh branch creation**: no `clean`/`ai/history/<branch>` ref exists yet before running `sync_branch`. Assert both get created, clean's first commit has `master` tip as parent (assert `git merge-base --is-ancestor master <first_clean_commit>`), history has the root metadata commit as its first new commit (with `X-Base-Split-Branch`/`X-Base-Split-Root-Unclean` trailers) directly on top of `ai/history/master`'s tip, followed by replayed commits.
8. **Idempotent re-run / incremental resume**: run `sync_branch` once with 2 unclean commits, then add a 3rd unclean commit and re-run. Assert: (a) the run doesn't recreate/duplicate the first two commits (branch length grows by exactly the expected number of new commits, not 2x), (b) `find_last_synced_source` correctly resolves to the 2nd commit's sha before the 2nd run, (c) the new commit's parent is the previous run's tip (chain integrity, `git rev-list --count clean-branch` matches expectations exactly).
9. **Root-commit / first-unclean-commit edge case**: unclean's very first commit (no parent) — confirm `filtered_diff_for_commit` correctly uses the `--root`/empty-tree-diff path rather than erroring on `<sha>^`.
10. **All-ai-only branch** (every unclean commit is ai-only): assert clean ref gets created (per spec, "not created yet will be added") but stays exactly at `master`'s tip with zero additional commits — verifies the "still create the branch, just no commits" path doesn't crash when the replay loop produces zero commits.
11. **`--dry-run`**: assert no ref changes occur (`git show-ref` before/after identical) despite the command reporting what it would do.

## Open items I flag rather than silently resolve

1. **Fresh-branch replay lower bound** (§2): whether a brand-new unclean branch's replay range for clean/history should be bounded by `merge-base(unclean_tip, master)` / `merge-base(unclean_tip, ai/history/master)`, or should simply replay every commit unconditionally from unclean's root. This depends on an unclean-branch-creation convention that isn't yet codified anywhere in code. Needs explicit confirmation before implementing `commits_to_replay`'s new-branch case.
2. **Whether `X-Base-Split-Root-Unclean` on the history root metadata commit is actually needed** given it's derivable from git parentage of `ai/history/master` vs. the branch — included per spec intent but flagged as possibly redundant; the sibling agent designing unclean-reconstruction should confirm they want to consume it before we commit to always writing/maintaining it.
3. **Committer bot identity** — recommend reusing/extending the existing `rebase_strip_claude_authorship.py` "Lucky Lucy" identity constants rather than inventing a new one, but this is a naming/branding choice for the repo owner, not something I can decide unilaterally.
4. **Symlink/submodule paths crossing the boundary** — mechanically should work via the same `ls-tree` mode/blob copy, but untested and flagged as lower-confidence.

### Critical Files for Implementation
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/classify.py
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/branches.py
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/git_ops.py
- /home/user/git/luckydonald/base/scripts/°base/git/°split_lib/cli.py
- /home/user/git/luckydonald/base/scripts/°base/ai/hooks/_lib.py (reference pattern for blob-staging idiom, lines 180-202)