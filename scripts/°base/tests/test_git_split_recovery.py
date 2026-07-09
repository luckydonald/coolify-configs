from __future__ import annotations

import contextlib
import importlib
import io
import subprocess
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
recovery = importlib.import_module("°split_lib.recovery")
cli = importlib.import_module("°split_lib.cli")


class RecoveryTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmpdir.name)
        init_repo(self.repo, branch="master")
        make_commit(self.repo, "README.md", "initial commit")
        git(["branch", "ai/history/master"], self.repo)

    def tearDown(self):
        self._tmpdir.cleanup()

    def make_unclean(self, base_branch: str) -> None:
        git(["checkout", "-b", branches.unclean_name(base_branch)], self.repo)


class ResolveWatchedRefsTests(RecoveryTestBase):
    def test_single_branch_includes_all_derived_refs(self):
        refs = recovery.resolve_watched_refs("feature", "master", self.repo)
        self.assertEqual(
            refs,
            [
                "master",
                "ai/history/master",
                "feature",
                "ai/UNCLEAN/feature",
                "ai/history/feature",
                "refs/base-split/history-master-fork-point/feature",
                "refs/base-split/unclean-cursor/clean/feature",
                "refs/base-split/unclean-cursor/history/feature",
            ],
        )

    def test_none_branch_unions_all_discovered_unclean_branches(self):
        self.make_unclean("one")
        git(["checkout", "master"], self.repo)
        git(["checkout", "-b", branches.unclean_name("two")], self.repo)
        git(["checkout", "master"], self.repo)

        refs = recovery.resolve_watched_refs(None, "master", self.repo)
        self.assertIn("one", refs)
        self.assertIn("two", refs)
        self.assertIn("ai/UNCLEAN/one", refs)
        self.assertIn("ai/UNCLEAN/two", refs)

    def test_dedup_preserves_order(self):
        refs = recovery.resolve_watched_refs("feature", "master", self.repo)
        self.assertEqual(len(refs), len(set(refs)))


class SnapshotTests(RecoveryTestBase):
    def test_snapshot_resolves_existing_and_missing_refs(self):
        snap = recovery.snapshot(["master", "does-not-exist"], self.repo)
        self.assertEqual(snap["master"], git_ops.rev_parse("master", self.repo))
        self.assertIsNone(snap["does-not-exist"])


class FormatTests(unittest.TestCase):
    def test_recovery_entry_contains_headline_table_and_undo_commands(self):
        before = {"master": "a" * 40, "ai/history/feature": None}
        entry = recovery.format_recovery_entry("scripts/°base/git/split.py sync-splits feature", before, "2026-01-01 00:00:00")

        self.assertIn("#### Run _2026-01-01 00:00:00_ `scripts/°base/git/split.py sync-splits feature`", entry)
        self.assertIn("`master` | `" + "a" * 40 + "`", entry)
        self.assertIn("`ai/history/feature` | `(none)`", entry)
        self.assertIn("git rebase --abort || true", entry)
        self.assertIn("git cherry-pick --abort || true", entry)
        self.assertIn("git merge --abort || true", entry)
        self.assertIn(f"git update-ref 'refs/heads/master' '{'a' * 40}'", entry)
        self.assertIn("git update-ref -d 'refs/heads/ai/history/feature' || true", entry)

    def test_after_summary_shows_before_and_now(self):
        before = {"master": "a" * 40}
        after = {"master": "b" * 40}
        summary = recovery.format_after_summary(before, after)
        self.assertIn(f"`master` | `{'a' * 40}` | `{'b' * 40}`", summary)


class WriteRecoveryLogTests(RecoveryTestBase):
    def test_appends_across_multiple_calls(self):
        recovery.write_recovery_log(self.repo, "#### Run _t1_ `cmd1`")
        recovery.write_recovery_log(self.repo, "#### Run _t2_ `cmd2`")

        content = (self.repo / recovery.RECOVERY_FILENAME).read_text()
        self.assertIn("#### Run _t1_ `cmd1`", content)
        self.assertIn("#### Run _t2_ `cmd2`", content)
        self.assertLess(content.index("cmd1"), content.index("cmd2"))


