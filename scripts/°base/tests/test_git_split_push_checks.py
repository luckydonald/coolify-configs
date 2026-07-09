from __future__ import annotations

import contextlib
import importlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB_ROOT = Path(__file__).resolve().parents[1] / "git"
sys.path.insert(0, str(LIB_ROOT))

branches = importlib.import_module("°split_lib.branches")
classify = importlib.import_module("°split_lib.classify")
push_checks = importlib.import_module("°split_lib.push_checks")
cli = importlib.import_module("°split_lib.cli")

ZERO_SHA = "0" * 40


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def make_commit(cwd: Path, filename: str, message: str) -> str:
    (cwd / filename).parent.mkdir(parents=True, exist_ok=True)
    (cwd / filename).write_text(message)
    git(["add", filename], cwd)
    git(["commit", "-m", message], cwd)
    return git(["rev-parse", "HEAD"], cwd)


class IsZeroShaTests(unittest.TestCase):
    def test_zero_sha_detected(self):
        self.assertTrue(push_checks.is_zero_sha(ZERO_SHA))

    def test_real_sha_not_zero(self):
        self.assertFalse(push_checks.is_zero_sha("a" * 40))


class PolicyMatrixTests(unittest.TestCase):
    def _branch(self, fmt: branches.BranchFormat) -> branches.BranchClassification:
        return branches.BranchClassification(
            ref="x", format=fmt, base_name="x", is_history_master=False
        )

    def _commit(self, *, ai_tainted: bool, code_containing: bool) -> classify.CommitClassification:
        return classify.CommitClassification(
            sha="a" * 40,
            subject="subject",
            paths=(),
            is_ai_only_commit=ai_tainted and not code_containing,
            is_ai_tainted_commit=ai_tainted,
            is_code_containing_commit=code_containing,
        )

    def test_unclean_allows_everything(self):
        branch = self._branch(branches.BranchFormat.UNCLEAN)
        commit = self._commit(ai_tainted=True, code_containing=True)
        self.assertEqual(push_checks.check_content_policy(branch, [commit]), [])

    def test_clean_blocks_ai_tainted(self):
        branch = self._branch(branches.BranchFormat.CLEAN)
        commit = self._commit(ai_tainted=True, code_containing=False)
        self.assertEqual(len(push_checks.check_content_policy(branch, [commit])), 1)

    def test_clean_allows_pure_code(self):
        branch = self._branch(branches.BranchFormat.CLEAN)
        commit = self._commit(ai_tainted=False, code_containing=True)
        self.assertEqual(push_checks.check_content_policy(branch, [commit]), [])

    def test_history_blocks_code_containing(self):
        branch = self._branch(branches.BranchFormat.HISTORY)
        commit = self._commit(ai_tainted=False, code_containing=True)
        self.assertEqual(len(push_checks.check_content_policy(branch, [commit])), 1)

    def test_history_allows_ai_only(self):
        branch = self._branch(branches.BranchFormat.HISTORY)
        commit = self._commit(ai_tainted=True, code_containing=False)
        self.assertEqual(push_checks.check_content_policy(branch, [commit]), [])


class NamePolicyTests(unittest.TestCase):
    def _branch(self, fmt: branches.BranchFormat) -> branches.BranchClassification:
        return branches.BranchClassification(
            ref="x", format=fmt, base_name="x", is_history_master=False
        )

    def test_unclean_blocked_on_origin(self):
        self.assertIsNotNone(
            push_checks.check_name_policy(self._branch(branches.BranchFormat.UNCLEAN), "origin")
        )

    def test_history_blocked_on_origin(self):
        self.assertIsNotNone(
            push_checks.check_name_policy(self._branch(branches.BranchFormat.HISTORY), "origin")
        )

    def test_clean_allowed_on_origin(self):
        self.assertIsNone(
            push_checks.check_name_policy(self._branch(branches.BranchFormat.CLEAN), "origin")
        )

    def test_unclean_allowed_on_non_origin_remote(self):
        self.assertIsNone(
            push_checks.check_name_policy(self._branch(branches.BranchFormat.UNCLEAN), "backup")
        )


class EndToEndCheckPushTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git(["init", "-b", "master"], self.repo)
        git(["config", "user.email", "test@example.com"], self.repo)
        git(["config", "user.name", "Test"], self.repo)
        make_commit(self.repo, "README.md", "init")

    def tearDown(self):
        self._tmp.cleanup()

    def _run_check(self, remote_name: str, local_ref: str, local_sha: str, remote_sha: str = ZERO_SHA) -> tuple[int, str]:
        stdin_text = f"{local_ref} {local_sha} {local_ref} {remote_sha}\n"
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code = cli._check_push(remote_name, "irrelevant-url", stdin_text, repo_root=self.repo)
        return code, buf.getvalue()

    def test_unclean_to_non_origin_with_mixed_commit_allowed(self):
        sha = make_commit(self.repo, "src/thing.py", "Add thing")
        (self.repo / "ai").mkdir(exist_ok=True)
        sha = make_commit(self.repo, "ai/query.md", "ai: log prompt")
        code, _ = self._run_check("backup", "refs/heads/ai/UNCLEAN/feature-x", sha)
        self.assertEqual(code, 0)

    def test_unclean_to_origin_blocked_by_name(self):
        sha = make_commit(self.repo, "src/thing.py", "Add thing")
        code, output = self._run_check("origin", "refs/heads/ai/UNCLEAN/feature-x", sha)
        self.assertEqual(code, 1)
        self.assertIn("must not be pushed", output)

    def test_clean_to_non_origin_with_pure_code_allowed(self):
        sha = make_commit(self.repo, "src/thing.py", "Add thing")
        code, _ = self._run_check("backup", "refs/heads/feature-x", sha)
        self.assertEqual(code, 0)

    def test_clean_to_non_origin_with_ai_tainted_blocked(self):
        sha = make_commit(self.repo, "ai/query.md", "ai: log prompt")
        code, output = self._run_check("backup", "refs/heads/feature-x", sha)
        self.assertEqual(code, 1)
        self.assertIn("clean-format", output)

    def test_history_to_non_origin_with_code_blocked(self):
        sha = make_commit(self.repo, "src/thing.py", "Add thing")
        code, output = self._run_check("backup", "refs/heads/ai/history/feature-x", sha)
        self.assertEqual(code, 1)
        self.assertIn("history-format", output)

    def test_history_to_origin_with_ai_only_blocked_by_name_and_content_would_pass_content(self):
        sha = make_commit(self.repo, "ai/query.md", "ai: log prompt")
        code, output = self._run_check("origin", "refs/heads/ai/history/feature-x", sha)
        self.assertEqual(code, 1)
        self.assertIn("must not be pushed", output)
        # content policy allows ai-only commits on history branches; only the name check fires.
        self.assertNotIn("contains code content", output)

    def test_deleted_branch_is_skipped(self):
        code, _ = self._run_check(
            "origin", "refs/heads/ai/UNCLEAN/feature-x", ZERO_SHA, remote_sha="a" * 40
        )
        self.assertEqual(code, 0)

    def test_new_branch_with_no_remote_configured_uses_fallback(self):
        sha = make_commit(self.repo, "src/thing.py", "Add thing")
        code, _ = self._run_check("backup", "refs/heads/feature-x", sha, remote_sha=ZERO_SHA)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
