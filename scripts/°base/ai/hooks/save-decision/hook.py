#!/usr/bin/env python3
"""PostToolUse hook for AskUserQuestion / request_user_input: append the asked
question(s) and the picked answer to ai/query.md as a markdown blockquote, then commit.

Usage:
  hook.py [ai_tool_name]           normal hook mode (reads payload from stdin)
  hook.py --preview=<file>         render a saved payload JSON to stdout for quick inspection
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel, StrictBool, StrictInt, computed_field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib import append_and_commit, dump_debug_payload, is_cross_tool_duplicate, read_payload, resolve_log_path, slugify  # noqa: E402


class Choice(BaseModel):
    label: str = ""
    description: str = ""
    preview: str = ""
    selection: StrictBool | StrictInt = False
    # False       → not selected
    # True        → selected, order unknown (single-select)
    # 1, 2, 3 …  → selected, 1-based click order (multi-select)
    # bool(selection) is always the correct "is selected" check
    note: str = ""          # free text / annotation note — Other entry only
    is_other: bool = False  # True for the parser-injected Other/Notes row

    @computed_field
    @property
    def selected(self) -> bool:
        return bool(self.selection)


class Question(BaseModel):
    question: str
    header: str = ""
    multi_select: bool = False
    choices: list[Choice]  # predefined options + one parser-injected Other at end

    @computed_field
    @property
    def selected(self) -> list[Choice]:
        return sorted(
            (c for c in self.choices if c.selected),
            key=lambda c: c.selection,
        )

    @computed_field
    @property
    def timed_out(self) -> bool:
        return not self.selected


Choice.model_rebuild()
Question.model_rebuild()


def _greedy_match(answer: str, labels: list[str]) -> tuple[list[str], str]:
    """Greedy left-to-right match of labels against the answer string.

    Returns (click_order, custom_text) where custom_text is the leftover after
    all matched labels are consumed.
    """
    available = list(labels)
    remaining = answer
    click_order: list[str] = []
    while remaining:
        matched = False
        for label in available:
            if not label:
                continue
            if remaining == label or remaining.startswith(label + ", "):
                click_order.append(label)
                available.remove(label)
                remaining = remaining[len(label):]
                if remaining.startswith(", "):
                    remaining = remaining[2:]
                matched = True
                break
        if not matched:
            break
    return click_order, remaining


def _parse_claude(payload: dict) -> list[Question]:
    """Parse a Claude Code AskUserQuestion payload into a list of Questions."""
    tool_input = payload.get("tool_input") or {}
    if not tool_input and isinstance(payload.get("questions"), list):
        tool_input = {"questions": payload["questions"]}
    raw_questions = tool_input.get("questions") or []

    raw_response = payload.get("tool_response") or {}
    if isinstance(raw_response, str):
        try:
            raw_response = json.loads(raw_response)
        except (json.JSONDecodeError, ValueError):
            raw_response = {}
    if not isinstance(raw_response, dict):
        raw_response = {}

    raw_answers: dict = raw_response.get("answers") or {}
    raw_annotations: dict = raw_response.get("annotations") or {}

    questions: list[Question] = []
    for q in raw_questions:
        if not isinstance(q, dict):
            continue
        qtext = q.get("question", "")
        raw_opts = [o for o in (q.get("options") or []) if isinstance(o, dict)]
        multi = bool(q.get("multiSelect", False))
        answer_str = raw_answers.get(qtext, "")
        raw_ann = raw_annotations.get(qtext) or {}
        ann_notes = raw_ann.get("notes", "")
        notes_only = answer_str == "(notes only)"

        if multi:
            labels = [o.get("label", "") for o in raw_opts]
            click_order, custom_text = _greedy_match(answer_str, labels)
            rank_map = {label: rank for rank, label in enumerate(click_order, 1)}
            choices: list[Choice] = [
                Choice(
                    label=o.get("label", ""),
                    description=o.get("description", ""),
                    preview=o.get("preview", ""),
                    selection=rank_map.get(o.get("label", ""), False),
                )
                for o in raw_opts
            ]
            other_sel: StrictBool | StrictInt = True if custom_text else False
            choices.append(Choice(is_other=True, selection=other_sel, note=custom_text))
        else:
            # "direct other": answer doesn't match any label and isn't the notes-only
            # sentinel → the raw answer string is the text the user typed in the Other field.
            label_set = {o.get("label", "") for o in raw_opts}
            direct_other = bool(answer_str) and not notes_only and answer_str not in label_set
            choices = [
                Choice(
                    label=o.get("label", ""),
                    description=o.get("description", ""),
                    preview=o.get("preview", ""),
                    selection=not notes_only and not direct_other and o.get("label", "") == answer_str,
                )
                for o in raw_opts
            ]
            other_sel = notes_only or direct_other
            other_note = ann_notes if notes_only else (answer_str if direct_other else "")
            choices.append(Choice(
                is_other=True,
                selection=other_sel,
                note=other_note,
            ))

        questions.append(Question(
            question=qtext,
            header=q.get("header", ""),
            multi_select=multi,
            choices=choices,
        ))
    return questions


def _parse_codex(payload: dict) -> list[Question]:
    """Parse a Codex request_user_input payload into a list of Questions."""
    tool_input = payload.get("tool_input") or {}
    raw_questions = tool_input.get("questions") or []

    raw_response = payload.get("tool_response") or "{}"
    if isinstance(raw_response, str):
        try:
            codex_answers: dict = json.loads(raw_response).get("answers") or {}
        except (json.JSONDecodeError, ValueError):
            codex_answers = {}
    elif isinstance(raw_response, dict):
        codex_answers = raw_response.get("answers") or {}
    else:
        codex_answers = {}

    questions: list[Question] = []
    for q in raw_questions:
        if not isinstance(q, dict):
            continue
        qid = q.get("id", "")
        qtext = q.get("question", "")
        raw_opts = [o for o in (q.get("options") or []) if isinstance(o, dict)]
        multi = bool(q.get("multiSelect", False))

        raw_ans = codex_answers.get(qid)  # None when key absent (timeout)
        if raw_ans is None:
            items: list[str] | None = None
        else:
            items = raw_ans.get("answers") or [] if isinstance(raw_ans, dict) else []

        if items is None:
            notes = ""
            selected_labels: list[str] = []
            none_of_above = False
        else:
            notes = "; ".join(
                i[len("user_note: "):]
                for i in items
                if isinstance(i, str) and i.startswith("user_note: ")
            )
            none_of_above = "None of the above" in items
            selected_labels = [
                i for i in items
                if isinstance(i, str)
                and not i.startswith("user_note: ")
                and i != "None of the above"
            ]

        if multi:
            rank_map = {label: rank for rank, label in enumerate(selected_labels, 1)}
            choices: list[Choice] = [
                Choice(
                    label=o.get("label", ""),
                    description=o.get("description", ""),
                    selection=rank_map.get(o.get("label", ""), False),
                )
                for o in raw_opts
            ]
            if items is not None and (notes or none_of_above):
                other_sel: StrictBool | StrictInt = True
                other_note = notes
            else:
                other_sel = False
                other_note = ""
            choices.append(Choice(is_other=True, selection=other_sel, note=other_note))
        else:
            # For Codex single-select: when a predefined label is selected and a note
            # is present, the note belongs on that choice (not on Other). Other is only
            # selected for "None of the above" or when no predefined choice was picked.
            has_predefined_selection = bool(selected_labels)
            choices = [
                Choice(
                    label=o.get("label", ""),
                    description=o.get("description", ""),
                    selection=o.get("label", "") in selected_labels,
                    note=(
                        notes
                        if (o.get("label", "") in selected_labels and not none_of_above)
                        else ""
                    ),
                )
                for o in raw_opts
            ]
            other_selected = items is not None and (
                none_of_above or (not has_predefined_selection and bool(notes))
            )
            other_note = notes if (none_of_above or not has_predefined_selection) else ""
            choices.append(Choice(
                is_other=True,
                selection=other_selected,
                note=other_note,
            ))

        questions.append(Question(
            question=qtext,
            header=q.get("header", ""),
            multi_select=multi,
            choices=choices,
        ))
    return questions


def _parse_copilot(payload: dict) -> list[Question]:
    """Parse a Copilot CLI ask_user payload into a list of Questions.

    Copilot's ask_user schema is much simpler than Claude's AskUserQuestion:
    a single question, a flat list of choice strings (no label/description/
    preview), and an allow_freeform flag instead of multiSelect. There is
    exactly one question per call.

    Two things confirmed only from real hook payloads (not the published
    docs) and easy to get wrong:
    - `tool_name` is *always* reported as the Claude-mapped name
      (`AskUserQuestion`), never the literal `ask_user` runtime name — per
      hooks-reference.md's runtime→Claude tool name table, PostToolUse
      payloads always use the Claude name. So this parser must be selected
      via the CLI `ai_tool` argv (or a payload-shape heuristic), not by
      checking `tool_name == "ask_user"`.
    - The answer lives in `tool_result.text_result_for_llm` (a human-readable
      string), not a Claude-style `tool_response` dict. It is prefixed with
      "User selected: <label>" when a listed choice was picked, or
      "User responded: <text>" for a freeform/no-match answer; empty/absent
      when the question timed out unanswered.
    """
    tool_input = payload.get("tool_input") or {}
    qtext = tool_input.get("question", "")
    raw_choices = [c for c in (tool_input.get("choices") or []) if isinstance(c, str)]
    allow_freeform = bool(tool_input.get("allow_freeform", True))

    tool_result = payload.get("tool_result")
    text_result = tool_result.get("text_result_for_llm", "") if isinstance(tool_result, dict) else ""
    if not text_result:
        # Backward/test compatibility with the originally-assumed
        # `tool_response` shape (a JSON object/string carrying "answer").
        raw_response = payload.get("tool_response") or ""
        if isinstance(raw_response, str):
            try:
                parsed = json.loads(raw_response)
            except (json.JSONDecodeError, ValueError):
                parsed = raw_response
        else:
            parsed = raw_response
        text_result = (
            parsed.get("answer", "") if isinstance(parsed, dict)
            else parsed if isinstance(parsed, str)
            else ""
        )

    if text_result.startswith("User selected: "):
        answer = text_result[len("User selected: "):]
        direct_other = False
    elif text_result.startswith("User responded: "):
        answer = text_result[len("User responded: "):]
        direct_other = True
    else:
        answer = text_result
        direct_other = allow_freeform and bool(answer) and answer not in raw_choices

    choices: list[Choice] = [
        Choice(
            label=label,
            selection=not direct_other and label == answer,
        )
        for label in raw_choices
    ]
    other_note = answer if direct_other else ""
    choices.append(Choice(is_other=True, selection=direct_other, note=other_note))

    return [Question(
        question=qtext,
        header="",
        multi_select=False,
        choices=choices,
    )]


def _looks_like_copilot_payload(payload: dict) -> bool:
    """Shape-based fallback detector for Copilot's flat ask_user schema, used
    when no CLI `ai_tool` argv is available to disambiguate (e.g. `--preview`
    mode or direct/manual payload testing). Copilot's `tool_input` has a
    singular `question` string and never Claude's nested `questions` list."""
    tool_input = payload.get("tool_input")
    return isinstance(tool_input, dict) and "question" in tool_input and "questions" not in tool_input


