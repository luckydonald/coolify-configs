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
trailers = importlib.import_module("°split_lib.trailers")
sync_unclean = importlib.import_module("°split_lib.sync_unclean")


def commit_with_trailer(
    cwd: Path,
    filename: str,
    subject: str,
    trailer_values: dict[str, str],
    content: str | None = None,
) -> str:
    """Hand-craft a commit carrying the given trailers, without going through
    the (concurrently-developed) forward-direction module."""
    path = cwd / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else subject)
    git(["add", filename], cwd)

    message = subject + "\n"
    if trailer_values:
        message = trailers.write_trailers(message, trailer_values, cwd)

    msg_file = cwd / ".commitmsg-tmp"
    msg_file.write_text(message)
    try:
        git(["commit", "-F", str(msg_file)], cwd)
    finally:
        msg_file.unlink(missing_ok=True)

    return git(["rev-parse", "HEAD"], cwd)


class SyncUncleanTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmpdir.name)
        init_repo(self.repo, branch="master")
        self.root_sha = make_commit(self.repo, "README.md", "initial commit")
        # A stand-in "original unclean sha" -- just needs to be a real,
        # locally-resolvable commit; its own content is irrelevant to the
        # tests, only its sha is used as a matched-pair key.
        self.unclean_source_sha = self.root_sha

        # Seed ai/history/master so ai/history/feature has a base to fork from
        # (mirrors test_git_split_sync_splits.py's setUp).
        git(["branch", branches.history_name("master")], self.repo)
        git(["branch", "feature"], self.repo)
        git(["branch", branches.history_name("feature")], self.repo)

    def tearDown(self):
        self._tmpdir.cleanup()

    def checkout(self, ref: str) -> None:
        git(["checkout", ref], self.repo)

    def reconstruct(self, **kwargs):
        return sync_unclean.reconstruct_unclean(
            "feature", repo_root=self.repo, main_branch="master", **kwargs
        )

    def unclean_tree_paths(self) -> list[str]:
        tip = git_ops.rev_parse(branches.unclean_name("feature"), self.repo)
        return git(["ls-tree", "-r", "--name-only", tip], self.repo).splitlines()


class MergedPairTests(SyncUncleanTestBase):
    def test_code_and_history_pair_merge(self):
        self.checkout("feature")
        clean_sha = commit_with_trailer(
            self.repo,
            "src/app.py",
            "add app code",
            {
                sync_unclean.SOURCE_TRAILER: self.unclean_source_sha,
                sync_unclean.KIND_TRAILER: "mixed",
            },
        )
        self.checkout(branches.history_name("feature"))
        history_sha = commit_with_trailer(
            self.repo,
            "ai/notes.md",
            "ai: jot down notes",
            {
                sync_unclean.SOURCE_TRAILER: self.unclean_source_sha,
                sync_unclean.KIND_TRAILER: "mixed",
            },
        )

        result = self.reconstruct()

        self.assertEqual(result["commits_created"], 1)
        self.assertEqual(result["divergences_found"], 0)

        paths = self.unclean_tree_paths()
        self.assertIn("src/app.py", paths)
        self.assertIn("ai/notes.md", paths)
        self.assertIn("README.md", paths)

        unclean_tip = git_ops.rev_parse(branches.unclean_name("feature"), self.repo)
        message = git_ops.commit_message(unclean_tip, self.repo)
        self.assertEqual(
            trailers.read_trailer_value(message, sync_unclean.RECON_TRAILER, self.repo),
            self.unclean_source_sha,
        )
        # sanity: both source shas are still findable via git.
        self.assertTrue(git_ops.rev_exists(clean_sha, self.repo))
        self.assertTrue(git_ops.rev_exists(history_sha, self.repo))

    def test_merge_prefers_clean_subject_and_keeps_history_body(self):
        self.checkout("feature")
        commit_with_trailer(
            self.repo,
            "src/app.py",
            "reviewed: tighten error handling",
            {sync_unclean.SOURCE_TRAILER: self.unclean_source_sha, sync_unclean.KIND_TRAILER: "mixed"},
        )
        self.checkout(branches.history_name("feature"))
        commit_with_trailer(
            self.repo,
            "ai/notes.md",
            "ai: tighten error handling attempt 3",
            {sync_unclean.SOURCE_TRAILER: self.unclean_source_sha, sync_unclean.KIND_TRAILER: "mixed"},
            content="line one of real substance\nline two of real substance\n",
        )

        self.reconstruct()

        unclean_tip = git_ops.rev_parse(branches.unclean_name("feature"), self.repo)
        message = git_ops.commit_message(unclean_tip, self.repo)
        self.assertTrue(message.startswith("reviewed: tighten error handling"))


class SoloCherryPickTests(SyncUncleanTestBase):
    def test_code_only_cherry_pick_no_source_trailer(self):
        self.checkout("feature")
        commit_with_trailer(self.repo, "src/only_code.py", "add only-code file", {})

        result = self.reconstruct()

        self.assertEqual(result["commits_created"], 1)
        self.assertIn("src/only_code.py", self.unclean_tree_paths())

    def test_history_only_cherry_pick_no_source_trailer(self):
        self.checkout(branches.history_name("feature"))
        commit_with_trailer(self.repo, "ai/only_history.md", "ai: only history note", {})

        result = self.reconstruct()

        self.assertEqual(result["commits_created"], 1)
        self.assertIn("ai/only_history.md", self.unclean_tree_paths())

    def test_dangling_trailer_falls_back_to_unmatched(self):
        self.checkout("feature")
        fake_sha = "1234567890abcdef1234567890abcdef12345678"
        commit_with_trailer(
            self.repo,
            "src/dangling.py",
            "add dangling-source file",
            {sync_unclean.SOURCE_TRAILER: fake_sha},
        )

        result = self.reconstruct()

        self.assertEqual(result["commits_created"], 1)
        self.assertIn("src/dangling.py", self.unclean_tree_paths())


