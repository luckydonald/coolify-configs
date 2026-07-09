#!/usr/bin/env python3
"""Unit tests for the Codex request_user_input parser in save-decision/hook.py.

Tests parse_payload() with Codex-format payloads (tool_name = "request_user_input")
and asserts on the resulting list[Question] model attributes.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
_HOOK_PATH = ROOT / "scripts" / "°base" / "ai" / "hooks" / "save-decision" / "hook.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("_save_decision_hook_codex_test", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_hook = _load_hook()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_payload(questions, answers):
    """Build a minimal Codex PostToolUse hook payload."""
    return {
        "tool_name": "request_user_input",
        "tool_input": {"questions": questions, "autoResolutionMs": 60000},
        "tool_response": json.dumps({"answers": answers}),
    }


# Reusable question definitions

_Q_SINGLE = [
    {
        "id": "q1",
        "header": "Pick",
        "question": "Which option?",
        "options": [
            {"label": "A", "description": "First."},
            {"label": "B", "description": "Second."},
        ],
    }
]

_Q_MULTI = [
    {
        "id": "q1",
        "header": "Pick",
        "question": "Which options?",
        "multiSelect": True,
        "options": [
            {"label": "A", "description": "First."},
            {"label": "B", "description": "Second."},
            {"label": "C", "description": "Third."},
        ],
    }
]


class ParseCodexTests(unittest.TestCase):

    def _parse(self, payload):
        return _hook.parse_payload(payload)

    def _other(self, q):
        return next(c for c in q.choices if c.is_other)

    def _choice(self, q, label):
        return next(c for c in q.choices if c.label == label)

    # --- structural invariants ---

    def test_other_always_last_and_unique(self):
        """Every question has exactly one is_other Choice and it is last."""
        qs = self._parse(_make_payload(_Q_SINGLE, {"q1": {"answers": ["A"]}}))
        q = qs[0]
        self.assertTrue(q.choices[-1].is_other)
        self.assertEqual(sum(1 for c in q.choices if c.is_other), 1)
        self.assertEqual(len(q.choices), len(_Q_SINGLE[0]["options"]) + 1)

    # --- single-select cases ---

    def test_single_label_selected(self):
        qs = self._parse(_make_payload(_Q_SINGLE, {"q1": {"answers": ["A"]}}))
        q = qs[0]
        self.assertFalse(q.timed_out)
        self.assertTrue(self._choice(q, "A").selected)
        self.assertFalse(self._choice(q, "B").selected)
        other = self._other(q)
        self.assertFalse(other.selected)
        self.assertEqual(other.note, "")

    def test_single_label_with_note(self):
        """Label selected + user_note → note on the selected predefined choice, Other unselected."""
        qs = self._parse(_make_payload(_Q_SINGLE, {
            "q1": {"answers": ["A", "user_note: urgent"]}
        }))
        q = qs[0]
        a_choice = self._choice(q, "A")
        self.assertTrue(a_choice.selected)
        self.assertEqual(a_choice.note, "urgent")
        other = self._other(q)
        self.assertFalse(other.selected)
        self.assertEqual(other.note, "")

    def test_none_of_the_above_no_note(self):
        """'None of the above' → Other selected, no predefined choice selected."""
        qs = self._parse(_make_payload(_Q_SINGLE, {
            "q1": {"answers": ["None of the above"]}
        }))
        q = qs[0]
        self.assertFalse(q.timed_out)
        for c in q.choices:
            if not c.is_other:
                self.assertFalse(c.selected, msg=f"{c.label!r} should not be selected")
        other = self._other(q)
        self.assertTrue(other.selected)
        self.assertEqual(other.note, "")

    def test_none_of_the_above_with_note(self):
        qs = self._parse(_make_payload(_Q_SINGLE, {
            "q1": {"answers": ["None of the above", "user_note: see me"]}
        }))
        other = self._other(self._parse(_make_payload(_Q_SINGLE, {
            "q1": {"answers": ["None of the above", "user_note: see me"]}
        }))[0])
        self.assertTrue(other.selected)
        self.assertEqual(other.note, "see me")

    def test_timeout_key_absent(self):
        """Question key absent from answers dict → timed_out, all unselected."""
        qs = self._parse(_make_payload(_Q_SINGLE, {}))
        q = qs[0]
        self.assertTrue(q.timed_out)
        for c in q.choices:
            self.assertFalse(c.selected, msg=f"{c.label!r} should not be selected")

    # --- multi-select cases ---

    def test_multi_select_click_order(self):
        """Answer list order → 1-based rank on each matching Choice."""
        qs = self._parse(_make_payload(_Q_MULTI, {
            "q1": {"answers": ["C", "A", "B"]}
        }))
        q = qs[0]
        self.assertFalse(q.timed_out)
        self.assertEqual(self._choice(q, "C").selection, 1)
        self.assertEqual(self._choice(q, "A").selection, 2)
        self.assertEqual(self._choice(q, "B").selection, 3)
        self.assertFalse(self._other(q).selected)

    def test_multi_with_note(self):
        """Label selected + user_note → label has rank, Other selected (order unknown)."""
        qs = self._parse(_make_payload(_Q_MULTI, {
            "q1": {"answers": ["A", "user_note: extra"]}
        }))
        q = qs[0]
        self.assertEqual(self._choice(q, "A").selection, 1)
        other = self._other(q)
        self.assertTrue(other.selected)
        # selection is True (bool) because the Other entry is always last —
        # its click position relative to predefined choices is unknown.
        self.assertIs(other.selection, True)
        self.assertEqual(other.note, "extra")

    def test_multi_timeout(self):
        qs = self._parse(_make_payload(_Q_MULTI, {}))
        q = qs[0]
        self.assertTrue(q.timed_out)

    # --- multiple questions ---

    def test_multiple_questions_order_and_independence(self):
        """Three questions in one payload → correct list order, parsed independently."""
        questions_def = [
            {
                "id": "bin", "header": "Bin", "question": "Binary?",
                "options": [{"label": "Yes", "description": ""}, {"label": "No", "description": ""}],
            },
            {
                "id": "prio", "header": "Prio", "question": "Priority?",
                "options": [{"label": "High", "description": ""}, {"label": "Low", "description": ""}],
            },
            {
                "id": "mode", "header": "Mode", "question": "Mode?",
                "options": [{"label": "Plan", "description": ""}, {"label": "Action", "description": ""}],
            },
        ]
        answers = {
            "bin": {"answers": ["Yes"]},
            "prio": {"answers": ["High", "user_note: urgent"]},
            # "mode" absent → timed out
        }
        qs = self._parse(_make_payload(questions_def, answers))
        self.assertEqual(len(qs), 3)

        q0 = qs[0]
        self.assertEqual(q0.question, "Binary?")
        self.assertTrue(next(c for c in q0.choices if c.label == "Yes").selected)
        self.assertFalse(q0.timed_out)

        q1 = qs[1]
        self.assertEqual(q1.question, "Priority?")
        high = next(c for c in q1.choices if c.label == "High")
        self.assertTrue(high.selected)
        self.assertEqual(high.note, "urgent")
        other1 = next(c for c in q1.choices if c.is_other)
        self.assertFalse(other1.selected)
        self.assertEqual(other1.note, "")

        q2 = qs[2]
        self.assertEqual(q2.question, "Mode?")
        self.assertTrue(q2.timed_out)


if __name__ == "__main__":
    unittest.main()
