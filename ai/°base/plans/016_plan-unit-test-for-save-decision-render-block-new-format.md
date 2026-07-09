# Plan: Unit test for save-decision `_render_block` new format

## Context

`save-decision/hook.py::_render_block` currently produces a simple blockquote with questions, options, and a JSON dump. The spec in `ai/°base/errors/12.expected.md` defines a new rich format: a collapsible `<details>` block with a numbered summary, per-question checkboxes, multi-select click-order badges, preview code blocks, and notes. This test is TDD — it drives the implementation by asserting the new format before the code is written.

## File to create

**`scripts/°base/tests/test_save_decision.py`**

## Approach

### 1. Load the hook module directly (no git repo)

Use `importlib.util.spec_from_file_location` to load `save-decision/hook.py` and access `_render_block` without subprocess overhead. Works because `hook.py` uses `Path(__file__)` to insert the `hooks/` directory into `sys.path` before importing `_lib`, so all paths resolve correctly when loaded from its real location.

### 2. Parse spec file for input and expected output

```python
_SPEC_PATH = ROOT / "ai" / "°base" / "errors" / "12.expected.md"
```

- **Input JSON** — first ` ```json … ``` ` block (lines 2-178), parsed with `json.loads`.
- **Expected output** — section between `# \`query.md\` addition\n\n` and `\n# Summary of format options` (inclusive of the trailing blank line: `text[cs:ce+1]`).

### 3. New `_render_block` signature

The test calls:
```python
_hook._render_block(
    tool_input={"questions": data["questions"]},
    tool_response={
        "answers": data["answers"],
        "annotations": data.get("annotations", {}),
    },
)
```

This replaces the current `(tool_input: dict, answer: str)` signature with `(tool_input: dict, tool_response: dict)`. The test will fail with the current implementation — that's the TDD intent.

### 4. Single test method

```python
class RenderBlockTests(unittest.TestCase):
    def test_full_example_from_spec(self):
        actual = _hook._render_block(tool_input, tool_response)
        self.assertEqual(actual, _expected_output)
```

## Critical files

| File | Role |
|---|---|
| `scripts/°base/tests/test_save_decision.py` | New test file (create) |
| `scripts/°base/ai/hooks/save-decision/hook.py` | Module under test (read-only here) |
| `ai/°base/errors/12.expected.md` | Spec — provides both input JSON and expected output |

## Verification

```bash
uv run --project scripts/°base python -m unittest scripts.°base.tests.test_save_decision -v
```

Expected: `FAIL` with current `_render_block` (wrong output format). After the implementation is updated, this test must pass.
