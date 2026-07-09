"""(B) sync-splits reverse direction: `{branch}` (clean) + `ai/history/{branch}`
(history) -> `ai/UNCLEAN/{branch}` ("unclean reconstruction").

See the sync-splits design plan for the full picture. This module is the
*consumer* of the trailer schema written by the forward direction
(`sync_splits.py`): `X-Base-Split-Source` / `X-Base-Split-Kind` /
`X-Base-Split-Counterpart-Tree`. It does not import or call that module.

Design note (deliberate extension beyond the fixed schema, documented since
it wasn't spelled out in the plan): every commit *this* module creates on
`ai/UNCLEAN/{branch}` is stamped with its own trailer,
`X-Base-Unclean-Reconstructed-From`, whose value is the bucket key (either
the resolved unclean-source sha for a matched pair, or the commit's own sha
for an unmatched cherry-pick). This is what makes idempotent re-runs,
divergence-detection, and "which unclean commit did we build for key K"
lookups possible without a second side-channel database -- we just walk
`ai/UNCLEAN/{branch}`'s own history and read the trailer back off. The two
cursor refs (`refs/base-split/unclean-cursor/clean|history/{branch}`) are
still maintained per the plan, and used to bound how much of `clean`/
`history` needs to be re-scanned each run; the trailer-derived map is the
source of truth for "already reconstructed", since it's robust even if a
cursor sha gets rewritten out from under us (amended clean/history commits).
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import branches, classify, git_ops, identity, trailers, tree_ops
from .tree_ops import PathChange

SOURCE_TRAILER = "X-Base-Split-Source"
KIND_TRAILER = "X-Base-Split-Kind"
COUNTERPART_TREE_TRAILER = "X-Base-Split-Counterpart-Tree"

# Our own bookkeeping trailer -- see module docstring.
RECON_TRAILER = "X-Base-Unclean-Reconstructed-From"

_TRAILER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s")


def clean_cursor_ref(base_branch: str) -> str:
    return f"refs/base-split/unclean-cursor/clean/{base_branch}"


def history_cursor_ref(base_branch: str) -> str:
    return f"refs/base-split/unclean-cursor/history/{base_branch}"


def _author_info(sha: str, cwd: Path) -> tuple[str, str, str]:
    """Return (author_name, author_email, author_date), author_date as a raw
    git-acceptable date string (`%ad --date=raw`)."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%an%x1f%ae%x1f%ad", "--date=raw", sha],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    name, email, date = result.stdout.strip().split("\x1f")
    return name, email, date


def _committer_date_now() -> str:
    return "%d +0000" % int(time.time())


def _date_ts(author_date: str) -> int:
    """Sort-friendly integer timestamp out of a `%ad --date=raw` string
    (`"<unix-ts> <tz-offset>"`)."""
    return int(author_date.split()[0])


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    subject: str
    body: str
    author_name: str
    author_email: str
    author_date: str
    paths: list[PathChange]
    source: str | None
    kind: str | None


def read_commit_info(sha: str, cwd: Path) -> CommitInfo:
    message = git_ops.commit_message(sha, cwd)
    subject, _, body = message.partition("\n\n")
    subject = subject.rstrip("\n")

    source = trailers.read_trailer_value(message, SOURCE_TRAILER, cwd)
    kind = trailers.read_trailer_value(message, KIND_TRAILER, cwd)
    author_name, author_email, author_date = _author_info(sha, cwd)
    paths = tree_ops.raw_diff_for_commit(sha, cwd)

    return CommitInfo(
        sha=sha,
        subject=subject,
        body=body,
        author_name=author_name,
        author_email=author_email,
        author_date=author_date,
        paths=paths,
        source=source,
        kind=kind,
    )


def _key_for_info(info: CommitInfo, cwd: Path):
    """The bucket key for a single clean/history commit: its resolved
    unclean-source sha if the trailer is present and resolves to a real
    local commit, else a unique unmatched marker keyed on its own sha."""
    if info.source is not None and git_ops.rev_exists(info.source, cwd):
        return info.source
    return ("unmatched", info.sha)


def bucket_commits(
    clean_infos: list[CommitInfo],
    history_infos: list[CommitInfo],
    cwd: Path,
) -> dict:
    """Bucket clean+history commits by their `X-Base-Split-Source` value
    (falling back to a unique unmatched marker when the trailer is missing
    or dangling). Raises ValueError if two commits on the *same* side land
    on the same matched key -- per the plan, refuse rather than guess.
    """
    buckets: dict = {}

    for side, infos in (("clean", clean_infos), ("history", history_infos)):
        for info in infos:
            key = _key_for_info(info, cwd)
            bucket = buckets.setdefault(key, {"clean": None, "history": None})
            if bucket[side] is not None:
                raise ValueError(
                    f"Duplicate {side} commits share source key {key!r}: "
                    f"{bucket[side].sha} and {info.sha}"
                )
            bucket[side] = info

    return buckets


