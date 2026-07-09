"""Render tests driven by real debug payloads recorded during hook integration testing.

Spec: ai/°base/errors/15.expected.md

Each `# N` section in the spec maps to one subTest. Format:

    # {N}
    ## Input
    | test | {N}        |
    | ---- | ---------- |
    | type | claude     |  ← or "codex"
    | file | `filename` |  ← file under ai/°base/output/debug/

    ## `query.md` addition

    {rendered output block}

    ---

New entries can be added to the spec at any time; the test discovers them dynamically.
"""
from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
_HOOK_PATH = ROOT / "scripts" / "°base" / "ai" / "hooks" / "save-decision" / "hook.py"
_SPEC_PATH = ROOT / "ai" / "°base" / "errors" / "15.expected.md"
_DEBUG_DIR  = ROOT / "ai" / "°base" / "output" / "debug"


def _load_hook():
    spec = importlib.util.spec_from_file_location("_save_decision_hook_15_test", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_table(text: str) -> dict[str, str]:
    result = {}
    for m in re.finditer(r'^\| (\w+) \| (.+?) \|$', text, re.MULTILINE):
        key = m.group(1)
        val = m.group(2).strip().strip('`')
        if re.match(r'^-+$', val):
            continue  # skip separator row values
        result[key] = val
    return result


def _parse_entries() -> list[dict]:
    """Parse all # N sections from the spec into entry dicts.

    Returns list of {num, table, output} in order of appearance.
    """
    text = _SPEC_PATH.read_text(encoding='utf-8')

    # Split into sections on bare '---' lines, preserving section content
    raw_sections: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip() == '---':
            raw_sections.append(current)
            current = []
        else:
            current.append(line)
    if current:
        raw_sections.append(current)

    entries = []
    for raw in raw_sections:
        section = ''.join(raw).strip()
        if not section:
            continue
        m = re.match(r'^# (\d+)\n', section)
        if not m:
            continue
        num = int(m.group(1))
        table = _parse_table(section)

        output_m = re.search(r'^## `query\.md` addition\n\n(.+)', section, re.DOTALL | re.MULTILINE)
        if not output_m:
            continue
        # Normalise trailing newlines: _render_block always ends with \n\n
        output = output_m.group(1).rstrip('\n') + '\n\n'

        entries.append({'num': num, 'table': table, 'output': output})
    return entries


_hook = _load_hook()
_entries = _parse_entries()


class RenderFromDebugTests(unittest.TestCase):

    def test_entry_sequence(self):
        """Entries form a contiguous sequence starting at 1."""
        nums = [e['num'] for e in _entries]
        self.assertGreater(len(nums), 0, "spec has no entries")
        self.assertEqual(nums[0], 1, "sequence must start at 1")
        for i, n in enumerate(nums, 1):
            self.assertEqual(n, i, f"entry at position {i} has num={n}, expected {i}")

    def test_render_matches_spec(self):
        """Each spec entry renders identically to its expected block."""
        for entry in _entries:
            num = entry['num']
            table = entry['table']
            typ = table.get('type', '')
            filename = table.get('file', '')
            label = f"{num} - {typ} - {filename}"

            with self.subTest(label):
                # Section heading num and table 'test' value must agree
                self.assertEqual(
                    str(num), table.get('test', ''),
                    f"section # {num} heading does not match table test={table.get('test')!r}",
                )

                payload = json.loads((_DEBUG_DIR / filename).read_text(encoding='utf-8'))
                tool = 'codex' if typ == 'codex' else 'claude'
                questions = _hook.parse_payload(payload)
                actual = _hook._render_block(questions, tool=tool)
                self.assertEqual(actual, entry['output'])


if __name__ == '__main__':
    unittest.main()
