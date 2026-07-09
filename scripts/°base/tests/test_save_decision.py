"""TDD test for the new save-decision _render_block format.

Input JSON and expected output are both read from the spec file:
    ai/°base/errors/12.expected.md

The test fails with the current implementation and must pass once
_render_block is updated to produce the rich collapsible format.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
_HOOK_PATH = ROOT / "scripts" / "°base" / "ai" / "hooks" / "save-decision" / "hook.py"
_SPEC_PATH = ROOT / "ai" / "°base" / "errors" / "12.expected.md"


def _load_hook():
    spec = importlib.util.spec_from_file_location("_save_decision_hook_test", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_spec() -> tuple[dict, str]:
    """Return (input_data, expected_output) extracted from 12.expected.md."""
    text = _SPEC_PATH.read_text(encoding="utf-8")

    # Input JSON: first ```json … ``` block
    js = text.index("```json\n") + len("```json\n")
    je = text.index("\n```\n", js)
    data = json.loads(text[js:je])

    # Expected output: content of the "# `query.md` addition" section,
    # up to and including the trailing blank line before "# Summary …".
    sm = "# `query.md` addition\n\n"
    cs = text.index(sm) + len(sm)
    em = "\n# Summary of format options"
    ce = text.index(em, cs)
    expected = text[cs : ce + 1]  # ce+1 includes the trailing blank line

    return data, expected


_hook = _load_hook()
_data, _expected_output = _parse_spec()


class RenderBlockTests(unittest.TestCase):
    def test_full_example_from_spec(self):
        """_render_block produces the rich format defined in 12.expected.md."""
        questions = _hook.parse_payload({
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": _data["questions"]},
            "tool_response": {
                "answers": _data["answers"],
                "annotations": _data.get("annotations", {}),
            },
        })
        actual = _hook._render_block(questions)
        self.assertEqual(actual, _expected_output)


if __name__ == "__main__":
    unittest.main()