@dataclass(frozen=True)
class _OrderEntry:
    key: object
    bucket: dict
    preceding_matched_index: int
    date_ts: int
    is_clean: bool


def order_key(item: _OrderEntry, unclean_source_order: list[str]) -> tuple:
    """Sort key per the plan: matched items (their key is a real position in
    the known unclean lineage) sort by that position; unmatched items sort
    after *all* matched items (the `0`/`1` leading discriminant stands in for
    the plan's "matched-index vs. inf" contrast -- using a discriminant
    instead of a literal `float('inf')` avoids comparing `int` against
    `float` mid-tuple), then by the index of their nearest preceding matched
    sibling on their own branch, then commit date, then clean-before-history
    on an exact tie.
    """
    if isinstance(item.key, str) and item.key in unclean_source_order:
        return (0, unclean_source_order.index(item.key), 0, 0)
    return (1, item.preceding_matched_index, item.date_ts, 0 if item.is_clean else 1)


def _order_buckets(
    items: list[tuple],
    clean_infos: list[CommitInfo],
    history_infos: list[CommitInfo],
    unclean_source_order: list[str],
    cwd: Path,
) -> list[tuple]:
    clean_pos = {info.sha: i for i, info in enumerate(clean_infos)}
    history_pos = {info.sha: i for i, info in enumerate(history_infos)}

    def preceding_matched_index(side_infos: list[CommitInfo], idx: int) -> int:
        for j in range(idx - 1, -1, -1):
            k = _key_for_info(side_infos[j], cwd)
            if isinstance(k, str) and k in unclean_source_order:
                return unclean_source_order.index(k)
        return -1

    entries: list[_OrderEntry] = []
    for key, bucket in items:
        info = bucket["clean"] or bucket["history"]
        is_clean = bucket["clean"] is not None
        if isinstance(key, str) and key in unclean_source_order:
            preceding = -1  # unused by order_key's matched branch
        elif is_clean:
            preceding = preceding_matched_index(clean_infos, clean_pos[bucket["clean"].sha])
        else:
            preceding = preceding_matched_index(history_infos, history_pos[bucket["history"].sha])
        entries.append(
            _OrderEntry(
                key=key,
                bucket=bucket,
                preceding_matched_index=preceding,
                date_ts=_date_ts(info.author_date),
                is_clean=is_clean,
            )
        )

    entries.sort(key=lambda entry: order_key(entry, unclean_source_order))
    return [(entry.key, entry.bucket) for entry in entries]


def check_order_consistency(
    clean_infos: list[CommitInfo],
    history_infos: list[CommitInfo],
    unclean_source_order: list[str],
    *,
    force: bool = False,
) -> None:
    """Refuse if the relative order of matched keys implied by `clean_infos`
    or `history_infos` contradicts the known `unclean_source_order` -- i.e.
    someone reordered commits directly on `clean` or `history`. Raises
    ValueError naming the offending shas unless `force=True`.
    """
    if force or not unclean_source_order:
        return

    order_index = {sha: i for i, sha in enumerate(unclean_source_order)}
    for label, infos in (("clean", clean_infos), ("history", history_infos)):
        last_pos = None
        last_sha = None
        for info in infos:
            if info.source not in order_index:
                continue
            pos = order_index[info.source]
            if last_pos is not None and pos < last_pos:
                raise ValueError(
                    f"{label} branch order contradicts the known unclean lineage order: "
                    f"commit {info.sha} (source {info.source}, lineage position {pos}) "
                    f"comes after {last_sha} (lineage position {last_pos}); "
                    "pass force=True to override (falls back to trusting clean's order)."
                )
            last_pos, last_sha = pos, info.sha


def read_unclean_source_order(unclean_ref: str, cwd: Path) -> list[str]:
    """The ground-truth ordering of bucket keys already incorporated into
    `unclean_ref`, oldest first, read back off our own `RECON_TRAILER`."""
    tip = git_ops.rev_parse(unclean_ref, cwd)
    if tip is None:
        return []
    order = []
    for sha in git_ops.rev_list_reverse(tip, cwd):
        message = git_ops.commit_message(sha, cwd)
        key = trailers.read_trailer_value(message, RECON_TRAILER, cwd)
        if key is not None:
            order.append(key)
    return order