def parse_payload(payload: dict, ai_tool: str | None = None) -> list[Question]:
    """Dispatch to the correct parser.

    Prefers the CLI `ai_tool` argv (passed by every generated hook config) as
    the source of truth, since Copilot's PostToolUse payload always reports
    `tool_name` as the Claude-mapped name (`AskUserQuestion`), making
    `tool_name` alone insufficient to detect a Copilot `ask_user` call. Falls
    back to `tool_name`/payload-shape inference when no CLI arg is given
    (`--preview` mode, tests, or manual invocations).
    """
    if ai_tool == "copilot":
        return _parse_copilot(payload)
    if ai_tool == "codex":
        return _parse_codex(payload)
    if ai_tool == "claude":
        return _parse_claude(payload)

    tool_name = payload.get("tool_name")
    if tool_name == "request_user_input":
        return _parse_codex(payload)
    if tool_name == "ask_user" or _looks_like_copilot_payload(payload):
        return _parse_copilot(payload)
    return _parse_claude(payload)


def _render_preview_block(preview: str, lang: str, out: list[str]) -> None:
    """Append a fenced code block for a preview field inside a blockquote list."""
    out.append(f">   - ```{lang}\n")
    for line in preview.splitlines():
        if line:
            out.append(f">     {line}\n")
        else:
            out.append(">\n")
    out.append(">     ```\n")


