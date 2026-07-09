---
name: "bugsink-triage"
description: "Triage current errors in Bugsink (a self-hosted, Sentry-API-compatible error tracker). Use this whenever the user asks to check Bugsink, check for errors/exceptions in production, see what's failing, or review Sentry-style issues — even if they only name one project or one side of the stack (frontend/backend/worker/etc.), since related projects in the same Bugsink instance are almost always worth cross-checking together. Produces a triage report of real, unresolved issues; does not investigate root cause or write fixes."
---

# Bugsink Triage

Bugsink is a self-hosted, Sentry-API-compatible error tracker. A given
Bugsink instance can host any number of projects — a backend and a
frontend, several microservices, a mobile app, whatever the project this
skill is running in happens to ship. This skill produces a quick, accurate
read on what's actually broken right now across all of them — nothing more.
Root-causing and fixing is a separate, heavier follow-up the user can ask
for once they know which issue is worth their time.

## Why triage is its own step

Every issue in Bugsink costs real investigation time to run down. Jumping
straight to "let's fix issue X" without first surveying everything risks
fixing something minor while a more important issue sits unnoticed, or
duplicating effort on two issues that are actually the same root cause
surfacing on both ends of the stack. Triage answers three questions before
any digging starts: *what's real, what's noise, and what's connected?*

## Steps

1. **List all projects** (`mcp__bugsink__list_projects`). Don't assume you
   already know the project layout from a previous run or from the repo's
   name — list them fresh each time, since projects can be added or renamed.
   If the user named a specific project, still list the rest; a "check the
   backend" request benefits from a quick look at whatever else shares the
   instance, since one component's failure often shows up as a symptom
   elsewhere too.

2. **List issues per project**, sorted by `last_seen` descending
   (`mcp__bugsink__list_issues`). Do this for every relevant project
   returned in step 1, not just the one the user named directly.

3. **Filter out noise before reporting anything.** Two categories don't
   belong in the headline results:
   - `is_muted: true` — someone already decided this doesn't need attention.
   - Sample/verification errors — recognizable by wording like *"sample
     error for Sentry/Bugsink verification"*, a message that's obviously a
     deliberately-thrown test error, or a `transaction`/route that looks
     like a self-test endpoint (e.g. `/test/sentry`, `.../sample-error`).
     These exist to confirm the error pipeline works, not to report real bugs.

   Still mention their existence briefly (e.g. "2 muted/verification issues
   excluded") so the user knows they weren't silently dropped — just don't
   spend investigation effort on them.

4. **Correlate issues across projects.** A generic error in one project
   (e.g. a frontend `500 Internal Server Error` on some route, or a worker
   timeout) is frequently just the visible symptom of a failure in another
   project it depends on, not an independent bug. Check for:
   - Overlapping `first_seen`/`last_seen` windows across projects.
   - A `transaction` in one project that plausibly corresponds to an
     endpoint, job, or call path implicated in another project's issue.

   When issues across projects look connected, say so explicitly and treat
   the one closest to the actual failure (e.g. the backend/service side
   rather than the UI that merely surfaced it) as primary — that's almost
   always where the real fix belongs, with the other as a downstream
   consequence rather than a separate thing to fix.

5. **Report the results.** For each project, list the real (non-muted,
   non-sample) unresolved issues with: friendly ID, error type/value,
   transaction, first/last seen, and event count. Group correlated
   cross-project pairs/clusters together and call out the relationship.
   Close with a one-line count of what was excluded as noise.

   Do not start editing code at this stage — that's a follow-up once the user picks which issue(s) to chase.
   Instead list the issues in a new `ai/errors/<unpadded-digits>.md` file.
   Since it's markdown you can combine related errors and such.
   The idea here is to have a piece of summary of the error, so if something similar would come up later it - and later on also it's solution - is documented.

   If the user wants to go deeper on a specific issue,
   `mcp__bugsink__get_issue`, `mcp__bugsink__list_events`, and
   `mcp__bugsink__get_stacktrace` are available for that next step.