def _reconstructed_key_map(unclean_ref: str, cwd: Path) -> dict[str, str]:
    """key -> current commit sha on `unclean_ref` that was built for it."""
    tip = git_ops.rev_parse(unclean_ref, cwd)
    if tip is None:
        return {}
    mapping: dict[str, str] = {}
    for sha in git_ops.rev_list_reverse(tip, cwd):
        message = git_ops.commit_message(sha, cwd)
        key = trailers.read_trailer_value(message, RECON_TRAILER, cwd)
        if key is not None:
            mapping[key] = sha
    return mapping


def _full_matched_infos(ref: str, cwd: Path) -> dict[str, CommitInfo]:
    """key -> most-recent CommitInfo for that key, scanning `ref`'s entire
    local history (not just new-since-cursor). Used only for divergence
    detection, where we need the *current* state of a key's commit on each
    side even if it wasn't touched in this run."""
    tip = git_ops.rev_parse(ref, cwd)
    if tip is None:
        return {}
    mapping: dict[str, CommitInfo] = {}
    for sha in git_ops.rev_list_reverse(tip, cwd):
        info = read_commit_info(sha, cwd)
        key = _key_for_info(info, cwd)
        if isinstance(key, str):
            mapping[key] = info  # last one wins: the current/most-recent commit for this key
    return mapping


def detect_divergences(unclean_ref: str, buckets: dict, cwd: Path) -> list[dict]:
    """For matched keys already reconstructed onto `unclean_ref` (per
    `RECON_TRAILER`), recompute the combined clean+history delta and compare
    the resulting tree against the recorded unclean commit's actual tree.
    Read-only -- never modifies anything.
    """
    key_to_sha = _reconstructed_key_map(unclean_ref, cwd)
    divergences: list[dict] = []

    for key, bucket in buckets.items():
        if not isinstance(key, str) or key not in key_to_sha:
            continue
        clean_info, history_info = bucket["clean"], bucket["history"]
        if clean_info is None or history_info is None:
            continue

        old_sha = key_to_sha[key]
        parent_sha = git_ops.rev_parse(f"{old_sha}^", cwd)
        base_tree = git_ops.tree_for_commit(parent_sha, cwd) if parent_sha else git_ops.EMPTY_TREE_SHA

        expected_tree = tree_ops.apply_path_changes(base_tree, clean_info.paths, clean_info.sha, cwd)
        expected_tree = tree_ops.apply_path_changes(expected_tree, history_info.paths, history_info.sha, cwd)
        actual_tree = git_ops.tree_for_commit(old_sha, cwd)

        if expected_tree != actual_tree:
            divergences.append(
                {
                    "key": key,
                    "old_unclean_sha": old_sha,
                    "old_tree": actual_tree,
                    "expected_tree": expected_tree,
                    "clean_sha": clean_info.sha,
                    "history_sha": history_info.sha,
                }
            )

    return divergences


def _strip_trailers(body: str) -> str:
    lines = body.splitlines()
    while lines and _TRAILER_LINE_RE.match(lines[-1]):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _non_boilerplate_history_content(history_info: CommitInfo) -> str:
    """Heuristic per the plan: "more than one non-trivial line" of history's
    body (after stripping trailers and AI-subject-boilerplate-looking lines)
    counts as worth preserving."""
    stripped = _strip_trailers(history_info.body)
    non_trivial = [
        line for line in stripped.splitlines()
        if line.strip() and not classify.AI_SUBJECT_RE.match(line.strip())
    ]
    if len(non_trivial) > 1:
        return stripped.strip()
    return ""


def _message_with_body(subject: str, body: str) -> str:
    body = body.strip("\n")
    if body:
        return f"{subject}\n\n{body}\n"
    return f"{subject}\n"


def _merge_message(clean_info: CommitInfo, history_info: CommitInfo) -> str:
    if clean_info.subject != history_info.subject:
        message = _message_with_body(clean_info.subject, clean_info.body)
        extra = _non_boilerplate_history_content(history_info)
        if extra:
            message = message.rstrip("\n") + "\n\n---\n" + extra + "\n"
        return message
    return _message_with_body(clean_info.subject, clean_info.body)


