# Plan: Pydantic model for save-decision hook

## Context

`save-decision/hook.py` currently parses two structurally different wire formats
(Claude Code `AskUserQuestion` and Codex `request_user_input`) through raw dict
lookups and a normalization function that re-serialises back to dicts.
`_render_block` then guesses keys again when rendering.

Goal: two flat Pydantic models that both parsers fill and `_render_block` reads
via attributes.

---

## Model design  (top of `save-decision/hook.py`)

```python
from pydantic import BaseModel, Field, computed_field

class Choice(BaseModel):
    label: str = ""              # empty for the parser-injected Other entry
    description: str = ""
    preview: str = ""
    selection: bool | int = False
    # False        → not selected
    # True         → selected, order unknown (single-select)
    # 1, 2, 3 …   → selected, 1-based click order (multi-select)
    # bool(selection) is always the truthiness check for "is selected"
    note: str = ""               # free text / annotation note (Other entry only)
    is_other: bool = False       # True for the implicit Other/Notes row

    @computed_field
    @property
    def selected(self) -> bool:
        return bool(self.selection)

class Question(BaseModel):
    question: str
    header: str = ""
    multi_select: bool = False
    choices: list[Choice]        # predefined options + one parser-injected Other at end

    @computed_field
    @property
    def selected(self) -> list[Choice]:
        return sorted((c for c in self.choices if c.selected), key=lambda c: c.selection)

    @computed_field
    @property
    def timed_out(self) -> bool:
        return not self.selected
```

The parser **always appends** an `is_other=True` Choice as the last entry — even when
nothing was entered (rank=None, note=""). The renderer never constructs it; it just
reads `choice.is_other` to know it generates the display label from context instead of
`choice.label`.

`selected_preview` is not stored — the renderer reads `q.selected[0].preview` directly
when it needs the preview of the chosen option (summary section).

---

## Two parsers → one `parse_payload(payload: dict) -> list[Question]`

Dispatch on `payload.get("tool_name")`:
- `"request_user_input"` → `_parse_codex(payload)`
- anything else (`"AskUserQuestion"`, missing) → `_parse_claude(payload)`

### `_parse_claude(payload) -> list[Question]`

Sources:
- questions list: `payload["tool_input"]["questions"]`
- answers dict (keyed by question text): `payload["tool_response"]["answers"]`
- annotations dict (keyed by question text): `payload["tool_response"].get("annotations", {})`

Existing fallback for bare `payload.get("questions")` list is kept.

Per question:
1. Parse `_parse_multi_answer(answer_str, labels)` → `(click_order, custom_text)`
2. Build `choices` from option list, setting `rank` for each matched label
3. Set `custom_text`, `notes` (from `annotation.notes`), `selected_preview` (from `annotation.preview`)

For single-select `answer == "(notes only)"`: no choices get `rank`, `notes` comes from annotation.

### `_parse_codex(payload) -> list[Question]`

Sources:
- questions list: `payload["tool_input"]["questions"]` — each has an `"id"` field
- answers: JSON-parse `payload["tool_response"]` string →
  `{qid: {"answers": ["Label", "user_note: text", "None of the above"]}}`

Per question id:
- `"user_note: ..."` items → strip prefix → `notes` (joined by `"; "`)
- `"None of the above"` → discard (UI-injected sentinel)
- remaining strings → selected labels → match against option list → set `rank` in click order
- no matched labels + notes → `notes` only (equivalent to notes-only)
- nothing at all → `timed_out` is True (computed from empty state)

---

## Updated `_render_block(questions: list[Question]) -> str`

Replaces `_render_block(tool_input: dict, tool_response: dict)`.

All dict accesses become attribute reads:
- `q.question`, `q.header`, `q.multi_select`, `q.choices`, `q.selected`, `q.timed_out`
- `choice.label`, `choice.description`, `choice.preview`, `choice.selection`, `choice.selected`, `choice.note`, `choice.is_other`

Rank badge: `isinstance(c.selection, int) and not isinstance(c.selection, bool)` — True only for numbered multi-select click order, not for bare `True` (single-select).

`_parse_multi_answer` is removed — its job now happens at parse time (choices carry `selection`).

The "Other" row at the bottom of each question is rendered from:
- multi-select: `q.custom_text` → checked/unchecked + text if non-empty
- single-select with any preview option: `"_Notes: Add notes on this design._"` (any `choice.preview` present)
- single-select notes-only: `q.notes` → `[x] _Notes:_ \n > {notes}`
- single-select plain: `"_Type something[.:]_"` (unchanged logic)

