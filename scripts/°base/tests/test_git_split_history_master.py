"""Tests for (C) part 1: history_master.update_history_master."""

from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _git_test_helpers import git, init_repo, make_commit  # noqa: E402

LIB_ROOT = Path(__file__).resolve().parents[1] / "git"
sys.path.insert(0, str(LIB_ROOT))

git_ops = importlib.import_module("°split_lib.git_ops")
history_master = importlib.import_module("°split_lib.history_master")
trailers = importlib.import_module("°split_lib.trailers")


class HistoryMasterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = Path(self._tmp.name)
        init_repo(self.repo_root)
        make_commit(self.repo_root, "root.txt", "root commit")

    def _add_clean_branch_trailer(self, sha: str, branch: str) -> None:
        """Amend the commit at HEAD (must be `sha`) to carry the required
        X-Base-Split-Clean-Branch trailer, as if a merge/squash bot had
        added it when landing a split-participating branch into master."""
        message = git_ops.commit_message(sha, self.repo_root)
        new_message = trailers.write_trailers(
            message, {"X-Base-Split-Clean-Branch": branch}, self.repo_root
        )
        git(["commit", "--amend", "-m", new_message], self.repo_root)

    def test_first_run_creates_history_master_at_master_tip(self) -> None:
        result = history_master.update_history_master(
            repo_root=self.repo_root, main_branch="master"
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["first_run"])

        master_tip = git(["rev-parse", "master"], self.repo_root)
        history_tip = git(["rev-parse", "ai/history/master"], self.repo_root)
        self.assertEqual(master_tip, history_tip)

    def test_idempotent_rerun_is_a_no_op(self) -> None:
        history_master.update_history_master(repo_root=self.repo_root, main_branch="master")
        history_tip_after_first = git(["rev-parse", "ai/history/master"], self.repo_root)

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master")

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["first_run"])
        history_tip_after_second = git(["rev-parse", "ai/history/master"], self.repo_root)
        self.assertEqual(history_tip_after_first, history_tip_after_second)

    def test_subsequent_run_replays_and_preserves_a_prior_merge_marker(self) -> None:
        # First run: history-master starts as a literal copy of master.
        history_master.update_history_master(repo_root=self.repo_root, main_branch="master")

        # Seed a manually-added commit on top of history-master mimicking a
        # previous merge-marker (as if a branch had already been folded in).
        git(["checkout", "ai/history/master"], self.repo_root)
        marker_message = trailers.write_trailers(
            "[base] history: mark end of 'some-branch's replayed history\n",
            {"X-Base-Split-Merge-Marker-For": "deadbeef" * 5},
            self.repo_root,
        )
        git(["commit", "--allow-empty", "-m", marker_message], self.repo_root)
        seeded_marker_sha = git(["rev-parse", "HEAD"], self.repo_root)
        git(["checkout", "master"], self.repo_root)

        # Advance master with a genuinely new commit.
        make_commit(self.repo_root, "master2.txt", "master advances")

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master")
        self.assertEqual(result["status"], "ok")

        # The replayed marker's trailer must have survived the cherry-pick
        # verbatim, and the new master commit must now be reachable too.
        history_log = git(["log", "--format=%H", "ai/history/master"], self.repo_root).splitlines()
        marker_messages = [
            trailers.read_trailer_value(git_ops.commit_message(sha, self.repo_root), "X-Base-Split-Merge-Marker-For", self.repo_root)
            for sha in history_log
        ]
        self.assertIn("deadbeef" * 5, marker_messages)

        master_tip = git(["rev-parse", "master"], self.repo_root)
        git(["merge-base", "--is-ancestor", master_tip, "ai/history/master"], self.repo_root)
        # (The original seeded commit's sha doesn't necessarily survive --
        # it gets cherry-picked/re-created -- but its content, the trailer,
        # does, which is already asserted above.)

    def test_base_merge_recreation_after_master_advances(self) -> None:
        # Set up a fake "base" remote repo, cloned from this repo (rather
        # than a fresh init) so the two share history -- a genuinely
        # unrelated history would make `git merge` refuse outright, which
        # isn't the scenario being tested here (a real base/base fork does
        # share history with this repo).
        base_repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(base_repo_tmp.cleanup)
        base_repo_root = Path(base_repo_tmp.name)
        git(["clone", str(self.repo_root), str(base_repo_root)], self.repo_root)
        git(["config", "user.email", "test@example.com"], base_repo_root)
        git(["config", "user.name", "Test"], base_repo_root)
        make_commit(base_repo_root, "shared.txt", "base adds shared.txt", content="from base\n")
        base_sha = git(["rev-parse", "HEAD"], base_repo_root)

        git(["remote", "add", "base", str(base_repo_root)], self.repo_root)
        git(["fetch", "base"], self.repo_root)

        # First run: history-master starts as a literal copy of master
        # (neither has shared.txt yet).
        history_master.update_history_master(repo_root=self.repo_root, main_branch="master")

        # Manually construct the base-merge on ai/history/master. This
        # merges cleanly (history-master doesn't have shared.txt at all yet,
        # so it's a plain add, no conflict) -- recreate_base_merge()'s
        # auto-resolve doesn't require the *original* merge to have
        # conflicted, only that a resolved blob exists at old_merge_sha for
        # whatever path conflicts when it's later recreated onto a moved tip.
        git(["checkout", "ai/history/master"], self.repo_root)
        merge_proc = git_ops.merge_no_commit(base_sha, self.repo_root)
        self.assertEqual(merge_proc.returncode, 0, merge_proc.stderr)
        git(["commit", "--no-edit"], self.repo_root)
        merge_sha = git(["rev-parse", "HEAD"], self.repo_root)
        new_message = trailers.write_trailers(
            git_ops.commit_message(merge_sha, self.repo_root),
            {"X-Base-History-Merge-Kind": "base-merge", "X-Base-History-Merge-Sha": base_sha},
            self.repo_root,
        )
        git(["commit", "--amend", "-m", new_message], self.repo_root)
        merge_sha = git(["rev-parse", "HEAD"], self.repo_root)

        git(["checkout", "master"], self.repo_root)

        # Advance master with its OWN independent add of the same path, so
        # recreating the base-merge onto the new master tip is an add/add
        # conflict this time, even though the original wasn't.
        make_commit(self.repo_root, "shared.txt", "master adds shared.txt too", content="changed on master\n")

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master")
        self.assertEqual(result["status"], "ok")

        # The recreated merge must exist (a commit tagged base-merge,
        # replayed-from the original), and its content must be the ORIGINAL
        # merge's resolution for shared.txt (per the auto-resolve rule) --
        # base's content, since the original merge took it cleanly -- not
        # master's new content.
        history_log = git(["log", "--format=%H", "ai/history/master"], self.repo_root).splitlines()
        recreated = None
        for sha in history_log:
            message = git_ops.commit_message(sha, self.repo_root)
            replayed_from = trailers.read_trailer_value(message, "X-Base-History-Merge-Replayed-From", self.repo_root)
            if replayed_from == merge_sha:
                recreated = sha
                break
        self.assertIsNotNone(recreated, "expected a recreated base-merge commit")

        content = git_ops.show_path_at(recreated, "shared.txt", self.repo_root).decode()
        self.assertEqual(content, "from base\n")

    def test_first_base_fold_allows_unrelated_histories(self) -> None:
        # `base/base` is, by design, a separate template repo with no shared
        # ancestor with a freshly-adopting consuming repo (see README.md
        # "Setup: c) Merge base/base"). The very first fold onto a fresh
        # history-master line must succeed via --allow-unrelated-histories
        # instead of git refusing with "refusing to merge unrelated
        # histories" (observed live: see ai/°base/errors/18.md).
        base_repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(base_repo_tmp.cleanup)
        base_repo_root = Path(base_repo_tmp.name)
        init_repo(base_repo_root, branch="base")
        make_commit(base_repo_root, "template.txt", "base template file")
        base_sha = git(["rev-parse", "HEAD"], base_repo_root)

        git(["remote", "add", "base", str(base_repo_root)], self.repo_root)
        git(["fetch", "base"], self.repo_root)
        self.assertIsNone(git_ops.merge_base("master", base_sha, self.repo_root))

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["base_merge"], result["history_master"])
        content = git_ops.show_path_at(result["history_master"], "template.txt", self.repo_root).decode()
        self.assertEqual(content, "base template file")

    def test_master_is_never_mutated_by_a_base_merge(self) -> None:
        # Regression test for the invariant that adopting/updating base can
        # never touch `master` (and therefore never any clean branch) --
        # only `ai/history/master` ever receives base/base content.
        base_repo_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(base_repo_tmp.cleanup)
        base_repo_root = Path(base_repo_tmp.name)
        git(["clone", str(self.repo_root), str(base_repo_root)], self.repo_root)
        git(["config", "user.email", "test@example.com"], base_repo_root)
        git(["config", "user.name", "Test"], base_repo_root)
        make_commit(base_repo_root, "shared.txt", "base adds shared.txt", content="from base\n")
        base_sha = git(["rev-parse", "HEAD"], base_repo_root)

        git(["remote", "add", "base", str(base_repo_root)], self.repo_root)
        git(["fetch", "base"], self.repo_root)

        master_sha_before = git(["rev-parse", "master"], self.repo_root)

        # First run: creates ai/history/master from master's tip (pure
        # ancestry, no base/base content yet) -- must not touch master.
        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(git(["rev-parse", "master"], self.repo_root), master_sha_before)

        # Manually fold base/base into ai/history/master directly (mirrors
        # the base-merge-recreation test's setup) -- master must still be
        # untouched afterward, and a subsequent run must also leave it alone.
        git(["checkout", "ai/history/master"], self.repo_root)
        merge_proc = git_ops.merge_no_commit(base_sha, self.repo_root)
        self.assertEqual(merge_proc.returncode, 0, merge_proc.stderr)
        git(["commit", "--no-edit"], self.repo_root)
        merge_sha = git(["rev-parse", "HEAD"], self.repo_root)
        new_message = trailers.write_trailers(
            git_ops.commit_message(merge_sha, self.repo_root),
            {"X-Base-History-Merge-Kind": "base-merge", "X-Base-History-Merge-Sha": base_sha},
            self.repo_root,
        )
        git(["commit", "--amend", "-m", new_message], self.repo_root)
        git(["checkout", "master"], self.repo_root)

        self.assertEqual(git(["rev-parse", "master"], self.repo_root), master_sha_before)

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(git(["rev-parse", "master"], self.repo_root), master_sha_before)

    def test_force_merge_recovery_widens_search_window(self) -> None:
        # A branch merge commit lands on master, tagged with the required
        # trailer, but *before* the point update-history-master last ran --
        # simulating "the trailer was present but the normal incremental
        # detection window would have missed it".
        make_commit(self.repo_root, "feature.txt", "feature landed")
        merge_sha = git(["rev-parse", "HEAD"], self.repo_root)
        self._add_clean_branch_trailer(merge_sha, "feature")
        merge_sha = git(["rev-parse", "HEAD"], self.repo_root)

        # First (normal) run: creates history-master. Since this is the very
        # first run, the whole of master's history IS in the detection
        # window, so the branch gets picked up (and marked) here already --
        # to actually exercise "the window would have missed it", advance
        # master again afterwards and do a second normal run first.
        history_master.update_history_master(repo_root=self.repo_root, main_branch="master")
        make_commit(self.repo_root, "unrelated.txt", "unrelated master commit")
        history_master.update_history_master(repo_root=self.repo_root, main_branch="master")

        history_tip = git(["rev-parse", "ai/history/master"], self.repo_root)
        self.assertTrue(history_master.has_merge_marker(history_tip, merge_sha, self.repo_root))

        # Sanity: without force_merge, a THIRD normal run stays idempotent
        # (the branch was already picked up by the first run's full-history
        # scan, so there's nothing new for force_merge to "recover" here --
        # this test instead directly exercises the widened-search primitive).
        found_without_force = history_master.find_newly_merged_clean_branches(
            history_tip, git(["rev-parse", "master"], self.repo_root), self.repo_root
        )
        self.assertEqual(found_without_force, [])

        found_with_force = history_master.find_newly_merged_clean_branches(
            None, git(["rev-parse", "master"], self.repo_root), self.repo_root
        )
        self.assertIn((merge_sha, "feature"), found_with_force)

        # And update_history_master itself accepts force_merge without error
        # and remains a no-op once the branch is already marked.
        result = history_master.update_history_master(
            repo_root=self.repo_root, main_branch="master", force_merge=["feature"]
        )
        self.assertEqual(result["status"], "ok")

    def _make_conflicting_replay(self) -> None:
        """Diverge master and ai/history/master on the same line of the same
        file, so replaying master's new commit onto history-master's tip
        conflicts for real."""
        (self.repo_root / "conflict.txt").write_text("line1\nline2\nline3\n")
        git(["add", "conflict.txt"], self.repo_root)
        git(["commit", "-m", "add conflict.txt"], self.repo_root)
        git(["branch", "-f", "ai/history/master"], self.repo_root)

        (self.repo_root / "conflict.txt").write_text("line1\nCHANGED-ON-MASTER\nline3\n")
        git(["commit", "-am", "master changes line2"], self.repo_root)

        git(["checkout", "ai/history/master"], self.repo_root)
        (self.repo_root / "conflict.txt").write_text("line1\nCHANGED-ON-HISTORY\nline3\n")
        git(["commit", "-am", "history-master already changed line2 differently"], self.repo_root)
        git(["checkout", "master"], self.repo_root)

    def test_conflict_carries_the_human_readable_message(self) -> None:
        self._make_conflicting_replay()

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master", yes=True)

        self.assertEqual(result["status"], "conflict")
        message = result["pending"]["message"]
        self.assertIn("--continue", message)
        self.assertIn("--abort", message)

    def test_continue_detects_state_orphaned_by_a_manual_abort(self) -> None:
        self._make_conflicting_replay()
        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master", yes=True)
        self.assertEqual(result["status"], "conflict")

        # Simulate someone bypassing the tool and running the raw git command
        # by hand -- git's own state (CHERRY_PICK_HEAD) is now gone, but the
        # tool's persisted state file still claims a pending cherry-pick
        # (this is exactly what was found live in ai/°base/errors/18.md).
        git(["cherry-pick", "--abort"], self.repo_root)
        self.assertTrue((self.repo_root / ".git" / "BASE_SPLIT_HISTORY_MASTER_STATE").exists())

        with self.assertRaises(history_master.HistoryMasterError) as ctx:
            history_master.update_history_master(repo_root=self.repo_root, main_branch="master", continue_=True)
        self.assertIn("stale state", str(ctx.exception))
        self.assertIn("--abort", str(ctx.exception))

        # --abort still cleanly recovers from here (doesn't blow up trying to
        # abort a git operation that's already gone).
        abort_result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master", abort=True)
        self.assertEqual(abort_result["status"], "aborted")
        self.assertFalse((self.repo_root / ".git" / "BASE_SPLIT_HISTORY_MASTER_STATE").exists())