class DivergenceTests(SyncUncleanTestBase):
    def _seed_merged_pair(self):
        self.checkout("feature")
        commit_with_trailer(
            self.repo,
            "src/app.py",
            "add app code",
            {sync_unclean.SOURCE_TRAILER: self.unclean_source_sha, sync_unclean.KIND_TRAILER: "mixed"},
            content="version 1\n",
        )
        self.checkout(branches.history_name("feature"))
        commit_with_trailer(
            self.repo,
            "ai/notes.md",
            "ai: jot down notes",
            {sync_unclean.SOURCE_TRAILER: self.unclean_source_sha, sync_unclean.KIND_TRAILER: "mixed"},
        )
        first_result = self.reconstruct()
        self.assertEqual(first_result["commits_created"], 1)
        self.assertEqual(first_result["divergences_found"], 0)

    def _edit_clean_commit_content(self):
        self.checkout("feature")
        commit_with_trailer(
            self.repo,
            "src/app.py",
            "reviewed: fix a subtle bug",
            {sync_unclean.SOURCE_TRAILER: self.unclean_source_sha, sync_unclean.KIND_TRAILER: "mixed"},
            content="version 2, fixed during review\n",
        )

    def test_divergence_detected_but_not_rewritten_by_default(self):
        self._seed_merged_pair()
        before_tip = git_ops.rev_parse(branches.unclean_name("feature"), self.repo)

        self._edit_clean_commit_content()
        result = self.reconstruct()

        self.assertEqual(result["divergences_found"], 1)
        self.assertEqual(result["divergences_fixed"], 0)

        after_tip = git_ops.rev_parse(branches.unclean_name("feature"), self.repo)
        self.assertEqual(before_tip, after_tip)

        content = git(["show", f"{after_tip}:src/app.py"], self.repo)
        self.assertEqual(content, "version 1")

    def test_divergence_rewritten_with_allow_diverge_rewrite(self):
        self._seed_merged_pair()
        before_tip = git_ops.rev_parse(branches.unclean_name("feature"), self.repo)

        self._edit_clean_commit_content()
        result = self.reconstruct(allow_diverge_rewrite=True)

        self.assertEqual(result["divergences_found"], 1)
        self.assertEqual(result["divergences_fixed"], 1)

        after_tip = git_ops.rev_parse(branches.unclean_name("feature"), self.repo)
        self.assertNotEqual(before_tip, after_tip)

        content = git(["show", f"{after_tip}:src/app.py"], self.repo)
        self.assertEqual(content, "version 2, fixed during review")
        # history-side content should be untouched by the rewrite.
        self.assertEqual(git(["show", f"{after_tip}:ai/notes.md"], self.repo), "ai: jot down notes")


class IdempotencyTests(SyncUncleanTestBase):
    def test_idempotent_rerun_makes_no_changes(self):
        self.checkout("feature")
        commit_with_trailer(
            self.repo,
            "src/app.py",
            "add app code",
            {sync_unclean.SOURCE_TRAILER: self.unclean_source_sha, sync_unclean.KIND_TRAILER: "mixed"},
        )
        self.checkout(branches.history_name("feature"))
        commit_with_trailer(
            self.repo,
            "ai/notes.md",
            "ai: jot down notes",
            {sync_unclean.SOURCE_TRAILER: self.unclean_source_sha, sync_unclean.KIND_TRAILER: "mixed"},
        )

        first = self.reconstruct()
        self.assertEqual(first["commits_created"], 1)

        tip_after_first = git_ops.rev_parse(branches.unclean_name("feature"), self.repo)
        clean_cursor_after_first = git_ops.rev_parse(sync_unclean.clean_cursor_ref("feature"), self.repo)
        history_cursor_after_first = git_ops.rev_parse(sync_unclean.history_cursor_ref("feature"), self.repo)

        second = self.reconstruct()

        self.assertEqual(second["commits_created"], 0)
        self.assertEqual(second["divergences_found"], 0)

        tip_after_second = git_ops.rev_parse(branches.unclean_name("feature"), self.repo)
        self.assertEqual(tip_after_first, tip_after_second)
        self.assertEqual(
            clean_cursor_after_first,
            git_ops.rev_parse(sync_unclean.clean_cursor_ref("feature"), self.repo),
        )
        self.assertEqual(
            history_cursor_after_first,
            git_ops.rev_parse(sync_unclean.history_cursor_ref("feature"), self.repo),
        )


class BucketingTests(SyncUncleanTestBase):
    def test_duplicate_trailer_collision_raises(self):
        self.checkout("feature")
        commit_with_trailer(
            self.repo,
            "src/app.py",
            "add app code",
            {sync_unclean.SOURCE_TRAILER: self.unclean_source_sha},
        )
        commit_with_trailer(
            self.repo,
            "src/app2.py",
            "add more app code",
            {sync_unclean.SOURCE_TRAILER: self.unclean_source_sha},
        )

        with self.assertRaises(ValueError):
            self.reconstruct()


if __name__ == "__main__":
    unittest.main()
