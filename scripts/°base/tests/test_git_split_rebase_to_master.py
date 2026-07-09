"""Tests for (C) part 2: rebase_to_master.rebase_branches_to_master."""

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

rebase_to_master = importlib.import_module("°split_lib.rebase_to_master")


class RebaseBranchesToMasterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = Path(self._tmp.name)
        init_repo(self.repo_root)
        make_commit(self.repo_root, "root.txt", "root commit")

    def test_only_unclean_exists_skips_clean_and_history(self) -> None:
        git(["branch", "ai/UNCLEAN/feature"], self.repo_root)
        git(["checkout", "ai/UNCLEAN/feature"], self.repo_root)
        make_commit(self.repo_root, "unclean.txt", "unclean-only commit")
        git(["checkout", "master"], self.repo_root)

        status = rebase_to_master.rebase_branches_to_master(
            "feature", repo_root=self.repo_root, main_branch="master"
        )

        self.assertEqual(status["clean"], "skipped: branch does not exist")
        self.assertEqual(status["history"], "skipped: branch does not exist")
        self.assertIn("history missing", status["unclean"])

    def test_only_clean_exists_rebases_successfully(self) -> None:
        git(["checkout", "-b", "feature"], self.repo_root)
        make_commit(self.repo_root, "feature.txt", "feature commit")
        git(["checkout", "master"], self.repo_root)
        make_commit(self.repo_root, "master.txt", "master advances")

        status = rebase_to_master.rebase_branches_to_master(
            "feature", repo_root=self.repo_root, main_branch="master"
        )

        self.assertIn("rebased", status["clean"])
        self.assertEqual(status["history"], "skipped: branch does not exist")
        self.assertEqual(status["unclean"], "skipped: branch does not exist")

        master_tip = git(["rev-parse", "master"], self.repo_root)
        # `git merge-base --is-ancestor` exits non-zero (raising, since our
        # helper runs with check=True) if master_tip is NOT an ancestor of
        # feature -- i.e. this call succeeding is itself the assertion.
        git(["merge-base", "--is-ancestor", master_tip, "feature"], self.repo_root)

    def test_clean_and_unclean_exist_but_no_history_skips_unclean(self) -> None:
        git(["checkout", "-b", "feature"], self.repo_root)
        make_commit(self.repo_root, "feature.txt", "feature commit")
        git(["branch", "ai/UNCLEAN/feature"], self.repo_root)
        git(["checkout", "master"], self.repo_root)
        make_commit(self.repo_root, "master.txt", "master advances")

        status = rebase_to_master.rebase_branches_to_master(
            "feature", repo_root=self.repo_root, main_branch="master"
        )

        self.assertIn("rebased", status["clean"])
        self.assertEqual(status["history"], "skipped: branch does not exist")
        self.assertIn("history missing", status["unclean"])

    def test_all_three_exist_and_rebase_successfully(self) -> None:
        # Set up ai/history/master so the history variant has something to
        # rebase onto.
        git(["checkout", "-b", "ai/history/master"], self.repo_root)
        make_commit(self.repo_root, "history_master.txt", "history master seed")
        git(["checkout", "master"], self.repo_root)

        # feature (clean), ai/history/feature, ai/UNCLEAN/feature all diverge
        # from their respective bases.
        git(["checkout", "-b", "feature"], self.repo_root)
        make_commit(self.repo_root, "feature.txt", "feature commit")
        git(["checkout", "ai/history/master"], self.repo_root)
        git(["checkout", "-b", "ai/history/feature"], self.repo_root)
        make_commit(self.repo_root, "history_feature.txt", "history feature commit")
        git(["checkout", "-b", "ai/UNCLEAN/feature"], self.repo_root)
        make_commit(self.repo_root, "unclean_feature.txt", "unclean feature commit")

        # Advance both masters so there's something real to rebase onto.
        git(["checkout", "master"], self.repo_root)
        make_commit(self.repo_root, "master.txt", "master advances")
        git(["checkout", "ai/history/master"], self.repo_root)
        make_commit(self.repo_root, "history_master2.txt", "history master advances")
        git(["checkout", "master"], self.repo_root)

        status = rebase_to_master.rebase_branches_to_master(
            "feature", repo_root=self.repo_root, main_branch="master"
        )

        self.assertIn("rebased", status["clean"])
        self.assertIn("rebased", status["history"])
        self.assertIn("rebased", status["unclean"])

        master_tip = git(["rev-parse", "master"], self.repo_root)
        history_master_tip = git(["rev-parse", "ai/history/master"], self.repo_root)
        history_tip = git(["rev-parse", "ai/history/feature"], self.repo_root)

        # As above: a raise here (via our check=True helper) is the failure signal.
        git(["merge-base", "--is-ancestor", master_tip, "feature"], self.repo_root)
        git(["merge-base", "--is-ancestor", history_master_tip, "ai/history/feature"], self.repo_root)
        git(["merge-base", "--is-ancestor", history_tip, "ai/UNCLEAN/feature"], self.repo_root)

    def test_none_exist_raises(self) -> None:
        with self.assertRaises(ValueError):
            rebase_to_master.rebase_branches_to_master(
                "nonexistent", repo_root=self.repo_root, main_branch="master"
            )


if __name__ == "__main__":
    unittest.main()
