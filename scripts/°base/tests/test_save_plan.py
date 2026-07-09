"""Tests for save-plan/hook.py — _plan_from_edit and _plan_from_write.

Covers the fix that added Edit support: previously only Write was handled,
so editing a plan file via Edit left it unrecorded.

The parametrized real-payload test loads:
    ai/°base/output/debug/20260630-033350_321912-record-memory.json
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_HOOK_PATH = ROOT / "scripts" / "°base" / "ai" / "hooks" / "save-plan" / "hook.py"
_DEBUG_FILE = (
    ROOT / "ai" / "°base" / "output" / "debug"
    / "20260630-033350_321912-record-memory.json"
)

_PLAN_PATH_RE = r"/\.claude/plans/[^/]+\.md$"


def _load_hook():
    spec = importlib.util.spec_from_file_location("_save_plan_hook_test", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_hook = _load_hook()
_payload = json.loads(_DEBUG_FILE.read_text(encoding="utf-8"))


def _tmp_plan_path(tmp: str, name: str = "test-plan.md") -> Path:
    """Create a path that satisfies the .claude/plans/ regex inside a temp dir."""
    plan_dir = Path(tmp) / ".claude" / "plans"
    plan_dir.mkdir(parents=True)
    return plan_dir / name


class PlanFromEditPathFilterTests(unittest.TestCase):
    """_plan_from_edit must reject anything outside ~/.claude/plans/."""

    def test_non_plan_path_returns_empty(self):
        self.assertEqual(_hook._plan_from_edit({"file_path": "/tmp/some-file.md"}), "")

    def test_no_claude_dir_returns_empty(self):
        self.assertEqual(_hook._plan_from_edit({"file_path": "/home/user/plans/x.md"}), "")

    def test_missing_file_returns_empty_not_raises(self):
        tool_input = {"file_path": "/nonexistent/.claude/plans/plan.md"}
        self.assertEqual(_hook._plan_from_edit(tool_input), "")

    def test_empty_file_path_returns_empty(self):
        self.assertEqual(_hook._plan_from_edit({}), "")
        self.assertEqual(_hook._plan_from_edit({"file_path": ""}), "")


class PlanFromEditContentTests(unittest.TestCase):
    """_plan_from_edit reads and strips the file at a matching path."""

    def test_reads_file_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = _tmp_plan_path(tmp)
            plan_file.write_text("# My Plan\n\nDo the thing.\n", encoding="utf-8")
            result = _hook._plan_from_edit({"file_path": str(plan_file)})
            self.assertEqual(result, "# My Plan\n\nDo the thing.")

    def test_strips_trailing_newlines(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = _tmp_plan_path(tmp)
            plan_file.write_text("  content  \n\n", encoding="utf-8")
            result = _hook._plan_from_edit({"file_path": str(plan_file)})
            self.assertEqual(result, "content")


class PlanFromWriteDoesNotHandleEditTests(unittest.TestCase):
    """_plan_from_write must return '' for Edit payloads (no content key)."""

    def test_write_returns_empty_for_edit_payload(self):
        """_plan_from_write has no content field in an Edit payload → always ''."""
        tool_input = _payload["tool_input"]
        self.assertEqual(_hook._plan_from_write(tool_input), "")


class DebugPayloadTests(unittest.TestCase):
    """Assertions against the recorded debug payload from the triggering session."""

    def test_tool_name_is_edit(self):
        self.assertEqual(_payload["tool_name"], "Edit")

    def test_hook_event_is_post_tool_use(self):
        self.assertEqual(_payload["hook_event_name"], "PostToolUse")

    def test_file_path_matches_plan_pattern(self):
        import re
        file_path = _payload["tool_input"]["file_path"]
        self.assertRegex(file_path, _PLAN_PATH_RE)

    def test_plan_from_edit_reads_matching_path(self):
        """_plan_from_edit reads file content when given the debug payload's path."""
        plan_body = _payload["tool_response"]["originalFile"]
        file_name = Path(_payload["tool_input"]["file_path"]).name
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = _tmp_plan_path(tmp, file_name)
            plan_file.write_text(plan_body, encoding="utf-8")
            tool_input = {**_payload["tool_input"], "file_path": str(plan_file)}
            result = _hook._plan_from_edit(tool_input)
            self.assertEqual(result, plan_body.strip())

    def test_original_file_is_non_empty_markdown_plan(self):
        original = _payload["tool_response"]["originalFile"]
        self.assertTrue(original.strip().startswith("# Plan:"))


class CopilotPlanPathTests(unittest.TestCase):
    """save-plan/hook.py must also recognize Copilot's session plan file:
    ~/.copilot/session-state/<session_id>/plan.md — and its `create`/`edit`
    tool names, which use `path`/`file_text` instead of Claude's
    `file_path`/`content`."""

    def _copilot_plan_path(self, tmp: str, session_id: str = "abc-123") -> Path:
        plan_dir = Path(tmp) / ".copilot" / "session-state" / session_id
        plan_dir.mkdir(parents=True)
        return plan_dir / "plan.md"

    def test_is_plan_file_path_accepts_copilot_session_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._copilot_plan_path(tmp)
            self.assertTrue(_hook._is_plan_file_path(str(plan_path)))

    def test_is_plan_file_path_rejects_non_plan_copilot_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / ".copilot" / "session-state" / "abc-123" / "notes.md"
            other.parent.mkdir(parents=True)
            self.assertFalse(_hook._is_plan_file_path(str(other)))

    def test_plan_from_write_reads_copilot_create_tool_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._copilot_plan_path(tmp)
            body = "# Plan: Copilot support\n\nSome content.\n"
            result = _hook._plan_from_write({"path": str(plan_path), "file_text": body})
            self.assertEqual(result, body.strip())

    def test_plan_from_edit_reads_copilot_edit_tool_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = self._copilot_plan_path(tmp)
            body = "# Plan: Copilot support\n\nUpdated content.\n"
            plan_path.write_text(body, encoding="utf-8")
            result = _hook._plan_from_edit({"path": str(plan_path), "old_str": "x", "new_str": "y"})
            self.assertEqual(result, body.strip())

    def test_plan_from_copilot_session_reads_plan_md_by_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_id = "session-xyz"
            plan_path = self._copilot_plan_path(tmp, session_id)
            body = "# Plan: fallback read\n"
            plan_path.write_text(body, encoding="utf-8")
            import unittest.mock as mock
            with mock.patch.object(Path, "home", return_value=Path(tmp)):
                result = _hook._plan_from_copilot_session({"session_id": session_id})
            self.assertEqual(result, body.strip())

    def test_plan_from_copilot_session_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            import unittest.mock as mock
            with mock.patch.object(Path, "home", return_value=Path(tmp)):
                result = _hook._plan_from_copilot_session({"session_id": "nope"})
            self.assertEqual(result, "")

    def test_plan_from_copilot_session_no_session_id_returns_empty(self):
        self.assertEqual(_hook._plan_from_copilot_session({}), "")


if __name__ == "__main__":
    unittest.main()
