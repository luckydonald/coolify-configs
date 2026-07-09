---
name: "bugsink-setup"
description: "Wire up Sentry-compatible error tracking (Bugsink or Sentry SaaS) for a backend and/or frontend: SDK init, environment variables, release/build tagging, sourcemap upload, and — critical for self-hosted Bugsink — a same-origin tunnel so CORS/mixed-content doesn't silently swallow browser errors. Currently covers Python and Rust backends and Vue frontends, with more stacks addable over time. Use this whenever the user asks to add error monitoring, crash reporting, exception tracking, hook up Sentry or Bugsink, or wants to see JS/Python/Rust errors from production — even if they only mention one side of the stack, or say things like 'track errors in prod' or 'set up crash reporting' without naming Bugsink/Sentry explicitly."
---

# Bugsink / Sentry Setup

Bugsink is a self-hosted, Sentry-API-compatible error tracker. Because it's
usually self-hosted rather than the Sentry SaaS product, two things about it
routinely trip up an otherwise-correct Sentry integration:

1. **The browser SDK talks to the DSN host directly.** If Bugsink runs over
   plain HTTP while the app is HTTPS (mixed content), or Bugsink doesn't send
   CORS headers, the browser silently drops every event — no error, no
   console warning, nothing reaches Bugsink. The fix is a same-origin tunnel:
   the browser POSTs to your own backend, which forwards to Bugsink
   server-side where CORS/mixed-content rules don't apply. This needs
   cooperation from both sides of the stack, so it's covered on both the
   backend and frontend reference pages below.
2. **Bugsink has no concept of "orgs."** Sentry's own tooling (e.g. sourcemap
   upload plugins) asks for an org slug regardless; any non-empty placeholder
   works since Bugsink ignores it.

Everything else is standard Sentry SDK wiring that also works fine against
Sentry SaaS — the tunnel is the one piece worth skipping if the DSN is
directly reachable from the browser with normal CORS.

## Stack-specific guides

Read the guide(s) that match what's actually in the project — don't read
both if only one side exists, and don't skip a side that does exist just
because the user only asked about the other one (a "the browser reports no
errors" complaint is very often actually a tunnel/CORS problem that has to
be diagnosed from both ends).

- **`references/python.md`** — backend SDK init, ASGI lifespan gotcha,
  release/build metadata, the tunnel endpoint, sample-error route.
- **`references/rust.md`** — backend SDK init, the pre-async-runtime init
  gotcha, release/build metadata, the tunnel endpoint, sample-error route.
- **`references/vue.md`** — frontend SDK init, tunnel probe-and-fallback,
  Pinia state scrubbing, sourcemap upload, sample-error test view.

This skill only has those three so far. If a project needs another language
or frontend framework, write a new `references/<name>.md` following the same
shape (SDK init → release tagging → tunnel-if-needed → sourcemap upload) and
list it here — don't bloat this dispatcher file with it.

There's also **`references/monorepo-deploys.md`**, an optional addendum
(not a stack guide, so it isn't in the list above) covering a deploy
topology gotcha — see "Orient yourself" below for when it applies.

## Orient yourself before writing anything

Don't assume file layout — find the real ones for this project:

- The backend's ASGI/WSGI app entrypoint and however it already reads
  environment variables (a `Settings` class, a hand-rolled `env()` helper,
  plain `os.environ`, `.env.example`). Add new variables the same way, don't
  introduce a second pattern.
- The frontend's `main.ts`/`main.js` entrypoint and its Vite config.
- Whether either side already has partial Sentry code (an old attempt, a
  `sentry.py`/`sentry.ts` stub) — extend it instead of starting over.
- Whether this project is a subdirectory of a bigger monorepo, deployed via
  a platform-managed "Base Directory" or equivalent subdirectory scoping
  (Coolify and similar). If so, read `references/monorepo-deploys.md`
  before writing the release/build-metadata step — `.git` is very often
  unreachable from that kind of build, and the generic "bake it in a stage
  that still has `.git`" advice doesn't apply.

Ask the user for the DSN(s) if they're not already in an env file — never
invent one. Find out: is there one Bugsink project for both frontend and
backend, or two separate DSNs? Usually it's two — most setups create a
separate Sentry/Bugsink project per side, so default to wiring up two
independent DSN env vars rather than assuming the frontend can just fall
back to the backend's. Is Bugsink reachable directly from a browser, or
only from inside the backend's network (the common self-hosted case, which
is exactly when the tunnel matters)?

## Environment variables

A minimal, framework-agnostic set — reuse these names unless the project has
an established different convention:

| Variable | Meaning |
|---|---|
| `SENTRY_DSN` | Backend DSN. Empty/unset = reporting disabled. |
| `SENTRY_ENVIRONMENT` | e.g. `production`, `development`. |
| `SENTRY_RELEASE` | Optional override; defaults to the git commit (see the language guides). |
| `SENTRY_TRACES_SAMPLE_RATE` | `0`–`1`; omit or `0` to disable tracing spans. |
| `VITE_SENTRY_DSN` | Frontend DSN. Usually a *different* value from `SENTRY_DSN` — frontend and backend are typically separate Sentry/Bugsink projects, so treat this as its own env var to configure rather than one that just inherits the backend's. Only default it to `SENTRY_DSN` if the user confirms both sides genuinely share one project. |
| `VITE_SENTRY_ENVIRONMENT`, `VITE_SENTRY_RELEASE`, `VITE_SENTRY_TRACES_SAMPLE_RATE` | Frontend equivalents of the above. |

Treat an empty-string DSN as "disabled," not an error — every init function
should no-op cleanly when the DSN is blank so Sentry stays opt-in per
environment (e.g. off in local dev, on in production) with zero code branches.

## Deployment env passthrough

Wherever the deployment config lives (docker-compose, k8s manifests,
platform-specific env config), pass the variables above through to both
services as two independent values — since frontend and backend are usually
separate DSNs, don't wire one to fall back to the other unless the user
confirmed a single shared project:

```yaml
environment:
  SENTRY_DSN: '${SENTRY_DSN:-}'
  VITE_SENTRY_DSN: '${VITE_SENTRY_DSN:-}'
  SENTRY_ENVIRONMENT: '${SENTRY_ENVIRONMENT:-production}'
```

If the user *did* confirm both sides share one Bugsink project, it's fine to
collapse this to `VITE_SENTRY_DSN: '${VITE_SENTRY_DSN:-${SENTRY_DSN:-}}'` so
one value drives both — just don't make that the default assumption.

## Verify before calling it done

- With DSNs unset, confirm the app still starts and runs normally (Sentry
  must be fully inert, not an error, when not configured).
- With DSNs set, hit the sample-error routes described in the language
  guides and confirm both events land in Bugsink with the right
  release/environment tags.
- If you built the tunnel, deliberately break the direct path (or just check
  the console logs from the probe) to confirm the fallback actually engages
  — an untested fallback path is the most common way this kind of setup
  silently regresses later.

Once this is wired up, ongoing monitoring is a different task — if a
`bugsink-triage` skill is available in this project, point the user at it
for periodically checking what's actually failing in production.
