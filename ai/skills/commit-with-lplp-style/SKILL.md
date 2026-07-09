---
name: "commit-with-lplp-style"
description: "Activates the lplp-pipbuck commit style for the current session. Commits after every completed task, amends nearby `ai:` prompt/decision auto-commits into the work commit, preserves plan commits as separate history, writes messages via ai/git/pending-commit.md, and never commits unrelated files. Use when the user opts in to this style at session start, or explicitly asks to enable it."
---

# lplp Commit Style

Adopt these rules for every commit made this session:

1. **Commit after every completed task.** Never leave work uncommitted.

2. **Check the last 2 commits before committing:**
   - Last message matches one of the auto-commit patterns below → **amend** it.
   - Otherwise → create a new commit.
   - If more than one chained past commit is an auto-commit, include those too.

   Auto-commit patterns — fold into the preceding code commit **by default**:
   - `ai: updated prompt` — user prompt saved to `ai/query.md`
   - `ai: save decision <slug>` — resolved `AskUserQuestion`
   - `ai: agent <id> results` — subagent result record
   - `ai: record memory <slug>` — memory file written (e.g. `ai: record memory MEMORY`, `ai: record memory feedback_commit_amend_over_reset`)
   - `ai: save plan <NNN>_<slug>` — plan files

   **When to keep a commit separate instead of folding:**
   - A plan commit whose plan file content is a genuine, substantive change over the plan it follows — cutting a new version — stays its own commit. Rename it away from the raw hook message per the plan-commit format below.
   - A plan commit is followed by one or more further plan-file revisions for the *same* plan number — folding any of them would erase the revision history. Keep each version as its own commit (first one `ai: Plan:`, each later one `ai: Plan update:`).
   - A plan-save commit whose plan file content is **byte-identical** (or near-identical, no real change) to the plan it follows is not a new version — it did not "change" anything — fold it into its implementation like any other lone auto-commit.
   - Several plan-save commits landing within seconds of each other, with **non-overlapping** edits (e.g. drafting different sections back-to-back rather than revising the same content) — squash those into a single plan commit. They're one drafting burst, not distinct versions; only genuinely separate revision passes (later editing, e.g. after review or a test run surfaced something) earn their own `ai: Plan update:`.
   - A prompt commit (`ai: updated prompt`) represents a clearly new or unrelated topic — it started a different task, not a continuation of the preceding code commit.
   - When in doubt, fold. The goal is readable preserved history, not having every auto-save as separate commit.

   **Naming a plan-only commit that is kept separate:** raw hook messages like `ai: save plan <NNN>_<slug>` are placeholders, not final history — rename them to match the summary style used for real work, but with `ai: Plan:` (first version) or `ai: Plan update:` (each later revision of the same plan number) in place of `ai: Run:`:
   ```md
   [where] component-or-topic: ai: Plan: <short one-line summary of what the plan proposes><sentence-separator>
   ```
   ```md
   [where] component-or-topic: ai: Plan update: <short one-line summary of what changed in this revision><sentence-separator>
   ```
   Leaving the bare `ai: save plan <NNN>_<slug>`/`ai: Plan …` hook message on a commit that's staying in history is not wanted — always rename it once it's confirmed to be a real, kept version cut.

   The plan's summary line should read as basically the same line as the `ai: Run:` commit that eventually implements it, just in **current/imperative tense instead of past tense** — e.g. plan says `ai: Plan: Fix \`get-base.py\` auto mode failing on a fresh repo...`, the implementation says `ai: Run: Fixed \`get-base.py\` auto mode failing on a fresh repo...`. Don't invent a differently-worded plan summary; write the eventual Run summary first (even if only in your head) and de-conjugate it.

   If a `git reset --soft HEAD~N` accidentally included commits that should stay separate, restore them with `git reset --soft <original-hash>` before committing.
   Prefer `--amend` over `HEAD~1`.