def build_merged_commit(unclean_prev: str, item: dict, cwd: Path, *, dry_run: bool = False) -> str:
    """Build (but, per the plan, always *build* -- `dry_run` only controls
    whether callers move refs afterwards, since creating loose git objects is
    harmless) the next unclean commit for one bucket item.

    `item` is a bucket value dict (`{"clean": CommitInfo|None, "history":
    CommitInfo|None}`) plus a `"key"` entry (the bucket key) so the result
    can be stamped with `RECON_TRAILER` -- a minimal, documented extension of
    the plan's literal `bucket_commits` value shape, since the trailer needs
    the key and `build_merged_commit` has no other way to receive it.
    """
    clean_info = item.get("clean")
    history_info = item.get("history")
    key = item.get("key")

    prev_tree = git_ops.tree_for_commit(unclean_prev, cwd)

    if clean_info is not None and history_info is not None:
        tree = tree_ops.apply_path_changes(prev_tree, clean_info.paths, clean_info.sha, cwd)
        tree = tree_ops.apply_path_changes(tree, history_info.paths, history_info.sha, cwd)
        author_name, author_email, author_date = (
            clean_info.author_name,
            clean_info.author_email,
            clean_info.author_date,
        )
        message = _merge_message(clean_info, history_info)
        recon_key = key if isinstance(key, str) else clean_info.sha
    else:
        solo = clean_info if clean_info is not None else history_info
        assert solo is not None, "build_merged_commit called with an empty item"
        tree = tree_ops.apply_path_changes(prev_tree, solo.paths, solo.sha, cwd)
        author_name, author_email, author_date = solo.author_name, solo.author_email, solo.author_date
        message = _message_with_body(solo.subject, solo.body)
        recon_key = key if isinstance(key, str) else solo.sha

    message = trailers.write_trailers(message, {RECON_TRAILER: recon_key}, cwd)

    return git_ops.commit_tree(
        tree,
        [unclean_prev],
        message,
        cwd,
        author_name=author_name,
        author_email=author_email,
        author_date=author_date,
        committer_name=identity.BOT_NAME,
        committer_email=identity.BOT_EMAIL,
        committer_date=_committer_date_now(),
    )


def _rewrite_from(unclean_ref: str, old_sha: str, new_tree: str, cwd: Path, *, dry_run: bool = False) -> str:
    """Amend `old_sha` in place to `new_tree` and replay every commit after
    it on `unclean_ref`, preserving each descendant's own message/author but
    recomputing its tree (its own delta, reapplied onto the new chain).
    Returns the new tip; moves `unclean_ref` there unless `dry_run`.
    """
    tip = git_ops.rev_parse(unclean_ref, cwd)
    assert tip is not None
    all_shas = git_ops.rev_list_reverse(unclean_ref, cwd)
    idx = all_shas.index(old_sha)
    descendants = all_shas[idx + 1:]

    parent_sha = git_ops.rev_parse(f"{old_sha}^", cwd)
    parents = [parent_sha] if parent_sha else []
    message = git_ops.commit_message(old_sha, cwd)
    author_name, author_email, author_date = _author_info(old_sha, cwd)

    new_prev = git_ops.commit_tree(
        new_tree,
        parents,
        message,
        cwd,
        author_name=author_name,
        author_email=author_email,
        author_date=author_date,
        committer_name=identity.BOT_NAME,
        committer_email=identity.BOT_EMAIL,
        committer_date=_committer_date_now(),
    )

    for d_sha in descendants:
        d_changes = tree_ops.raw_diff_for_commit(d_sha, cwd)
        d_tree = tree_ops.apply_path_changes(git_ops.tree_for_commit(new_prev, cwd), d_changes, d_sha, cwd)
        d_message = git_ops.commit_message(d_sha, cwd)
        d_author_name, d_author_email, d_author_date = _author_info(d_sha, cwd)
        new_prev = git_ops.commit_tree(
            d_tree,
            [new_prev],
            d_message,
            cwd,
            author_name=d_author_name,
            author_email=d_author_email,
            author_date=d_author_date,
            committer_name=identity.BOT_NAME,
            committer_email=identity.BOT_EMAIL,
            committer_date=_committer_date_now(),
        )

    if not dry_run:
        git_ops.move_ref(unclean_ref, new_prev, tip, cwd)

    return new_prev


def _new_shas_since_cursor(tip: str | None, cursor: str | None, lower_bound_ref: str, cwd: Path) -> list[str]:
    if tip is None:
        return []
    if cursor is not None and git_ops.rev_exists(cursor, cwd) and git_ops.is_ancestor(cursor, tip, cwd):
        return git_ops.rev_list_reverse(f"{cursor}..{tip}", cwd)
    # Cursor missing or no longer an ancestor (e.g. amended/rewritten history)
    # -- fall back to everything past where this branch forked off
    # `lower_bound_ref` (mirrors sync_splits.commits_to_replay), rather than
    # walking all the way back through shared ancestry with e.g. `master`.
    base = git_ops.merge_base(tip, lower_bound_ref, cwd)
    if base is None:
        return git_ops.rev_list_reverse(tip, cwd)
    return git_ops.rev_list_reverse(f"{base}..{tip}", cwd)


