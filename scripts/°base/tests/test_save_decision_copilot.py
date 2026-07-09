#!/usr/bin/env python3
"""Unit tests for the Copilot ask_user parser in save-decision/hook.py.

Tests parse_payload() with Copilot-format payloads and asserts on the
resulting list[Question] model attributes. Copilot's ask_user schema is much
simpler than Claude's AskUserQuestion: a single question, a flat list of
choice strings, and an allow_freeform flag (instead of multiSelect /
per-choice label+description+preview).

Two things below are confirmed against **real** hook payloads captured in
`ai/°base/output/debug/*-save-decision.json` during an actual Copilot CLI
session, not from the published docs (which turned out to be misleading
here):

- `tool_name` is *always* reported as the Claude-mapped name
  (`"AskUserQuestion"`), never the literal `"ask_user"` runtime name — see
  hooks-reference.md's runtime→Claude tool name table: PostToolUse payloads
  always use the Claude name. This means dispatch cannot rely on
  `tool_name == "ask_user"` alone; it must use the CLI `ai_tool` argv (or the
  payload-shape fallback in `_looks_like_copilot_payload`).
- The answer lives in `tool_result.text_result_for_llm` (a human-readable
  string prefixed with "User selected: <label>" or "User responded: <text>"),
  not a Claude-style `tool_response` dict/JSON string.

`_make_payload`/legacy tests below intentionally keep covering the
originally-assumed `tool_response`-based shape too, since `_parse_copilot`
retains a fallback for it (harmless extra robustness, and protects any
external tooling that may already depend on it).
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_HOOK_PATH = ROOT / "scripts" / "°base" / "ai" / "hooks" / "save-decision" / "hook.py"
_DEBUG_DIR = ROOT / "ai" / "°base" / "output" / "debug"


def _load_hook():
    spec = importlib.util.spec_from_file_location("_save_decision_hook_copilot_test", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_hook = _load_hook()


def _make_real_payload(question, choices, allow_freeform, text_result_for_llm):
    """Build a payload matching the real, confirmed Copilot PostToolUse shape:
    `tool_name` is the Claude-mapped name, and the answer lives in
    `tool_result.text_result_for_llm`."""
    tool_input = {"question": question, "allow_freeform": allow_freeform}
    if choices is not None:
        tool_input["choices"] = choices
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": tool_input,
        "tool_result": {"result_type": "success", "text_result_for_llm": text_result_for_llm},
    }


def _make_payload(question, choices, allow_freeform, answer):
    """Legacy/originally-assumed shape (`tool_name: "ask_user"`,
    `tool_response`) — still exercised for the fallback parsing path."""
    return {
        "tool_name": "ask_user",
        "tool_input": {"question": question, "choices": choices, "allow_freeform": allow_freeform},
        "tool_response": answer,
    }


class ParseCopilotRealPayloadTests(unittest.TestCase):
    """Tests using the real, confirmed payload shape (tool_name =
    "AskUserQuestion", answer in tool_result.text_result_for_llm)."""

    def test_dispatches_via_ai_tool_argv_despite_claude_tool_name(self):
        payload = _make_real_payload("Pick one", ["A", "B"], True, "User selected: A")
        questions = _hook.parse_payload(payload, ai_tool="copilot")
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].question, "Pick one")

    def test_tool_name_alone_no_longer_dispatches_correctly(self):
        """Regression guard: without the CLI ai_tool argv, tool_name =
        "AskUserQuestion" alone would normally route to _parse_claude and (since
        there is no nested "questions" list) silently find nothing — unless the
        payload-shape fallback in _looks_like_copilot_payload saves it."""
        payload = _make_real_payload("Pick one", ["A", "B"], True, "User selected: A")
        questions = _hook.parse_payload(payload)  # no ai_tool given
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].question, "Pick one")

    def test_user_selected_matches_choice(self):
        payload = _make_real_payload("Pick one", ["A", "B", "C"], True, "User selected: B")
        [q] = _hook.parse_payload(payload, ai_tool="copilot")
        selected = [c.label for c in q.choices if c.selected and not c.is_other]
        self.assertEqual(selected, ["B"])
        self.assertFalse(q.multi_select)

    def test_user_responded_lands_in_other(self):
        payload = _make_real_payload(
            "Pick one", ["A", "B"], True, "User responded: Something else entirely"
        )
        [q] = _hook.parse_payload(payload, ai_tool="copilot")
        other = next(c for c in q.choices if c.is_other)
        self.assertTrue(other.selected)
        self.assertEqual(other.note, "Something else entirely")
        self.assertFalse(any(c.selected for c in q.choices if not c.is_other))

    def test_pure_freeform_question_has_no_choices(self):
        """A question asked with no `choices` array at all (pure freeform)."""
        payload = _make_real_payload(
            "What's one thing?", None, True, "User responded: a test answer"
        )
        [q] = _hook.parse_payload(payload, ai_tool="copilot")
        other = next(c for c in q.choices if c.is_other)
        self.assertTrue(other.selected)
        self.assertEqual(other.note, "a test answer")
        self.assertEqual(len([c for c in q.choices if not c.is_other]), 0)

    def test_timed_out_when_no_text_result(self):
        payload = _make_real_payload("Pick one", ["A", "B"], True, "")
        [q] = _hook.parse_payload(payload, ai_tool="copilot")
        self.assertTrue(q.timed_out)

    def test_infer_tool_prefers_cli_arg_over_misleading_tool_name(self):
        payload = _make_real_payload("Pick one", ["A", "B"], True, "User selected: A")
        self.assertEqual(_hook._infer_tool(payload, "copilot"), "copilot")

    def test_infer_tool_falls_back_to_payload_shape_without_cli_arg(self):
        payload = _make_real_payload("Pick one", ["A", "B"], True, "User selected: A")
        self.assertEqual(_hook._infer_tool(payload, "unknown"), "copilot")


class ParseCopilotCapturedPayloadTests(unittest.TestCase):
    """Regression tests against real, unmodified `PostToolUse` payloads
    captured (and `git add -f`'d, since `ai/°base/output/debug/` is normally
    gitignored) from an actual Copilot CLI session — the exact bug repro that
    prompted this fix: none of these 3 questions made it into `query.md`
    before `parse_payload`/`_infer_tool` learned to prefer the CLI `ai_tool`
    argv and to read `tool_result.text_result_for_llm`."""

    def test_choice_selected_payload(self):
        payload = json.loads(
            (_DEBUG_DIR / "20260708-185721_643221-save-decision.json").read_text(encoding="utf-8")
        )
        [q] = _hook.parse_payload(payload, ai_tool="copilot")
        self.assertTrue(q.question.startswith("This is a demo of the ask_user tool's question types."))
        selected = [c.label for c in q.choices if c.selected and not c.is_other]
        self.assertEqual(selected, ["Multiple-choice, no freeform fallback (Recommended)"])
        self.assertFalse(q.timed_out)

    def test_pure_freeform_payload(self):
        payload = json.loads(
            (_DEBUG_DIR / "20260708-185900_706055-save-decision.json").read_text(encoding="utf-8")
        )
        [q] = _hook.parse_payload(payload, ai_tool="copilot")
        other = next(c for c in q.choices if c.is_other)
        self.assertTrue(other.selected)
        self.assertEqual(other.note, "now do multi-choice.")

    def test_choice_selected_with_freeform_disallowed_payload(self):
        payload = json.loads(
            (_DEBUG_DIR / "20260708-185956_357441-save-decision.json").read_text(encoding="utf-8")
        )
        [q] = _hook.parse_payload(payload, ai_tool="copilot")
        selected = [c.label for c in q.choices if c.selected and not c.is_other]
        self.assertEqual(selected, ["Show me exit_plan_mode's approval menu instead"])

    def test_captured_payloads_render_with_copilot_glyph(self):
        for name in (
            "20260708-185721_643221-save-decision.json",
            "20260708-185900_706055-save-decision.json",
            "20260708-185956_357441-save-decision.json",
        ):
            payload = json.loads((_DEBUG_DIR / name).read_text(encoding="utf-8"))
            questions = _hook.parse_payload(payload, ai_tool="copilot")
            block = _hook._render_block(questions, tool=_hook._infer_tool(payload, "copilot"))
            self.assertTrue(block.startswith("◆ Question answered.\n"), name)


class ParseCopilotTests(unittest.TestCase):
    """Legacy/fallback-shape tests (tool_name = "ask_user", tool_response)."""

    def test_dispatches_to_copilot_parser(self):
        payload = _make_payload("Pick one", ["A", "B"], True, "A")
        questions = _hook.parse_payload(payload)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].question, "Pick one")

    def test_selects_matching_choice(self):
        payload = _make_payload("Pick one", ["A", "B", "C"], True, "B")
        [q] = _hook.parse_payload(payload)
        selected = [c.label for c in q.choices if c.selected and not c.is_other]
        self.assertEqual(selected, ["B"])
        self.assertFalse(q.multi_select)

    def test_freeform_answer_lands_in_other(self):
        payload = _make_payload("Pick one", ["A", "B"], True, "Something else entirely")
        [q] = _hook.parse_payload(payload)
        other = next(c for c in q.choices if c.is_other)
        self.assertTrue(other.selected)
        self.assertEqual(other.note, "Something else entirely")
        self.assertFalse(any(c.selected for c in q.choices if not c.is_other))

    def test_no_freeform_when_disallowed_and_no_match(self):
        payload = _make_payload("Pick one", ["A", "B"], False, "Not a listed choice")
        [q] = _hook.parse_payload(payload)
        other = next(c for c in q.choices if c.is_other)
        self.assertFalse(other.selected)

    def test_timed_out_when_no_answer(self):
        payload = _make_payload("Pick one", ["A", "B"], True, "")
        [q] = _hook.parse_payload(payload)
        self.assertTrue(q.timed_out)

    def test_json_object_tool_response(self):
        payload = _make_payload("Pick one", ["A", "B"], True, json.dumps({"answer": "A"}))
        [q] = _hook.parse_payload(payload)
        selected = [c.label for c in q.choices if c.selected and not c.is_other]
        self.assertEqual(selected, ["A"])


class RenderCopilotGlyphTests(unittest.TestCase):
    def test_copilot_glyph_used_for_ask_user(self):
        payload = _make_real_payload("Pick one", ["A", "B"], True, "User selected: A")
        questions = _hook.parse_payload(payload, ai_tool="copilot")
        block = _hook._render_block(questions, tool="copilot")
        self.assertTrue(block.startswith("◆ Question answered.\n"))


if __name__ == "__main__":
    unittest.main()