def _render_block(questions: list[Question], *, tool: str = "claude") -> str:
    total = len(questions)
    out: list[str] = []
    glyph = {"claude": "❯", "codex": "›", "copilot": "◆"}.get(tool, "❯")
    out.append(f"{glyph} Question answered.\n")
    out.append("> <details><summary>\n")
    out.append(">\n")

    # --- Summary ---
    for i, q in enumerate(questions, 1):
        out.append(f">> {i}. {q.question}\n")

    out.append(">\n")
    out.append("> (click to expand)\n")
    out.append(">\n")
    out.append("> </summary>\n")
    out.append(">\n")

    # --- Details ---
    for i, q in enumerate(questions, 1):
        if i > 1:
            out.append(">\n")

        pred_choices = [c for c in q.choices if not c.is_other]
        other = next((c for c in q.choices if c.is_other), None)
        has_any_preview = any(c.preview for c in pred_choices)
        last_pred_idx = len(pred_choices) - 1

        select_type = "Multi Select" if q.multi_select else "Single Select"
        out.append(f">> **{q.header}** ({i}/{total}) <kbd>{select_type}</kbd><br>\n")
        out.append(f">> {q.question}\n")

        if q.multi_select:
            for n, choice in enumerate(pred_choices, 1):
                check = "[x]" if choice.selected else "[ ]"
                rank_badge = (
                    f" <sup><sub><kbd>#{choice.selection}</kbd></sub></sup>"
                    if isinstance(choice.selection, int) and not isinstance(choice.selection, bool)
                    else ""
                )
                out.append(f"> - {check} {n}\\. {choice.label}{rank_badge}\n")
                if choice.description:
                    out.append(f">   - _{choice.description}_\n")
                if choice.preview:
                    lang = "text" if (n - 1) == last_pred_idx else ""
                    _render_preview_block(choice.preview, lang, out)

            other_n = len(pred_choices) + 1
            if other and other.note:
                other_check = "[ ]" if other.note.endswith("?") else "[x]"
                other_label = "_Type something:_"
            else:
                other_check = "[ ]"
                other_label = "_Type something._"
            out.append(f"> - {other_check} {other_n}\\. {other_label}\n")
            if other and other.note:
                out.append(f">   - > {other.note}\n")

        else:
            for n, choice in enumerate(pred_choices, 1):
                check = "[x]" if choice.selected else "[ ]"
                out.append(f"> - {check} {n}\\. {choice.label}\n")
                if choice.description:
                    out.append(f">   - _{choice.description}_\n")
                if choice.note:
                    out.append(f">   - > {choice.note}\n")
                if choice.preview:
                    lang = "text" if (n - 1) == last_pred_idx else ""
                    _render_preview_block(choice.preview, lang, out)

            other_n = len(pred_choices) + 1
            if other and other.selected and other.note:
                other_check = "[x]"
                other_label = "_Notes:_" if has_any_preview else "_Type something:_"
                other_text = other.note
            elif has_any_preview:
                other_check = "[ ]"
                other_label = "_Notes: Add notes on this design._"
                other_text = ""
            else:
                other_check = "[ ]"
                other_label = "_Type something._"
                other_text = ""
            out.append(f"> - {other_check} {other_n}\\. {other_label}\n")
            if other_text:
                out.append(f">   - > {other_text}\n")

    out.append(">\n")
    out.append("> </details>\n")
    out.append(">\n")
    out.append("\n")
    return "".join(out)


