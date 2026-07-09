from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

LIB_ROOT = Path(__file__).resolve().parents[1] / "git"
sys.path.insert(0, str(LIB_ROOT))

git_ops = importlib.import_module("°split_lib.git_ops")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _git_test_helpers import git, init_repo, make_commit  # noqa: E402


class RefQualificationTests(unittest.TestCase):
    """git update-ref does not resolve short names the way most git commands
    do -- a bare "feature-x" would otherwise create a loose ref straight
    under .git/ instead of a real branch under refs/heads/.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        init_repo(self.repo)
        self.sha = make_commit(self.repo, "f.txt", "init")

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_branch_with_bare_name_creates_real_branch(self):
        git_ops.create_branch("feature-x", self.sha, self.repo)
        refs = git(["show-ref"], self.repo)
        self.assertIn("refs/heads/feature-x", refs)
        self.assertTrue((self.repo / ".git" / "refs" / "heads" / "feature-x").exists())
        self.assertFalse((self.repo / ".git" / "feature-x").exists())

    def test_create_branch_with_nested_bare_name(self):
        git_ops.create_branch("ai/history/feature-x", self.sha, self.repo)
        refs = git(["show-ref"], self.repo)
        self.assertIn("refs/heads/ai/history/feature-x", refs)

    def test_move_ref_with_bare_name_updates_real_branch(self):
        git_ops.create_branch("feature-x", self.sha, self.repo)
        second_sha = make_commit(self.repo, "g.txt", "second")
        git_ops.move_ref("feature-x", second_sha, self.sha, self.repo)
        self.assertEqual(git_ops.rev_parse("refs/heads/feature-x", self.repo), second_sha)

    def test_fully_qualified_ref_is_passed_through_unchanged(self):
        git_ops.create_branch("refs/base-split/cursor/feature-x", self.sha, self.repo)
        refs = git(["show-ref"], self.repo)
        self.assertIn("refs/base-split/cursor/feature-x", refs)
        self.assertNotIn("refs/heads/refs/base-split", refs)


if __name__ == "__main__":
    unittest.main()