def reconstruct_unclean(
    base_branch: str,
    *,
    repo_root: Path,
    main_branch: str,
    allow_diverge_rewrite: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    cwd = repo_root
    clean_ref = base_branch
    history_ref = branches.history_name(base_branch)
    history_main_ref = branches.history_name(main_branch)
    unclean_ref = branches.unclean_name(base_branch)

    result: dict = {
        "branch": base_branch,
        "commits_created": 0,
        "divergences_found": 0,
        "divergences_fixed": 0,
        "order_conflict": False,
        "divergences": [],
    }

    clean_tip = git_ops.rev_parse(clean_ref, cwd)
    history_tip = git_ops.rev_parse(history_ref, cwd)

    if clean_tip is None and history_tip is None:
        result["note"] = "neither clean nor history branch exists"
        return result

    clean_cursor_name = clean_cursor_ref(base_branch)
    history_cursor_name = history_cursor_ref(base_branch)
    clean_cursor = git_ops.rev_parse(clean_cursor_name, cwd)
    history_cursor = git_ops.rev_parse(history_cursor_name, cwd)

    new_clean_shas = _new_shas_since_cursor(clean_tip, clean_cursor, main_branch, cwd)
    new_history_shas = _new_shas_since_cursor(history_tip, history_cursor, history_main_ref, cwd)

    clean_infos = [read_commit_info(sha, cwd) for sha in new_clean_shas]
    history_infos = [read_commit_info(sha, cwd) for sha in new_history_shas]

    buckets = bucket_commits(clean_infos, history_infos, cwd)

    unclean_source_order = read_unclean_source_order(unclean_ref, cwd)
    key_to_sha = _reconstructed_key_map(unclean_ref, cwd)

    try:
        check_order_consistency(clean_infos, history_infos, unclean_source_order, force=force)
    except ValueError:
        if not force:
            raise
        result["order_conflict"] = True

    # --- divergence detection: matched keys already reconstructed, re-derived
    # from the *full* current clean/history branches (not just new-since-
    # cursor commits) since either side of an already-reconciled pair may
    # have been edited/amended in place without producing any "new" commit
    # for the other side in this run. ---
    full_clean_map = _full_matched_infos(clean_ref, cwd)
    full_history_map = _full_matched_infos(history_ref, cwd)
    divergence_candidates = {
        key: {"clean": full_clean_map.get(key), "history": full_history_map.get(key)}
        for key in key_to_sha
        if key in full_clean_map and key in full_history_map
    }
    divergences = detect_divergences(unclean_ref, divergence_candidates, cwd)
    result["divergences_found"] = len(divergences)
    result["divergences"] = divergences

    unclean_tip = git_ops.rev_parse(unclean_ref, cwd)
    if unclean_tip is None:
        base_tip = git_ops.rev_parse(main_branch, cwd)
        assert base_tip is not None, f"main branch {main_branch!r} not found"
        unclean_tip = base_tip
        if not dry_run:
            git_ops.create_branch(unclean_ref, unclean_tip, cwd)

    if divergences and allow_diverge_rewrite:
        for divergence in divergences:
            unclean_tip = _rewrite_from(
                unclean_ref,
                divergence["old_unclean_sha"],
                divergence["expected_tree"],
                cwd,
                dry_run=dry_run,
            )
            result["divergences_fixed"] += 1

    # --- build newly-bucketed items (not yet reconstructed) in sort order ---
    items_to_build = [
        (key, bucket) for key, bucket in buckets.items()
        if not (isinstance(key, str) and key in key_to_sha)
    ]
    ordered = _order_buckets(items_to_build, clean_infos, history_infos, unclean_source_order, cwd)

    for key, bucket in ordered:
        item = {"clean": bucket["clean"], "history": bucket["history"], "key": key}
        unclean_tip = build_merged_commit(unclean_tip, item, cwd, dry_run=dry_run)
        result["commits_created"] += 1

    if not dry_run:
        prior_tip = git_ops.rev_parse(unclean_ref, cwd)
        git_ops.move_ref(unclean_ref, unclean_tip, prior_tip, cwd)
        if clean_tip is not None:
            git_ops.move_ref(clean_cursor_name, clean_tip, None, cwd)
        if history_tip is not None:
            git_ops.move_ref(history_cursor_name, history_tip, None, cwd)

    result["unclean_tip"] = unclean_tip
    return result