3. **Always write the message to `ai/git/pending-commit.md` first** like this:
   1. run exactly the whitelisted command `rm ai/git/pending-commit.md || echo 'was gone'`, which makes sure it's not gonna cause "stale unread file" issues.
   2. Write to `ai/git/pending-commit.md` using the preferred Built-in/MCP tool.
   3. Pass it to the commit with `-F ai/git/pending-commit.md`. Never inline the message in the command, to avoid the need for user confirmations.

4. **Message format:**
   ```md
   [where] component-or-topic: ai: Run: <short one-line summary><sentence-separator>

   <multiline body: what changed, why, key decisions>
   ```
   Where examples: `[.idea]`, `[git]` (gitignore etc.), `[github]` (workflows, issue templates, …), `[frontend]`, `[db]`, `[stdb]` (spacetimedb), `[api]`, `[backend]`, `[docker]`, `[coolify]`, `[infra]`, …
   The word or phrase after `[where]` is a component, feature, subsystem, or topic, not a Conventional Commit type. Do not use `feat`, `fix`, `chore`, `docs`, `test`, or `refactor` there unless that word is literally the component or topic being changed.
   Good examples:
   - `[frontend] admin: ai: Run: Implemented user deletion UI.`
   - `[backend] models: ai: Run: Added models for cool feature.`
   - <code>[backend] cool feature: ai: Run: Added the `cool` model.</code>
   - `[git] ignore rules: ai: Run: Ignored generated cache files.`
   Bad examples:
   - `[frontend] fix: ai: Run: Implement user deletion UI`
   - `[backend] feat: ai: Run: Added cool feature models`
   End every commit summary with a sentence separator: `.`, `:`, `,`, `!`, or `?`.
   Usually use `.` when the summary stands on its own and the body only adds context. Use `:` when the subject needs the body/details that follow to complete the thought.
   Both summary and body may contain pure-markdown for formatting.
   Code ticks (`` `like this` ``) are allowed in the summary line itself — e.g. to name a function, file, or flag — not just in the body.
   Do not hard-wrap the body at a fixed column width (e.g. 72 chars). Keep each paragraph or bullet point as one unbroken line, regardless of length; only break where a new paragraph or bullet genuinely starts.

   For normal use, multiple `[where]` parts can be written as one bracket with pipes, e.g. `[backend|frontend]`.

   For the base repo itself, use `[base] [optional source repo] topic: ai: …`. Recent examples include `[base] git hooks: ai: …`, `[base] ai/hooks: ai: …`, `[base] [AllMyStorage] skills: ai: …`, and `[base] [userscripts] gitignore: …`.

5. **Stage only files you changed yourself as part of the current task.** Before staging, run `git status` and `git diff --name-only` to confirm every file you are about to add. Never stage:
   - `ai/git/pending-commit.md` (it is gitignored)
   - files modified by the user, by hooks, or by other tooling that you did not touch
   - files unrelated to the task at hand, even if they appear modified

   Add files by explicit path — never `git add .` or `git add -A`.

6. Once this skill is activated, keep commiting after every completed task automatically without asking again.
   If the user responds with a simple `commit` or similar (`commit plz`, `keep commiting`, etc.), this means they want to remind you, to follow the "keep automatically committing" instruction, which you should already anyway.

7. **Never rewrite already-committed history just because you noticed a gap.** If you spot a stray `ai:` auto-commit left un-folded somewhere in existing history (e.g. a leftover `ai: updated prompt` because an unexpected commit landed in between), do not rebase/`reset --soft`/amend it away on your own initiative — ask the user first (e.g. via `AskUserQuestion`) whether they want it cleaned up. Rules 1–6 above are about commits you are making *right now* to finish the current task; they are not standing permission to rewrite arbitrary prior history whenever this skill happens to be active. An explicit cleanup request from the user (e.g. "clean up the commits since last push") still authorizes the full procedure below.

## Cleaning up stray `ai:` auto-commits

Use this procedure before merging or review when the branch has stray prompt/decision commits mixed into the history.

Handles these hook-created commits:

- **`ai: updated prompt`** — one per user prompt; touches only `ai/query.md` or `ai/°base/query.md`.
- **`ai: save decision <slug>`** — one per resolved `AskUserQuestion`; touches only `ai/query.md` or `ai/°base/query.md`. The slug is derived from the first question's text.
- **`ai: agent <id> results`** — subagent result record; touches only agent result files.
- **`ai: record memory <slug>`** — memory file written; touches only files under the memory directory.
- **Plan commits** — `ai: Plan …`, `ai: Plan Update …`, or `ai: save plan <NNN>_<slug>`; touches only `ai/plans/<NNN>_*.md` or `ai/°base/plans/<NNN>_*.md`. Keep separate — and renamed to `ai: Plan: …`/`ai: Plan update: …` (never left as the raw hook message) — when the plan file content is a genuine change cutting a new version, or when there are multiple revisions of the same plan number (the sequence records how the plan evolved). A plan commit whose content is unchanged/near-identical to the plan before it, or a lone plan commit with no real content and no follow-up updates, may be folded into its implementation instead.

### Procedure

**1. Audit the branch**

```bash
git log --oneline origin/<upstream>..HEAD
for sha in <shas>; do
  echo "$sha $(git log -1 --format='%s' $sha): $(git show --name-only --format='' $sha | tr '\n' ' ')"
done
```

**2. Plan groups**

- **`ai: updated prompt`**, **`ai: save decision <slug>`**, **`ai: agent <id> results`**, and **`ai: record memory <slug>`** commits → fix up under the **preceding** code commit by default. Exception: a prompt commit that clearly starts a different/unrelated task should stay as its own `pick`.
- **Plan commits** → fix up into the implementation commit if the plan was never revised, or if a follow-up plan-save's content turned out unchanged/near-identical (no real "change" happened, so it didn't earn a new version). Plan-saves seconds apart with non-overlapping edits (one drafting burst) squash together into a single plan `pick`. If the plan was genuinely revised in separate passes, keep each version as a separate `pick` and rename it — `ai: Plan: …` for the first, `ai: Plan update: …` for each later revision — instead of leaving the raw `ai: save plan <NNN>_<slug>` hook message. Word each summary as the eventual `ai: Run: …` summary in current tense rather than past tense.
- **Mislabeled commits** → flag commits whose message does not match the files they actually changed. Rename them as part of the rebase instead of silently folding them the wrong way.

**3. Write renamed commit messages** to `ai/git/rebase-msg-<sha>.md` for any commits needing a label fix.

**4. Write the rebase todo script**

```bash
cat > /tmp/git-rebase-todo.sh << 'SCRIPT'
cat > "$1" << 'REBASE'
pick <code-commit>
fixup <prompt-commit>          # ai: updated prompt; backward-fold
fixup <decision-commit>        # ai: save decision <slug>; backward-fold
fixup <agent-commit>           # ai: agent <id> results; backward-fold
fixup <memory-commit>          # ai: record memory <slug>; backward-fold
exec git commit --amend -F ai/git/rebase-msg-<sha>.md
pick <plan-commit>             # plan with follow-up updates: keep separate
# fixup <plan-commit>         # lone plan (no updates): may fold into implementation
pick <next-code-commit>
fixup <prompt-commit>
...
REBASE
SCRIPT
chmod +x /tmp/git-rebase-todo.sh
```

Put `exec git commit --amend -F ...` after all fixups for that group to rename the squashed result.

**5. Run**

```bash
GIT_SEQUENCE_EDITOR=/tmp/git-rebase-todo.sh git rebase -i origin/<upstream>
```

**6. Optional — rewrite HEAD as branch summary**

Write to `ai/git/pending-commit.md`:

```md
[scope] category: ai: Run: <summary>

Branch `<name>` based on `origin/<upstream>` @ <sha>.

- bullet: key decisions
- bullet: what changed and why
```

Then:

```bash
git commit --amend -F ai/git/pending-commit.md
```
