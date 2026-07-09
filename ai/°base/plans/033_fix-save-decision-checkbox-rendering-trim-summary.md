# Fix `save-decision` checkbox rendering + trim summary

## Context

`save-decision/hook.py` renders `AskUserQuestion`/`request_user_input` answers into `ai/°base/query.md` as a collapsible `<details>` block. Comparing a real recorded payload (`ai/°base/output/debug/20260708-090957_547762-save-decision.json`) against the current renderer shows two problems:

1. **No `[x]` ever appears in the expandable `<details>` body**, even though the `<summary>` line correctly names the chosen option. Root cause: `_render_block`'s single-select branch intentionally suppresses the checkmark whenever the selected choice has a `preview` block (`selected_has_preview`, added in commit `cd096c30`, comment: "the preview display serves as the visual selection indicator"). In practice this makes the checkbox list look like nothing was ever chosen, since most real single-select questions from `/plan` carry a preview. The reported payload's only question has a preview and picked option, so its checkbox is wrongly `[ ]`.
2. Separately requested cleanup: the `<summary>` currently repeats the chosen answer/preview under each question (`>> N. question` then `- answer label` / preview code block). That's redundant with the `<details>` body and makes the summary too verbose — it should list only the question text.

## Changes

### `scripts/°base/ai/hooks/save-decision/hook.py` — `_render_block`

**Details section (single-select branch, ~line 379-410):** remove the `selected_has_preview` suppression entirely. Checkbox state must always follow `choice.selected`, matching the multi-select branch's `check = "[x]" if choice.selected else "[ ]"` (line 355). Delete the now-dead `selected_has_preview` computation and its comment.

**Summary section (~lines 297-332):** for each question, emit only the numbered question line:
```
>> {i}. {q.question}
```
Remove the selected-label/`_Other_` bullet, the multi-select item list, and the preview code block that currently follow it. Drop the now-unused `indent`/`other`/`items_to_show`/`display` logic from this loop (the `other` lookup and preview rendering are still needed in the Details loop further down — only strip them from the Summary loop).

### Golden spec fixtures (must be updated to match, since tests assert exact string equality)

- `ai/°base/errors/12.expected.md` — `# query.md addition` block:
  - Summary: reduce all 7 question entries to bare `>> N. question text` lines (no answer bullets/preview blocks).
  - Details: question 6 ("Pick a logging approach") option 3 "No logging" currently `[ ]` (has a preview) → must become `[x]` since it's the selected option.
- `ai/°base/errors/15.expected.md` — same summary trim for both `# 1` and `# 2` entries (checkboxes there aren't affected since none of those selected choices carry previews, but double check after the code change).

### Tests

Run the existing suite — no new test files needed, since `test_save_decision.py` and `test_save_decision_15.py` assert against the spec fixtures above and will automatically validate the fix once fixtures are updated:
```
uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v
```
Also spot-check with the actual reported payload via the script's built-in preview mode:
```
uv run --project scripts/°base python scripts/°base/ai/hooks/save-decision/hook.py --preview=20260708-090957_547762-save-decision.json
```
Confirm: the summary line shows only the question text, and the details checklist shows `[x]` on "Before mutating anything (Recommended)".

## Verification
1. Update fixtures, run the full test suite — must pass.
2. Run `--preview` against the originally-reported JSON and visually confirm both fixes.
3. Spot check `test_save_decision_codex.py` still passes unmodified (Codex path shares `_render_block`, no Codex-specific fixtures reference previews so no changes expected there, but the full suite run covers it).
