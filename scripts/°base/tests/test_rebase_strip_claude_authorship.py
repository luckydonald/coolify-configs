from __future__ import annotations

import importlib.util
import shutil
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "°base" / "git" / "rebase_strip_claude_authorship.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("rebase_strip_claude_authorship", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RebaseStripClaudeAuthorshipTests(unittest.TestCase):
    def test_rebase_exec_uses_temporary_script_copy(self):
        module = load_script_module()
        rebase_commands: list[list[str]] = []
        exec_scripts: list[Path] = []

        def fake_run(args, **kwargs):
            args = list(args)
            if args[:2] == ["git", "fetch"]:
                return subprocess.CompletedProcess(args, 0)
            if args[:2] == ["git", "merge-base"]:
                return subprocess.CompletedProcess(args, 0, stdout="abc123\n")
            if args[:2] == ["git", "rebase"]:
                rebase_commands.append(args)
                exec_args = shlex.split(args[4])
                self.assertEqual(exec_args[0], sys.executable)
                self.assertEqual(exec_args[2], "--amend-step")
                exec_script = Path(exec_args[1])
                exec_scripts.append(exec_script)
                self.assertNotEqual(exec_script, SCRIPT)
                self.assertTrue(exec_script.exists())
                return subprocess.CompletedProcess(args, 0)
            raise AssertionError(f"unexpected command: {args}")

        with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
            self.assertEqual(module.main([]), 0)

        self.assertEqual(len(rebase_commands), 1)
        self.assertEqual(rebase_commands[0][:4], ["git", "rebase", "abc123", "--exec"])
        self.assertFalse(exec_scripts[0].exists())

    def test_rebase_failure_prints_recovery_without_traceback(self):
        module = load_script_module()
        exec_scripts: list[Path] = []

        def fake_run(args, **kwargs):
            args = list(args)
            if args[:2] == ["git", "fetch"]:
                return subprocess.CompletedProcess(args, 0)
            if args[:2] == ["git", "merge-base"]:
                return subprocess.CompletedProcess(args, 0, stdout="abc123\n")
            if args[:2] == ["git", "rebase"]:
                exec_scripts.append(Path(shlex.split(args[4])[1]))
                raise subprocess.CalledProcessError(1, args)
            raise AssertionError(f"unexpected command: {args}")

        with (
            mock.patch.object(module.subprocess, "run", side_effect=fake_run),
            mock.patch("sys.stderr") as stderr,
        ):
            self.assertEqual(module.main([]), 1)

        output = "".join(call.args[0] for call in stderr.write.call_args_list if call.args)
        self.assertIn("git rebase --continue", output)
        self.assertIn("git rebase --abort", output)
        self.assertNotIn("Traceback", output)
        self.assertTrue(exec_scripts[0].exists())
        shutil.rmtree(exec_scripts[0].parent)


if __name__ == "__main__":
    unittest.main()