class CliIntegrationTests(RecoveryTestBase):
    def test_sync_splits_writes_recovery_log_and_undo_restores_refs(self):
        self.make_unclean("feature")
        make_commit(self.repo, "src/x.py", "add x")
        git(["checkout", "master"], self.repo)

        clean_before = git_ops.rev_parse("feature", self.repo)
        history_before = git_ops.rev_parse("ai/history/feature", self.repo)
        self.assertIsNone(clean_before)
        self.assertIsNone(history_before)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--repo-root", str(self.repo), "sync-splits", "feature", "--direction=to-clean-history"])
        self.assertEqual(code, 0)

        output = buf.getvalue()
        # Console output stays terse -- the full ref table + undo commands are
        # only ever meant to be read when something needs recovering, so they
        # go to the log file, not stdout, on a clean run.
        self.assertIn("snapshotted", output)
        self.assertNotIn("#### Run", output)
        self.assertNotIn("git update-ref -d 'refs/heads/feature' || true", output)

        recovery_file = self.repo / recovery.RECOVERY_FILENAME
        self.assertTrue(recovery_file.exists())
        logged = recovery_file.read_text()
        self.assertIn("#### Run", logged)
        self.assertIn("git update-ref -d 'refs/heads/feature' || true", logged)

        # sync-splits must have actually created these for the undo commands
        # (extracted from the log, not re-derived) to be meaningful.
        self.assertIsNotNone(git_ops.rev_parse("feature", self.repo))
        self.assertIsNotNone(git_ops.rev_parse("ai/history/feature", self.repo))

        undo_commands = [
            line
            for line in logged.splitlines()
            if line.startswith("git update-ref") or line.startswith("git rebase")
            or line.startswith("git cherry-pick") or line.startswith("git merge")
        ]
        for command in undo_commands:
            subprocess.run(command, cwd=self.repo, shell=True, check=True, capture_output=True)

        self.assertEqual(git_ops.rev_parse("feature", self.repo), clean_before)
        self.assertEqual(git_ops.rev_parse("ai/history/feature", self.repo), history_before)

    def test_update_history_master_conflict_shows_recovery_options_on_console_only(self):
        # Diverge master and ai/history/master on the same line of the same
        # file so replaying master's new commit onto history-master's tip
        # conflicts for real.
        make_commit(self.repo, "conflict.txt", "add conflict.txt", content="line1\nline2\nline3\n")
        git(["branch", "-f", "ai/history/master"], self.repo)
        make_commit(self.repo, "conflict.txt", "master changes line2", content="line1\nCHANGED-ON-MASTER\nline3\n")

        git(["checkout", "ai/history/master"], self.repo)
        make_commit(
            self.repo,
            "conflict.txt",
            "history-master already changed line2 differently",
            content="line1\nCHANGED-ON-HISTORY\nline3\n",
        )
        git(["checkout", "master"], self.repo)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--repo-root", str(self.repo), "update-history-master", "--yes"])
        self.assertEqual(code, 1)

        output = buf.getvalue()
        self.assertIn("== CONFLICT ==", output)
        self.assertIn("--continue", output)
        self.assertIn("--abort", output)
        # The full ref table/rollback commands stay out of the console --
        # only the short pointer line and the conflict block belong there.
        self.assertNotIn("#### Run", output)

        logged = (self.repo / recovery.RECOVERY_FILENAME).read_text()
        self.assertIn("#### Run", logged)
        self.assertIn("git update-ref", logged)
        # Every underlying git invocation is logged at DEBUG (file-only).
        self.assertIn("$ git checkout --detach HEAD", logged)
        self.assertIn("cherry-pick", logged)
        self.assertIn("rc=1", logged)

    def test_dry_run_skips_recovery_logging(self):
        self.make_unclean("feature")
        make_commit(self.repo, "src/x.py", "add x")
        git(["checkout", "master"], self.repo)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["--repo-root", str(self.repo), "sync-splits", "feature", "--dry-run"])

        self.assertNotIn("#### Run", buf.getvalue())
        self.assertFalse((self.repo / recovery.RECOVERY_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
