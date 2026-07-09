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

branches = importlib.import_module("°split_lib.branches")
git_ops = importlib.import_module("°split_lib.git_ops")
bootstrap = importlib.import_module("°split_lib.bootstrap")


class BootstrapTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmpdir.name)
        init_repo(self.repo, branch="master")
        make_commit(self.repo, "README.md", "initial commit")

        git(["checkout", "-b", "feature"], self.repo)
        make_commit(self.repo, "src/one.py", "add one")
        make_commit(self.repo, "src/two.py", "add two")
        git(["checkout", "master"], self.repo)

    def tearDown(self):
        self._tmpdir.cleanup()


class NoHistoryMasterTests(BootstrapTestBase):
    def test_errors_clearly_without_history_master(self):
        result = bootstrap.bootstrap_branch("feature", repo_root=self.repo, main_branch="master")
        self.assertFalse(result["ok"])
        self.assertIn("update-history-master", result["error"])
        self.assertIsNone(git_ops.rev_parse(branches.unclean_name("feature"), self.repo))
        self.assertIsNone(git_ops.rev_parse(branches.history_name("feature"), self.repo))


class NoCleanBranchTests(BootstrapTestBase):
    def test_errors_clearly_when_clean_branch_missing(self):
        git(["branch", branches.history_name("master")], self.repo)
        result = bootstrap.bootstrap_branch("does-not-exist", repo_root=self.repo, main_branch="master")
        self.assertFalse(result["ok"])
        self.assertIn("does not exist", result["error"])


class BootstrapFromCleanOnlyTests(BootstrapTestBase):
    def setUp(self):
        super().setUp()
        git(["branch", branches.history_name("master")], self.repo)

    def test_bootstrap_creates_history_and_unclean(self):
        history_master_tip = git_ops.rev_parse(branches.history_name("master"), self.repo)

        result = bootstrap.bootstrap_branch("feature", repo_root=self.repo, main_branch="master")

        self.assertTrue(result["ok"])
        self.assertTrue(result["history_created"])

        history_ref = branches.history_name("feature")
        self.assertIsNotNone(git_ops.rev_parse(history_ref, self.repo))
        self.assertEqual(git_ops.rev_parse(history_ref, self.repo), history_master_tip)

        fork_point = git_ops.rev_parse(branches.history_fork_point_ref("feature"), self.repo)
        self.assertEqual(fork_point, history_master_tip)

        unclean_ref = branches.unclean_name("feature")
        self.assertIsNotNone(git_ops.rev_parse(unclean_ref, self.repo))

        feature_tree = git(["rev-parse", "feature^{tree}"], self.repo)
        unclean_tree = git(["rev-parse", f"{unclean_ref}^{{tree}}"], self.repo)
        self.assertEqual(feature_tree, unclean_tree)

    def test_dry_run_makes_no_ref_changes(self):
        before = git(["show-ref"], self.repo)
        result = bootstrap.bootstrap_branch("feature", repo_root=self.repo, main_branch="master", dry_run=True)
        after = git(["show-ref"], self.repo)
        self.assertTrue(result["ok"])
        self.assertEqual(before, after)

    def test_idempotent_rerun(self):
        bootstrap.bootstrap_branch("feature", repo_root=self.repo, main_branch="master")
        unclean_ref = branches.unclean_name("feature")
        tip_after_first = git_ops.rev_parse(unclean_ref, self.repo)
        history_ref = branches.history_name("feature")
        history_tip_after_first = git_ops.rev_parse(history_ref, self.repo)

        result = bootstrap.bootstrap_branch("feature", repo_root=self.repo, main_branch="master")

        self.assertFalse(result["history_created"])
        self.assertEqual(git_ops.rev_parse(unclean_ref, self.repo), tip_after_first)
        self.assertEqual(git_ops.rev_parse(history_ref, self.repo), history_tip_after_first)


if __name__ == "__main__":
    unittest.main()