class CheckoutSyncTests(unittest.TestCase):
    """Regression tests for the discovered bug: plumbing ref moves
    (`_pull_master`, the final `history_ref` update) never touched the
    working tree/index, so a currently-checked-out ref went stale."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = Path(self._tmp.name)
        init_repo(self.repo_root)
        make_commit(self.repo_root, "root.txt", "root commit")

        origin_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(origin_tmp.cleanup)
        self.origin_root = Path(origin_tmp.name)
        git(["clone", str(self.repo_root), str(self.origin_root)], self.repo_root)
        git(["config", "user.email", "test@example.com"], self.origin_root)
        git(["config", "user.name", "Test"], self.origin_root)
        make_commit(self.origin_root, "root.txt", "master advances upstream", content="advanced\n")

        git(["remote", "add", "origin", str(self.origin_root)], self.repo_root)

    def _add_clean_branch_trailer(self, sha: str, branch: str) -> None:
        message = git_ops.commit_message(sha, self.repo_root)
        new_message = trailers.write_trailers(
            message, {"X-Base-Split-Clean-Branch": branch}, self.repo_root
        )
        git(["commit", "--amend", "-m", new_message], self.repo_root)

    def test_pull_master_keeps_current_checkout_in_sync(self) -> None:
        git(["checkout", "master"], self.repo_root)

        result = history_master.update_history_master(
            repo_root=self.repo_root, main_branch="master", pull_master=True
        )
        self.assertEqual(result["status"], "ok")

        self.assertEqual(git(["status", "--porcelain"], self.repo_root), "")
        origin_tip = git(["rev-parse", "master"], self.origin_root)
        self.assertEqual(git(["rev-parse", "master"], self.repo_root), origin_tip)
        self.assertEqual((self.repo_root / "root.txt").read_text(), "advanced\n")

    def test_pull_master_refuses_with_dirty_checkout(self) -> None:
        git(["checkout", "master"], self.repo_root)
        master_sha_before = git(["rev-parse", "master"], self.repo_root)
        (self.repo_root / "root.txt").write_text("dirty local edit\n")

        with self.assertRaises(history_master.HistoryMasterError):
            history_master.update_history_master(
                repo_root=self.repo_root, main_branch="master", pull_master=True
            )

        self.assertEqual(git(["rev-parse", "master"], self.repo_root), master_sha_before)
        self.assertEqual((self.repo_root / "root.txt").read_text(), "dirty local edit\n")

    def _make_conflicting_replay_with_third_branch_checked_out(self) -> None:
        """Diverge master/ai/history/master (real content conflict) and leave
        an unrelated third branch checked out -- regression setup for the
        discovered bug: every replay step runs through the scratch branch and
        always leaves HEAD detached when it's done, regardless of what (if
        anything) was checked out before the run started."""
        git(["checkout", "master"], self.repo_root)
        (self.repo_root / "conflict.txt").write_text("line1\nline2\nline3\n")
        git(["add", "conflict.txt"], self.repo_root)
        git(["commit", "-m", "add conflict.txt"], self.repo_root)
        git(["branch", "-f", "ai/history/master"], self.repo_root)

        (self.repo_root / "conflict.txt").write_text("line1\nCHANGED-ON-MASTER\nline3\n")
        git(["commit", "-am", "master changes line2"], self.repo_root)

        git(["checkout", "ai/history/master"], self.repo_root)
        (self.repo_root / "conflict.txt").write_text("line1\nCHANGED-ON-HISTORY\nline3\n")
        git(["commit", "-am", "history-master already changed line2 differently"], self.repo_root)

        git(["checkout", "-b", "some-other-branch", "master"], self.repo_root)

    def test_third_branch_checked_out_is_restored_after_a_successful_run(self) -> None:
        git(["branch", "ai/history/master"], self.repo_root)
        make_commit(self.repo_root, "master2.txt", "master advances")
        git(["checkout", "-b", "some-other-branch", "master"], self.repo_root)

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(git(["branch", "--show-current"], self.repo_root), "some-other-branch")

    def test_already_detached_stays_detached_after_a_successful_run(self) -> None:
        git(["branch", "ai/history/master"], self.repo_root)
        make_commit(self.repo_root, "master2.txt", "master advances")
        git(["checkout", "--detach", "master"], self.repo_root)
        self.assertEqual(git(["branch", "--show-current"], self.repo_root), "")

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(git(["branch", "--show-current"], self.repo_root), "")

    def test_third_branch_checked_out_survives_a_conflict_continue_round_trip(self) -> None:
        self._make_conflicting_replay_with_third_branch_checked_out()

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master", yes=True)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(git(["branch", "--show-current"], self.repo_root), "_base_split_scratch")

        git(["checkout", "--theirs", "--", "conflict.txt"], self.repo_root)
        git(["add", "conflict.txt"], self.repo_root)
        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master", continue_=True)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(git(["branch", "--show-current"], self.repo_root), "some-other-branch")

    def test_third_branch_checked_out_survives_an_abort(self) -> None:
        self._make_conflicting_replay_with_third_branch_checked_out()

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master", yes=True)
        self.assertEqual(result["status"], "conflict")

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master", abort=True)

        self.assertEqual(result["status"], "aborted")
        self.assertEqual(git(["branch", "--show-current"], self.repo_root), "some-other-branch")

    def test_history_ref_checkout_stays_in_sync_after_replay(self) -> None:
        history_master.update_history_master(repo_root=self.repo_root, main_branch="master")

        git(["checkout", "ai/history/master"], self.repo_root)
        make_commit(self.repo_root, "existing_history.txt", "pre-existing history content")
        git(["checkout", "master"], self.repo_root)

        make_commit(self.repo_root, "master2.txt", "master advances locally")

        git(["checkout", "ai/history/master"], self.repo_root)

        result = history_master.update_history_master(repo_root=self.repo_root, main_branch="master")
        self.assertEqual(result["status"], "ok")

        self.assertEqual(git(["status", "--porcelain"], self.repo_root), "")
        new_tip = git(["rev-parse", "ai/history/master"], self.repo_root)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo_root), new_tip)
        # The replay rebuilt commits (new shas) rather than fast-forwarding --
        # confirm the working tree actually reflects the rebuilt content.
        self.assertTrue((self.repo_root / "existing_history.txt").exists())
        self.assertTrue((self.repo_root / "master2.txt").exists())

    def test_full_yes_run_pulls_master_replays_and_folds_base_in_order(self) -> None:
        # Pre-existing history-master content that must be replayed forward.
        history_master.update_history_master(repo_root=self.repo_root, main_branch="master")
        git(["checkout", "ai/history/master"], self.repo_root)
        make_commit(self.repo_root, "existing_history.txt", "pre-existing history content")
        git(["checkout", "master"], self.repo_root)

        # A previously-merged branch with its own unreplayed history waiting.
        make_commit(self.repo_root, "feature.txt", "feature landed")
        merge_sha = git(["rev-parse", "HEAD"], self.repo_root)
        self._add_clean_branch_trailer(merge_sha, "feature")
        merge_sha = git(["rev-parse", "HEAD"], self.repo_root)
        git(["branch", "ai/history/feature"], self.repo_root)
        git(["checkout", "ai/history/feature"], self.repo_root)
        make_commit(self.repo_root, "feature_history.txt", "feature's own history content")
        git(["checkout", "master"], self.repo_root)

        # setUp's `origin` was cloned before the commits above, so local has
        # since diverged ahead of it -- replace it with a clone taken *now*,
        # then advance that fresh origin one further commit so local is a
        # clean ancestor of it (a genuine pull/fast-forward scenario).
        git(["remote", "remove", "origin"], self.repo_root)
        fresh_origin_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(fresh_origin_tmp.cleanup)
        self.origin_root = Path(fresh_origin_tmp.name)
        git(["clone", str(self.repo_root), str(self.origin_root)], self.repo_root)
        git(["config", "user.email", "test@example.com"], self.origin_root)
        git(["config", "user.name", "Test"], self.origin_root)
        make_commit(self.origin_root, "root.txt", "master advances upstream", content="advanced\n")
        git(["remote", "add", "origin", str(self.origin_root)], self.repo_root)

        # A real `base` remote with new content to fold in.
        base_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(base_tmp.cleanup)
        base_repo_root = Path(base_tmp.name)
        git(["clone", str(self.repo_root), str(base_repo_root)], self.repo_root)
        git(["config", "user.email", "test@example.com"], base_repo_root)
        git(["config", "user.name", "Test"], base_repo_root)
        make_commit(base_repo_root, "from_base.txt", "base content", content="from base\n")
        git(["branch", "-m", "base"], base_repo_root)  # refs/remotes/base/base must resolve after fetch
        git(["remote", "add", "base", str(base_repo_root)], self.repo_root)

        result = history_master.update_history_master(
            repo_root=self.repo_root, main_branch="master", pull_master=True, pull_base=True
        )
        self.assertEqual(result["status"], "ok")

        # Step 0/pull: master ended up at origin's tip.
        origin_tip = git(["rev-parse", "master"], self.origin_root)
        self.assertEqual(git(["rev-parse", "master"], self.repo_root), origin_tip)

        tip = git(["rev-parse", "ai/history/master"], self.repo_root)
        tree_files = git(["ls-tree", "-r", "--name-only", tip], self.repo_root)
        # Step 1: pre-existing history-master content survived the replay.
        self.assertIn("existing_history.txt", tree_files)
        # Step 2: the newly-available branch's history + marker are present.
        self.assertIn("feature_history.txt", tree_files)
        self.assertTrue(history_master.has_merge_marker(tip, merge_sha, self.repo_root))
        self.assertIn("from_base.txt", tree_files)
        # Step 3: the base-fold is literally the last commit -- proof it ran
        # strictly after steps 1 and 2, not before or interleaved.
        tip_message = git_ops.commit_message(tip, self.repo_root)
        self.assertEqual(
            trailers.read_trailer_value(tip_message, "X-Base-History-Merge-Kind", self.repo_root),
            "base-merge",
        )


if __name__ == "__main__":
    unittest.main()