def _find_repo_root(start: Path) -> Path | None:
    p = start.resolve()
    while True:
        if (p / ".git").exists():
            return p
        parent = p.parent
        if parent == p:
            return None
        p = parent


def _resolve_preview_path(value: str, script_dir: Path, cwd: Path) -> Path:
    """Resolve --preview=VALUE to an existing file path.

    Tries (in order):
      1. Absolute path as-is; if missing, retry as repo-root-relative (strip leading /).
      2. Bare filename (no /): ai/°base/output/debug/, then ai/output/debug/.
      3. Relative path: cwd, repo root, script dir.
    """
    repo_root = _find_repo_root(cwd)
    p = Path(value)

    if p.is_absolute():
        if p.exists():
            return p
        if repo_root is not None:
            candidate = repo_root / str(p).lstrip("/")
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"--preview: file not found: {value!r}")

    if "/" not in value and "\\" not in value:
        if repo_root is not None:
            for debug_dir in (
                repo_root / "ai" / "°base" / "output" / "debug",
                repo_root / "ai" / "output" / "debug",
            ):
                candidate = debug_dir / value
                if candidate.exists():
                    return candidate

    candidates: list[Path] = [cwd / p]
    if repo_root is not None and repo_root.resolve() != cwd.resolve():
        candidates.append(repo_root / p)
    candidates.append(script_dir / p)

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved

    raise FileNotFoundError(
        f"--preview: {value!r} not found "
        f"(cwd={cwd}, repo={repo_root}, script={script_dir})"
    )


