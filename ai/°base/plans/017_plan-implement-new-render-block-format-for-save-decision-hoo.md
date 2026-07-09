# Plan: Implement new `_render_block` format for save-decision hook

## Context

`save-decision/hook.py::_render_block` currently produces a flat blockquote (questions + JSON dump). The TDD test at `scripts/°base/tests/test_save_decision.py` asserts the new rich format: collapsible `<details>` with a numbered summary, per-question checkboxes, multi-select click-order badges, preview code blocks, and notes. The spec is `ai/°base/errors/12.expected.md`. Test drives the implementation.

## File to modify

**`scripts/°base/ai/hooks/save-decision/hook.py`**

---

## Rendering rules (from spec + analysis)

### Output skeleton
```
❯ Question answered.\n
> <details><summary>\n
>\n
>> 1. Q1 text?\n
>>    - answer1\n
>> 2. Q2 text?\n
...
>\n
> (click to expand)\n
>\n
> </summary>\n
>\n
[per-question detail blocks, separated by >\n]
> </details>\n
> \n
\n
```

### Summary section
- Header per question: `f">> {i}. {question}\n"` (1-indexed)
- Answer sub-item: `f">>{'':>{len(str(i))+3}}- {text}\n"` (indent = `len(str(i))+3` spaces after `>>`)
  - Single/multi normal answer: the `answers[q]` string verbatim
  - `(notes only)`: show `annotations[q]["notes"]` instead
  - `annotations[q]["preview"]` present: show `answers[q]` + preview code block:
    ```
    >>    - No logging\n
    >>      ```text\n
    >>      # nothing emitted here\n
    >>      ```\n
    ```
    (preview block indent = `len(str(i))+5` spaces after `>>`)

### Details section (per question)
```
>> **{header}** ({i}/{total}) <kbd>{Single|Multi} Select</kbd><br>\n
>> {question}\n
```
Then for each official option (1-indexed as `n`):
```
> - [{check}] {n}\. {label}{badge}\n
>   - _{description}_\n
[if preview:]
>   - ```[text]\n
>     {preview_line}\n    ← 5 spaces after >
>\n                       ← blank lines in preview
>     ```\n
```
Then the "Other" pseudo-option (always last, at `n = len(options) + 1`).

Blank blockquote separator between questions: `">\n"`.

---

## Selection logic

### Single select
- `answer == "(notes only)"` → no official option checked; Notes option `[x]` with `_Notes:_` label, sub-item blockquote = `annotations[q]["notes"]`
- `annotations[q].get("preview")` present → **no option checked at all** (all `[ ]`), Notes option `[ ]` with full label
- Otherwise → match `answer` against option labels; matched option `[x]`, rest `[ ]`

### Multi select
Parse with **greedy left-to-right** algorithm:
```python
def _parse_multi_answer(answer, options):
    labels = [o["label"] for o in options]
    remaining = answer
    click_order = []          # labels in click order
    while remaining:
        matched = False
        for label in labels:
            if remaining == label or remaining.startswith(label + ", "):
                click_order.append(label)
                labels.remove(label)
                remaining = remaining[len(label):]
                if remaining.startswith(", "):
                    remaining = remaining[2:]
                matched = True
                break
        if not matched:
            break
    custom_text = remaining
    return click_order, custom_text
```
- Official options: `[x]` + badge `<sup><sub><kbd>#{rank}</kbd></sub></sup>` if in click_order, else `[ ]` (no badge)
- Rank = 1-based position in click_order
- Custom option checkbox: `[x]` if `custom_text` and **not** `custom_text.endswith("?")`, else `[ ]`

---

## "Other" pseudo-option label rules
| Question has previews? | Custom text given? | Label |
|---|---|---|
| Yes | selected/typed notes | `_Notes:_` (only when `(notes only)` answer) |
| Yes | no notes typed | `_Notes: Add notes on this design._` |
| No | yes (text present) | `_Type something:_` |
| No | no | `_Type something._` |

**Note:** single-select with `annotations[q]["preview"]` → always `_Notes: Add notes on this design._`

---

## Preview code block language tag
Use `` ```text `` for an option's preview iff that option is `options[-1]` (last in the list) **and** it has a preview field. All others: ` ``` ` (no language).

## Preview rendering (inside details)
```python
for line in preview.splitlines():
    if line:
        out.append(f">     {line}\n")   # 5 spaces after >
    else:
        out.append(">\n")
```
Closer: `">     ```\n"` (5 spaces).

---

## Changes to `main()`
```python
# old:
answer = _flatten_answer(payload.get("tool_response"))
block = _render_block(tool_input, answer)

# new:
block = _render_block(tool_input, payload.get("tool_response") or {})
```
`_flatten_answer` can be removed (no longer called).

---

## Verification
```bash
uv run --project scripts/°base python -m unittest scripts/°base/tests/test_save_decision.py -v
```
Must go from `ERROR` (wrong signature) → `OK`.

Also run the full suite to confirm no regressions:
```bash
uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v
```
