from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB_ROOT = Path(__file__).resolve().parents[1] / "git"
sys.path.insert(0, str(LIB_ROOT))

branches = importlib.import_module("°split_lib.branches")


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


class ClassifyBranchTests(unittest.TestCase):
    def test_clean_branch(self):
        result = branches.classify_branch("feature/ABC-123_something")
        self.assertEqual(result.format, branches.BranchFormat.CLEAN)
        self.assertEqual(result.base_name, "feature/ABC-123_something")
        self.assertFalse(result.is_history_master)

    def test_unclean_branch(self):
        result = branches.classify_branch("ai/UNCLEAN/feature/ABC-123_something")
        self.assertEqual(result.format, branches.BranchFormat.UNCLEAN)
        self.assertEqual(result.base_name, "feature/ABC-123_something")

    def test_history_branch(self):
        result = branches.classify_branch("ai/history/bugfix/foo-crash")
        self.assertEqual(result.format, branches.BranchFormat.HISTORY)
        self.assertEqual(result.base_name, "bugfix/foo-crash")
        self.assertFalse(result.is_history_master)

    def test_history_master_flagged_when_base_name_matches_main_branch(self):
        result = branches.classify_branch("ai/history/master", main_branch="master")
        self.assertEqual(result.format, branches.BranchFormat.HISTORY)
        self.assertTrue(result.is_history_master)

    def test_history_master_not_flagged_for_different_main_branch(self):
        result = branches.classify_branch("ai/history/master", main_branch="main")
        self.assertEqual(result.format, branches.BranchFormat.HISTORY)
        self.assertFalse(result.is_history_master)

    def test_refs_heads_prefix_is_stripped(self):
        result = branches.classify_branch("refs/heads/ai/UNCLEAN/i-did-a-thing")
        self.assertEqual(result.format, branches.BranchFormat.UNCLEAN)
        self.assertEqual(result.ref, "ai/UNCLEAN/i-did-a-thing")

    def test_malformed_unclean_prefix_falls_through_to_clean(self):
        # No suffix after "ai/UNCLEAN/" doesn't match (requires 1+ chars).
        result = branches.classify_branch("ai/UNCLEAN/")
        self.assertEqual(result.format, branches.BranchFormat.CLEAN)


class NameHelperTests(unittest.TestCase):
    def test_unclean_name(self):
        self.assertEqual(branches.unclean_name("feature/x"), "ai/UNCLEAN/feature/x")

    def test_history_name(self):
        self.assertEqual(branches.history_name("feature/x"), "ai/history/feature/x")

    def test_round_trip_unclean(self):
        base = "feature/ABC-123/something/mr1"
        self.assertEqual(branches.base_name_from_unclean(branches.unclean_name(base)), base)

    def test_round_trip_history(self):
        base = "i-did-a-thing"
        self.assertEqual(branches.base_name_from_history(branches.history_name(base)), base)

    def test_base_name_from_unclean_returns_none_for_non_matching_ref(self):
        self.assertIsNone(branches.base_name_from_unclean("feature/x"))

    def test_base_name_from_history_returns_none_for_non_matching_ref(self):
        self.assertIsNone(branches.base_name_from_history("ai/UNCLEAN/feature/x"))


class DetectMainBranchTests(unittest.TestCase):
    def test_falls_back_to_existing_main_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(["init", "-b", "main"], repo)
            git(["config", "user.email", "test@example.com"], repo)
            git(["config", "user.name", "Test"], repo)
            (repo / "f.txt").write_text("hi")
            git(["add", "f.txt"], repo)
            git(["commit", "-m", "init"], repo)

            self.assertEqual(branches.detect_main_branch(repo), "main")

    def test_falls_back_to_master_when_nothing_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(["init", "-b", "some-other-branch"], repo)
            git(["config", "user.email", "test@example.com"], repo)
            git(["config", "user.name", "Test"], repo)
            (repo / "f.txt").write_text("hi")
            git(["add", "f.txt"], repo)
            git(["commit", "-m", "init"], repo)

            self.assertEqual(branches.detect_main_branch(repo), "master")


if __name__ == "__main__":
    unittest.main()
