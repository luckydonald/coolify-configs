from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _git_test_helpers import git, init_repo, make_commit  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "git" / "get-base.py"
SPLIT_PY = Path(__file__).resolve().parents[1] / "git" / "split.py"
SPLIT_LIB_ROOT = Path(__file__).resolve().parents[1] / "git" / "°split_lib"


def load_script_module():
    spec = importlib.util.spec_from_file_location("get_base", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed_real_split_tooling(base_repo: Path) -> None:
    """Copy the real `split.py` + full `°split_lib` package into a fake
    `base` remote's tree, so a checked-out worktree of it can actually run
    `update-history-master`/`bootstrap-branch` end to end, not just resolve
    `°split_lib.branches` for auto-mode's own decision-making."""
    git_dir = base_repo / "scripts" / "°base" / "git"
    dest = git_dir / "°split_lib"
    dest.mkdir(parents=True, exist_ok=True)
    for path in SPLIT_LIB_ROOT.glob("*.py"):
        (dest / path.name).write_text(path.read_text())
    (git_dir / "split.py").write_text(SPLIT_PY.read_text())


class GetBaseTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script_module()

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        init_repo(self.repo)
        make_commit(self.repo, "README.md", "initial commit")

        # A fake "base" remote, cloned from this repo so history is shared
        # (mirrors the pattern used in test_git_split_history_master.py).
        base_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(base_tmp.cleanup)
        self.base_repo = Path(base_tmp.name)
        git(["clone", str(self.repo), str(self.base_repo)], self.repo)
        git(["config", "user.email", "test@example.com"], self.base_repo)
        git(["config", "user.name", "Test"], self.base_repo)
        make_commit(self.base_repo, "scripts/°base/git/split.py", "pretend tool", content="# stand-in\n")
        git(["branch", "-m", "base"], self.base_repo)

    def _add_real_base_remote(self):
        git(["remote", "add", "base", str(self.base_repo)], self.repo)

    def _seed_real_split_lib(self):
        """Copy the real branches.py/__init__.py into the fake base remote's
        tree, so auto_argv's `importlib.import_module("°split_lib.branches")`
        (against the worktree, once created) actually resolves."""
        dest = self.base_repo / "scripts" / "°base" / "git" / "°split_lib"
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("__init__.py", "branches.py"):
            (dest / name).write_text((SPLIT_LIB_ROOT / name).read_text())
        git(["add", "."], self.base_repo)
        git(["commit", "-m", "seed real °split_lib.branches"], self.base_repo)

    def test_ensure_base_remote_adds_when_missing(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.module.ensure_base_remote(self.repo, "someuser")

        url = git(["remote", "get-url", "base"], self.repo)
        self.assertEqual(url, self.module.remote_url("someuser"))
        self.assertIn(f"get-base.py: adding base remote: {url}", stderr.getvalue())

    def test_ensure_base_remote_leaves_existing_url_untouched(self):
        self._add_real_base_remote()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.module.ensure_base_remote(self.repo, "someuser")

        url = git(["remote", "get-url", "base"], self.repo)
        self.assertEqual(url, str(self.base_repo))
        self.assertIn(f"get-base.py: base remote already exists: {self.base_repo}", stderr.getvalue())

    def test_ensure_worktree_creates_then_refreshes(self):
        self._add_real_base_remote()
        self.module.fetch_base(self.repo)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            path = self.module.ensure_worktree(self.repo)
        self.assertTrue(path.exists())
        self.assertIn(f"get-base.py: creating worktree: {path}", stderr.getvalue())
        first_tip = git(["rev-parse", "HEAD"], path)

        make_commit(self.base_repo, "more.txt", "base advances", content="more\n")
        self.module.fetch_base(self.repo)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            path_again = self.module.ensure_worktree(self.repo)

        self.assertEqual(path, path_again)
        self.assertIn(f"get-base.py: refreshing worktree: {path}", stderr.getvalue())
        second_tip = git(["rev-parse", "HEAD"], path_again)
        self.assertNotEqual(first_tip, second_tip)

    def test_main_delegates_with_repo_root_and_forwarded_argv(self):
        self._add_real_base_remote()

        captured = {}
        stderr = io.StringIO()

        def fake_execvp(executable, args):
            captured["executable"] = executable
            captured["args"] = args

        with contextlib.redirect_stderr(stderr), \
             mock.patch.object(self.module.os, "execvp", side_effect=fake_execvp), \
             mock.patch.object(self.module, "find_repo_root", return_value=self.repo):
            self.module.main(["bootstrap-branch", "feature"])

        self.assertEqual(captured["executable"], sys.executable)
        args = captured["args"]
        self.assertEqual(args[0], sys.executable)
        self.assertIn("split.py", args[1])
        self.assertIn("--repo-root", args)
        self.assertEqual(args[args.index("--repo-root") + 1], str(self.repo))
        self.assertEqual(args[-2:], ["bootstrap-branch", "feature"])

        self.assertIsNotNone(git(["remote", "get-url", "base"], self.repo))
        progress = stderr.getvalue()
        self.assertIn(f"get-base.py: repo root: {self.repo}", progress)
        self.assertIn(f"get-base.py: {self.module.REMOTE_NAME} remote already exists: {self.base_repo}", progress)
        self.assertIn("get-base.py: fetching base/base", progress)
        self.assertIn(f"get-base.py: creating worktree: {self.repo / '.git' / 'base-tools'}", progress)
        self.assertIn("get-base.py: delegating:", progress)
        self.assertIn("bootstrap-branch feature", progress)

    def test_never_touches_current_checkout(self):
        self._add_real_base_remote()
        (self.repo / "untracked.txt").write_text("scratch")
        status_before = git(["status", "--porcelain"], self.repo)
        head_before = git(["rev-parse", "HEAD"], self.repo)

        with mock.patch.object(self.module.os, "execvp"), \
             mock.patch.object(self.module, "find_repo_root", return_value=self.repo):
            self.module.main(["update-history-master", "--yes"])

        self.assertEqual(git(["status", "--porcelain"], self.repo), status_before)
        self.assertEqual(git(["rev-parse", "HEAD"], self.repo), head_before)


class AutoArgvTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script_module()

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        init_repo(self.repo, branch="master")
        make_commit(self.repo, "README.md", "initial commit")

        base_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(base_tmp.cleanup)
        self.base_repo = Path(base_tmp.name)
        git(["clone", str(self.repo), str(self.base_repo)], self.repo)
        git(["config", "user.email", "test@example.com"], self.base_repo)
        git(["config", "user.name", "Test"], self.base_repo)
        _seed_real_split_tooling(self.base_repo)
        git(["add", "."], self.base_repo)
        git(["commit", "-m", "seed real split tooling"], self.base_repo)
        git(["branch", "-m", "base"], self.base_repo)

        git(["remote", "add", "base", str(self.base_repo)], self.repo)
        self.module.fetch_base(self.repo)
        self.worktree = self.module.ensure_worktree(self.repo)

    def test_on_main_branch_runs_update_history_master(self):
        git(["checkout", "master"], self.repo)
        self.assertEqual(
            self.module.auto_argv(self.repo, self.worktree),
            ["update-history-master", "--yes"],
        )

    def test_on_history_master_runs_update_history_master(self):
        git(["checkout", "-b", "ai/history/master"], self.repo)
        self.assertEqual(
            self.module.auto_argv(self.repo, self.worktree),
            ["update-history-master", "--yes"],
        )

    def test_on_clean_feature_branch_runs_bootstrap_branch(self):
        git(["checkout", "-b", "feature"], self.repo)

        self.assertEqual(
            self.module.auto_argv(self.repo, self.worktree),
            ["bootstrap-branch", "feature"],
        )

        # `ai/history/master` never existed -- auto mode must have run
        # update-history-master as a prerequisite before deciding to
        # bootstrap, rather than handing back a doomed command.
        self.assertIsNotNone(git(["rev-parse", "ai/history/master"], self.repo))

    def test_on_clean_feature_branch_skips_update_history_master_if_present(self):
        git(["checkout", "-b", "feature"], self.repo)
        self.module.run_split(self.repo, self.worktree, ["update-history-master", "--yes"])
        self.assertIsNotNone(git(["rev-parse", "ai/history/master"], self.repo))

        with mock.patch.object(self.module, "run_split") as run_split:
            self.assertEqual(
                self.module.auto_argv(self.repo, self.worktree),
                ["bootstrap-branch", "feature"],
            )
        run_split.assert_not_called()

    def test_on_clean_feature_branch_aborts_if_update_history_master_fails(self):
        git(["checkout", "-b", "feature"], self.repo)

        with mock.patch.object(self.module, "run_split", return_value=1) as run_split:
            self.assertIsNone(self.module.auto_argv(self.repo, self.worktree))
        run_split.assert_called_once_with(
            self.repo, self.worktree, ["update-history-master", "--yes"],
        )

    def test_on_unclean_branch_runs_forward_sync(self):
        git(["checkout", "-b", "ai/UNCLEAN/feature"], self.repo)
        self.assertEqual(
            self.module.auto_argv(self.repo, self.worktree),
            ["sync-splits", "feature", "--direction=to-clean-history"],
        )

    def test_on_non_master_history_branch_runs_forward_sync(self):
        git(["checkout", "-b", "ai/history/feature"], self.repo)
        self.assertEqual(
            self.module.auto_argv(self.repo, self.worktree),
            ["sync-splits", "feature", "--direction=to-clean-history"],
        )

    def test_detached_head_refuses(self):
        head_sha = git(["rev-parse", "HEAD"], self.repo)
        git(["checkout", "--detach", head_sha], self.repo)
        self.assertIsNone(self.module.auto_argv(self.repo, self.worktree))


class MainAutoModeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script_module()

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        init_repo(self.repo, branch="master")
        make_commit(self.repo, "README.md", "initial commit")

        base_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(base_tmp.cleanup)
        self.base_repo = Path(base_tmp.name)
        git(["clone", str(self.repo), str(self.base_repo)], self.repo)
        git(["config", "user.email", "test@example.com"], self.base_repo)
        git(["config", "user.name", "Test"], self.base_repo)
        _seed_real_split_tooling(self.base_repo)
        git(["add", "."], self.base_repo)
        git(["commit", "-m", "seed"], self.base_repo)
        git(["branch", "-m", "base"], self.base_repo)

        git(["remote", "add", "base", str(self.base_repo)], self.repo)

    def test_empty_argv_triggers_auto_detection(self):
        git(["checkout", "-b", "feature"], self.repo)

        captured = {}
        stderr = io.StringIO()

        def fake_execvp(executable, args):
            captured["args"] = args

        with contextlib.redirect_stderr(stderr), \
             mock.patch.object(self.module.os, "execvp", side_effect=fake_execvp), \
             mock.patch.object(self.module, "find_repo_root", return_value=self.repo):
            code = self.module.main([])

        self.assertEqual(code, 0)
        self.assertEqual(captured["args"][-2:], ["bootstrap-branch", "feature"])
        progress = stderr.getvalue()
        self.assertIn("get-base.py: auto mode: current branch feature", progress)
        self.assertIn(
            "get-base.py: auto mode: 'ai/history/master' missing -- running update-history-master first",
            progress,
        )
        self.assertIn("get-base.py: auto mode: selected bootstrap-branch feature", progress)
        self.assertIsNotNone(git(["rev-parse", "ai/history/master"], self.repo))

    def test_nonempty_argv_bypasses_auto_detection(self):
        git(["checkout", "-b", "feature"], self.repo)

        captured = {}

        def fake_execvp(executable, args):
            captured["args"] = args

        with mock.patch.object(self.module.os, "execvp", side_effect=fake_execvp), \
             mock.patch.object(self.module, "find_repo_root", return_value=self.repo):
            self.module.main(["update-history-master", "--yes"])

        self.assertEqual(captured["args"][-2:], ["update-history-master", "--yes"])

    def test_detached_head_with_empty_argv_refuses(self):
        head_sha = git(["rev-parse", "HEAD"], self.repo)
        git(["checkout", "--detach", head_sha], self.repo)

        with mock.patch.object(self.module.os, "execvp") as execvp, \
             mock.patch.object(self.module, "find_repo_root", return_value=self.repo):
            code = self.module.main([])

        self.assertEqual(code, 1)
        execvp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
