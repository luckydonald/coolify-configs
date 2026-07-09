#!/usr/bin/env python3
"""PostToolUse hook for Write and ExitPlanMode: snapshot the plan into
``ai/plans/NNN_slug.md`` (or ``ai/°base/plans/...`` inside the base meta-repo)
and commit just that file.

Fires on:
- ``Write`` — when the plan file (~/.claude/plans/*.md) is written during
  plan mode; each distinct version gets its own commit.
- ``ExitPlanMode`` — when the user approves the plan; deduplicated so
  identical content doesn't produce a second commit.

Session tracking (keyed by session_id in a temp-dir state file):
- First write → allocate NNN, create ``NNN_slug.md``, commit.
- Later writes in the same session → reuse NNN; rename file if slug changed;
  new commit each time (no amending).
- ExitPlanMode → same dedup: skip if identical to last committed content.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib import base_ai_commit_subject, dump_debug_payload, is_cross_tool_duplicate, read_payload, resolve_log_path, slugify  # noqa: E402

_STATE_FILE = Path(tempfile.gettempdir()) / "save-plan-state.json"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Plan extraction
# ---------------------------------------------------------------------------

def _plan_from_response(tool_response) -> str:
    """Extract plan text from the ExitPlanMode tool_response dict."""
    if tool_response is None:
        return ""
    if isinstance(tool_response, dict):
        plan = tool_response.get("plan") or ""
        if plan.strip():
            return plan.strip()
        file_path = tool_response.get("filePath") or ""
        if file_path:
            p = Path(file_path)
            if p.is_file():
                return p.read_text(encoding="utf-8").strip()
        return ""
    if isinstance(tool_response, str):
        return tool_response.strip()
    return ""


def _plan_from_write(tool_input: dict) -> str:
    """Extract plan text when the Write/create tool writes the harness's plan
    file: Claude's ``~/.claude/plans/*.md`` or Copilot's
    ``~/.copilot/session-state/<session_id>/plan.md``."""
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not _is_plan_file_path(file_path):
        return ""
    return (tool_input.get("content") or tool_input.get("file_text") or "").strip()


def _plan_from_edit(tool_input: dict) -> str:
    """Extract plan text when the Edit/edit tool patches the harness's plan
    file: Claude's ``~/.claude/plans/*.md`` or Copilot's
    ``~/.copilot/session-state/<session_id>/plan.md``."""
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not _is_plan_file_path(file_path):
        return ""
    try:
        return Path(file_path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _is_plan_file_path(file_path: str) -> bool:
    """True for Claude's ``~/.claude/plans/*.md`` or Copilot's
    ``~/.copilot/session-state/<session_id>/plan.md``."""
    if not file_path:
        return False
    return bool(
        re.search(r"/\.claude/plans/[^/]+\.md$", file_path)
        or re.search(r"/\.copilot/session-state/[^/]+/plan\.md$", file_path)
    )


def _plan_from_copilot_session(payload: dict) -> str:
    """Fallback for Copilot's ``exit_plan_mode`` tool: its ``tool_response``
    doesn't carry the full plan text (that lives in the session's own
    ``plan.md``, already captured by a prior Write/create event). Read it
    directly via the session id when the response has no usable plan."""
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return ""
    plan_path = Path.home() / ".copilot" / "session-state" / session_id / "plan.md"
    if not plan_path.is_file():
        return ""
    return plan_path.read_text(encoding="utf-8").strip()


def _plan_from_codex_stop(payload: dict) -> str:
    """Extract the final proposed plan from a Codex Stop hook payload."""
    if payload.get("hook_event_name") not in {"Stop", "stop"}:
        return ""
    text = payload.get("last_assistant_message") or ""
    m = re.search(r"<proposed_plan>\s*(.*?)\s*</proposed_plan>", text, re.S)
    if not m:
        return ""
    return m.group(1).strip()


def _codex_session_files(session_id: str) -> list[Path]:
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.is_dir():
        return []
    if session_id:
        files = list(sessions_dir.rglob(f"*{session_id}.jsonl"))
        if files:
            return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
        return []
    return sorted(sessions_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]


def _text_from_codex_message_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _proposed_plan_from_text(text: str) -> str:
    m = re.search(r"<proposed_plan>\s*(.*?)\s*</proposed_plan>", text or "", re.S)
    return m.group(1).strip() if m else ""


def _plan_from_codex_transcript(session_id: str) -> str:
    """Extract the latest completed Codex Plan item from the session transcript."""
    latest_final_message = ""
    for path in _codex_session_files(session_id):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload") if isinstance(record, dict) else None
            if not isinstance(payload, dict):
                continue

            if payload.get("type") == "item_completed":
                item = payload.get("item")
                if isinstance(item, dict) and item.get("type") == "Plan":
                    text = item.get("text") or ""
                    if text.strip():
                        return text.strip()

            if not latest_final_message and record.get("type") == "response_item":
                if payload.get("type") == "message" and payload.get("role") == "assistant":
                    latest_final_message = _text_from_codex_message_content(payload.get("content"))

        plan = _proposed_plan_from_text(latest_final_message)
        if plan:
            return plan
    return ""


def _latest_query_entry(text: str) -> str:
    marker = re.compile(r"(?m)^[›❯⩼] ")
    matches = list(marker.finditer(text))
    if not matches:
        return ""
    entry = text[matches[-1].start():].strip()
    return entry[2:].strip()


def _plan_from_codex_query_log() -> str:
    """Fallback for forwarded-plan prompts logged before the Codex Stop hook runs."""
    log_path = resolve_log_path("ai/query.md", "ai/°base/query.md")
    if not log_path.is_file():
        return ""
    entry = _latest_query_entry(log_path.read_text(encoding="utf-8"))
    if "A previous agent produced the plan below" not in entry:
        return ""
    m = re.search(r"(?m)^# .*\Z", entry, re.S)
    return m.group(0).strip() if m else ""


def _plan_from_codex_sources(payload: dict) -> str:
    if payload.get("hook_event_name") not in {"Stop", "stop"}:
        return ""
    return (
        _plan_from_codex_transcript(str(payload.get("session_id") or ""))
        or _plan_from_codex_stop(payload)
        or _plan_from_codex_query_log()
    )


# ---------------------------------------------------------------------------
# Prefix helpers
# ---------------------------------------------------------------------------
# Todo capture
# ---------------------------------------------------------------------------

_TODO_CHECKBOXES = {
    "done": "[x]",
    "completed": "[x]",
    "in_progress": "[ ]",
    "pending": "[ ]",
    "blocked": "[ ]",
}
_TODO_SUFFIXES = {
    "in_progress": " *(in progress)*",
    "blocked": " *(blocked)*",
}
_TODOS_HEADING = "## Todos"


def _normalize_todos(tool_input: dict) -> list[dict]:
    """Normalize a TodoWrite (Claude) / update_todo (Copilot) tool_input into
    a flat list of ``{"text": str, "status": str}`` items.

    Claude's TodoWrite: ``todos: [{content, status, activeForm}]``.
    Copilot's update_todo schema isn't pinned down by the docs at
    implementation time, so several plausible field names are accepted
    defensively (``items``/``todo_list`` as list containers; ``title``/
    ``task``/``description`` as text fields).
    """
    raw = (
        tool_input.get("todos")
        or tool_input.get("items")
        or tool_input.get("todo_list")
        or []
    )
    if not isinstance(raw, list):
        return []
    todos: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = (
            item.get("content")
            or item.get("title")
            or item.get("task")
            or item.get("description")
            or ""
        ).strip()
        if not text:
            continue
        status = str(item.get("status") or "pending").strip().lower()
        todos.append({"text": text, "status": status})
    return todos


def _render_todos_markdown(todos: list[dict]) -> str:
    """Render normalized todos into a ``## Todos`` markdown section (no
    trailing section separator). Returns "" when there are no todos."""
    if not todos:
        return ""
    lines = [_TODOS_HEADING, ""]
    for item in todos:
        status = item["status"]
        checkbox = _TODO_CHECKBOXES.get(status, "[ ]")
        suffix = _TODO_SUFFIXES.get(status, "")
        lines.append(f"- {checkbox} {item['text']}{suffix}")
    return "\n".join(lines)


def _apply_todos_section(plan_text: str, todos_md: str) -> str:
    """Idempotently replace-or-append the ``## Todos`` section in a saved plan
    snapshot: replaces an existing section (heading through the next
    top-level ``## `` heading or EOF), or appends one at the end."""
    plan_text = plan_text.rstrip("\n")
    pattern = re.compile(
        rf"(?ms)^{re.escape(_TODOS_HEADING)}\s*$.*?(?=^## |\Z)"
    )
    if pattern.search(plan_text):
        new_text = pattern.sub(lambda _m: todos_md + "\n", plan_text, count=1)
    else:
        new_text = plan_text + "\n\n" + todos_md + "\n"
    return new_text.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------

def _next_prefix(plans_dir: Path) -> str:
    highest = 0
    for entry in plans_dir.glob("[0-9]*_*.md"):
        m = re.match(r"^(\d+)_", entry.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{highest + 1:03d}"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _commit(paths: list[str], msg: str) -> None:
    for p in paths:
        if Path(p).exists():
            subprocess.run(["git", "add", "--", p], capture_output=True)
        # Deleted paths are already staged by _git_rm; no add needed.
    msg = base_ai_commit_subject(msg)
    subprocess.run(["git", "commit", "--no-verify", "--only", *paths, "-m", msg], capture_output=True)


def _git_rm(path: str) -> None:
    subprocess.run(["git", "rm", "--force", "--", path], capture_output=True)


# ---------------------------------------------------------------------------
# Todo capture handler
# ---------------------------------------------------------------------------

def _apply_todos_and_commit(session_id: str, todos: list[dict]) -> int:
    """Shared tail end of todo capture: inject `todos` into the session's
    already-saved plan snapshot (if any) as a ``## Todos`` section, and
    commit. Silently no-ops when there's no session, no saved plan snapshot
    yet, or no todos to render -- this is a best-effort enrichment, not a
    plan-creation path.

    Commit message is `ai: Todo added` the first time the section is
    created, `ai: Todo updated` on every later change.
    """
    if not session_id:
        return 0
    state = _load_state()
    session = state.get(session_id)
    if not session:
        return 0
    relpath = session.get("relpath")
    if not relpath:
        return 0
    plan_path = Path(relpath)
    if not plan_path.is_file():
        return 0

    todos_md = _render_todos_markdown(todos)
    if not todos_md:
        return 0

    current = plan_path.read_text(encoding="utf-8")
    heading_existed = bool(re.search(rf"(?ms)^{re.escape(_TODOS_HEADING)}\s*$", current))
    updated = _apply_todos_section(current, todos_md)
    if updated == current:
        return 0

    plan_path.write_text(updated, encoding="utf-8")
    _commit([relpath], "ai: Todo updated" if heading_existed else "ai: Todo added")
    return 0


def _handle_todo_capture(session_id: str, tool_input: dict) -> int:
    """On TodoWrite/update_todo, inject the current (complete) todo list
    into the session's saved plan snapshot."""
    return _apply_todos_and_commit(session_id, _normalize_todos(tool_input))


def _handle_task_tool_capture(
    session_id: str, tool_name: str, tool_input: dict, tool_response: dict
) -> int:
    """On TaskCreate/TaskUpdate, accumulate per-session task state.

    Unlike TodoWrite/update_todo (which resend the *entire* current list
    every call), TaskCreate/TaskUpdate each mutate a single task -- so the
    full list has to be tracked across calls in the session state file
    (`tasks: {taskId: {"text": str, "status": str}}`), keyed the same way
    `prefix`/`relpath` already are.
    """
    if not session_id:
        return 0
    state = _load_state()
    session = state.setdefault(session_id, {})
    tasks: dict = session.setdefault("tasks", {})

    if tool_name == "TaskCreate":
        task_id = str((tool_response.get("task") or {}).get("id") or "")
        if not task_id:
            return 0
        text = tool_input.get("subject") or (tool_response.get("task") or {}).get("subject") or ""
        tasks[task_id] = {"text": text, "status": "pending"}
    else:  # TaskUpdate
        task_id = str(tool_input.get("taskId") or tool_response.get("taskId") or "")
        if not task_id:
            return 0
        if tool_input.get("status") == "deleted":
            tasks.pop(task_id, None)
        else:
            entry = tasks.setdefault(task_id, {"text": "", "status": "pending"})
            if tool_input.get("subject"):
                entry["text"] = tool_input["subject"]
            if tool_input.get("status"):
                entry["status"] = tool_input["status"]

    _save_state(state)
    return _apply_todos_and_commit(session_id, list(tasks.values()))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ai_tool = sys.argv[1] if len(sys.argv) > 1 else "claude"
    payload = read_payload()
    if is_cross_tool_duplicate(ai_tool):
        return 0
    dump_debug_payload(payload, "save-plan")
    session_id = payload.get("session_id", "")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool_name in ("TodoWrite", "update_todo"):
        return _handle_todo_capture(session_id, tool_input)
    if tool_name in ("TaskCreate", "TaskUpdate"):
        tool_response = payload.get("tool_response") or {}
        return _handle_task_tool_capture(session_id, tool_name, tool_input, tool_response)

    plan = ""
    if ai_tool == "codex":
        plan = _plan_from_codex_sources(payload)
    elif tool_name in ("Write", "create"):
        plan = _plan_from_write(tool_input)
    elif tool_name in ("Edit", "edit"):
        plan = _plan_from_edit(tool_input)
    elif tool_name in ("ExitPlanMode", "exit_plan_mode"):
        plan = (tool_input.get("plan") or "").strip()
        if not plan:
            plan = _plan_from_response(payload.get("tool_response"))
        if not plan:
            plan = _plan_from_copilot_session(payload)
    # Stop for Claude: no plan extraction — Write/ExitPlanMode already handled it.

    if not plan:
        return 0

    sentinel = resolve_log_path("ai/plans/.dir", "ai/°base/plans/.dir")
    plans_dir = sentinel.parent
    plans_dir.mkdir(parents=True, exist_ok=True)

    state = _load_state()
    session = state.get(session_id) if session_id else None
    # session shape: {"prefix": "004", "relpath": "ai/°base/plans/004_slug.md",
    #                 "source": "sprightly-mixing-iverson.md", "done": bool,
    #                 "tasks": {taskId: {"text": str, "status": str}}}
    # `tasks` may exist without `prefix`/`relpath` yet (TaskCreate/TaskUpdate
    # firing before any plan has been saved this session) -- only a `prefix`
    # means there's an actual plan snapshot to treat as "the session".
    if session and not session.get("prefix"):
        session = None

    # When Write fires after ExitPlanMode committed the previous plan (same session),
    # treat it as a brand-new plan and allocate a fresh prefix.
    if session and tool_name in ("Write", "create") and session.get("done"):
        session = None

    new_slug = slugify(plan, fallback="plan")

    if session:
        prefix = session["prefix"]
        old_relpath = session["relpath"]
        old_path = Path(old_relpath)

        # Skip identical content.
        if old_path.is_file() and old_path.read_text(encoding="utf-8").strip() == plan:
            if tool_name in ("ExitPlanMode", "exit_plan_mode") and session_id and session_id in state:
                state[session_id]["done"] = True
                _save_state(state)
            return 0

        new_relpath = str((plans_dir / f"{prefix}_{new_slug}.md").relative_to(Path.cwd()))
        new_path = plans_dir / f"{prefix}_{new_slug}.md"
        body = plan if plan.endswith("\n") else plan + "\n"

        if old_relpath != new_relpath:
            # Slug changed → rename: remove old, write new, commit both paths.
            _git_rm(old_relpath)
            new_path.write_text(body, encoding="utf-8")
            _commit([old_relpath, new_relpath], f"ai: save plan {prefix}_{new_slug}")
        else:
            # Same filename, updated content.
            new_path.write_text(body, encoding="utf-8")
            _commit([new_relpath], f"ai: save plan {prefix}_{new_slug}")

        session["relpath"] = new_relpath
        _save_state(state)

    else:
        # New session: allocate NNN and create the file.
        prefix = _next_prefix(plans_dir)
        out_path = plans_dir / f"{prefix}_{new_slug}.md"
        relpath = str(out_path.relative_to(Path.cwd()))
        body = plan if plan.endswith("\n") else plan + "\n"
        out_path.write_text(body, encoding="utf-8")
        _commit([relpath], f"ai: save plan {prefix}_{new_slug}")

        if session_id:
            # Capture the harness plan filename as metadata. `.update()`
            # (not a plain assignment) preserves a `tasks` map that
            # TaskCreate/TaskUpdate may have already started accumulating
            # for this session_id before any plan was saved.
            source = Path(tool_input.get("file_path") or tool_input.get("path") or "").name
            state.setdefault(session_id, {}).update(
                {"prefix": prefix, "relpath": relpath, "source": source}
            )
            _save_state(state)

    # After ExitPlanMode fires, mark this plan session done so that a subsequent
    # Write (new /plan command in the same Claude session) allocates a fresh prefix.
    if tool_name in ("ExitPlanMode", "exit_plan_mode") and session_id and session_id in state:
        state[session_id]["done"] = True
        _save_state(state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