def _infer_tool(payload: dict, ai_tool: str) -> str:
    """Resolve the rendering/parsing tool identity: prefer the CLI `ai_tool`
    argv (passed by every generated hook config) since Copilot's PostToolUse
    payload always reports `tool_name` as the Claude-mapped name
    (`AskUserQuestion`), never the literal `ask_user` runtime name. Falls
    back to `tool_name`/payload-shape inference when no CLI arg is given."""
    if ai_tool in ("claude", "codex", "copilot"):
        return ai_tool
    tool_name = payload.get("tool_name")
    if tool_name == "request_user_input":
        return "codex"
    if tool_name == "ask_user" or _looks_like_copilot_payload(payload):
        return "copilot"
    return "claude"


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("tool_name", nargs="?", default="unknown")
    parser.add_argument("--preview", metavar="FILE", default=None)
    args, _ = parser.parse_known_args()

    if args.preview is not None:
        path = _resolve_preview_path(
            args.preview,
            script_dir=Path(__file__).resolve().parent,
            cwd=Path.cwd(),
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        tool = _infer_tool(payload, args.tool_name)
        questions = parse_payload(payload, ai_tool=tool)
        if not questions:
            print("(no questions parsed)", file=sys.stderr)
            return 1
        sys.stdout.write(_render_block(questions, tool=tool))
        return 0

    payload = read_payload()
    if is_cross_tool_duplicate(args.tool_name):
        return 0
    dump_debug_payload(payload, "save-decision")

    tool = _infer_tool(payload, args.tool_name)
    questions = parse_payload(payload, ai_tool=tool)
    if not questions:
        return 0

    block = _render_block(questions, tool=tool)
    slug = slugify(questions[0].question, fallback="decision")

    log_path = resolve_log_path("ai/query.md", "ai/°base/query.md")
    append_and_commit(
        log_path,
        block,
        commit_template_relpath="ai/commit-templates/decision.md",
        default_commit_msg=f"ai: save decision {slug}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