Summary section: `q.selected[0].preview` replaces the old `annotation.preview` lookup.

---

## `main()` simplification

```python
payload = read_payload()
dump_debug_payload(payload, "save-decision")
questions = parse_payload(payload)
if not questions:
    return 0
block = _render_block(questions)
slug = slugify(questions[0].question, fallback="decision")
...
```

`_normalize_codex_answers` is deleted — its logic moves into `_parse_codex`.

---

## Test update  (`scripts/°base/tests/test_save_decision.py`)

Update call site from `_render_block(tool_input, tool_response)` to:

```python
questions = _hook.parse_payload({
    "tool_name": "AskUserQuestion",
    "tool_input": {"questions": _data["questions"]},
    "tool_response": {
        "answers": _data["answers"],
        "annotations": _data.get("annotations", {}),
    },
})
actual = _hook._render_block(questions)
```

---

## Codex parsing notes (from Rust schema)

`ai/references/.../request_user_input.rs` defines:
- `RequestUserInputQuestion`: `id`, `header`, `question`, `isOther`, `isSecret`, `options?: [{label, description}]` — no `preview` on options
- `RequestUserInputResponse`: `answers: HashMap<String, {answers: Vec<String>}>` — keyed by question id

Answer strings in the Vec can be:
- an option label (e.g. `"High"`)
- `"None of the above"` — user clicked the auto-added Other button
- `"user_note: text"` — note typed alongside the answer

Distinguish timeout (question key **absent** from `answers`) from "None of the above"
(key **present**, value `{answers: ["None of the above"]}`):
- timeout → all choices `selection = False`, `timed_out = True`
- "None of the above" → Other choice `selection = True`, `note = ""`

---

## New test file: `scripts/°base/tests/test_save_decision_codex.py`

Inline fixtures (no external spec file — assertions on model attributes, not rendered
markdown). Helper:

```python
def _make_payload(questions, answers):
    return {
        "tool_name": "request_user_input",
        "tool_input": {"questions": questions, "autoResolutionMs": 60000},
        "tool_response": json.dumps({"answers": answers}),
    }
```

Test cases — each calls `_hook.parse_payload(payload)` and asserts on the result:

| Test | Payload shape | Key assertions |
|---|---|---|
| `test_single_label_selected` | 1 question, `{"answers": ["A"]}` | A: `selected=True`, B: `False`, other: `selected=False, note=""` |
| `test_single_label_with_note` | 1 question, `{"answers": ["High", "user_note: urgent"]}` | "High": `selected=True`, other: `selected=True, note="urgent"` |
| `test_none_of_the_above_no_note` | 1 question, `{"answers": ["None of the above"]}` | all predefined `selected=False`, other: `selected=True, note=""` |
| `test_none_of_the_above_with_note` | 1 question, `{"answers": ["None of the above", "user_note: see me"]}` | other: `selected=True, note="see me"` |
| `test_timeout_key_absent` | 1 question, `{}` (empty answers) | `q.timed_out=True`, all `selected=False` |
| `test_multi_select_click_order` | 1 multi question, `{"answers": ["C", "A", "B"]}` | A: `selection=2`, B: `selection=3`, C: `selection=1` |
| `test_multi_with_note` | 1 multi question, `{"answers": ["A", "user_note: extra"]}` | A: `selection=1`, other: `selection=2, note="extra"` |
| `test_multiple_questions` | 3 questions, mixed answers | correct Question list order, each parsed independently |

Each test also checks `choice.is_other` is True only on the last choice, and
`len(q.choices) == len(options) + 1` for every question.

---

## Files changed

| File | Change |
|---|---|
| `scripts/°base/ai/hooks/save-decision/hook.py` | Add `Choice`/`Question` models + `parse_payload` + two parsers; rewrite `_render_block`; simplify `main`; delete `_normalize_codex_answers` + `_parse_multi_answer` |
| `scripts/°base/tests/test_save_decision.py` | Update call site to use `parse_payload` |
| `scripts/°base/tests/test_save_decision_codex.py` | New — Codex parser unit tests (inline fixtures) |

---

## Verification

```bash
cd /Users/user/Documents/programming/Python/base
python -m unittest scripts/°base/tests/test_save_decision.py scripts/°base/tests/test_save_decision_codex.py -v
```
