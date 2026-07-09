# Bugsink / Sentry — Vue frontend

Part of the `bugsink-setup` skill — see `../SKILL.md` for the shared
environment-variable list, deployment wiring, and verification checklist.
This page covers the frontend-specific pieces only.

## SDK init

```ts
// src/sentry.ts
import * as Sentry from "@sentry/vue";

export function initSentry(app: VueApp, router: Router, apiBaseUrl: string): void {
  const dsn = env.VITE_SENTRY_DSN;
  if (!dsn) return;
  Sentry.init({
    app, dsn,
    environment: env.VITE_SENTRY_ENVIRONMENT || undefined,
    release: env.VITE_SENTRY_RELEASE || __GIT_COMMIT_FULL__,   // see "Release/build metadata" below
    dist: __BUILD_TIME__,
    integrations: [
      Sentry.browserTracingIntegration({ router }),
      Sentry.vueIntegration({ app, tracingOptions: { trackComponents: true } }),
    ],
    sendDefaultPii: false,
    initialScope: { tags: { git_commit: __GIT_COMMIT_FULL__, git_branch: __GIT_BRANCH__, build_time: __BUILD_TIME__ } },
    tracesSampleRate: parseSampleRate(env.VITE_SENTRY_TRACES_SAMPLE_RATE) ?? 0,
  });
}
```

Add `@sentry/vue` as a runtime dependency.

## Release/build metadata

`__GIT_COMMIT_FULL__`/`__GIT_BRANCH__`/`__BUILD_TIME__` are compile-time
globals injected via Vite's `define`, resolved with the same
env-override → git → baked-file fallback chain used on the backend
(see `python.md`'s "Release/build metadata" section — keep both sides in
sync so a single deploy reports the same tags from both ends):

```js
// vite.config
function getBuildInfo() {
  const sourceCommit = process.env.SOURCE_COMMIT || ''
  try {
    return {
      commit: sourceCommit || execSync('git rev-parse HEAD', { encoding: 'utf8' }).trim(),
      branch: process.env.GIT_BRANCH || execSync('git rev-parse --abbrev-ref HEAD', { encoding: 'utf8' }).trim(),
    }
  } catch {
    // git binary unavailable (Docker build) — fall back to files baked in by a git-aware build stage
    return {
      commit: sourceCommit || readFile('git-commit.txt') || 'unknown',
      branch: process.env.GIT_BRANCH || readFile('git-branch.txt') || 'unknown',
    }
  }
}
// define: { __GIT_COMMIT_FULL__: JSON.stringify(buildInfo.commit), ... }
```

## The tunnel (self-hosted Bugsink only)

Skip this section entirely if Bugsink/Sentry is reachable directly from the
browser with working CORS. Build it whenever the backend half (see
`python.md`'s "The tunnel" section) is in place — this is the frontend half
that decides when to use it.

Don't hard-wire the tunnel on or off; probe first so the setup degrades
gracefully whether Bugsink is reachable directly or not:

1. On init, POST an empty body straight to the DSN's envelope URL. If that
   succeeds, use the DSN directly — no tunnel overhead.
2. If it fails, POST to the tunnel endpoint instead (the path the backend
   mounted, e.g. `/api/v1/sentry/tunnel`). If that works, re-init Sentry with
   `tunnel: '<that path>'` added to the options.
3. Log both checks (`console.info`/`console.warn`) — this is the single
   fastest way to diagnose "no errors showing up in Bugsink" during setup,
   since a silently-dropped browser event otherwise looks identical to "no
   errors happened."

```ts
async function probeConnection(label: string, url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { method: "POST", mode: "cors", body: "" });
    if (!response.ok && response.status !== 400) throw new Error(`HTTP ${response.status}`);
    console.info(`sentry connection check: ${label} - OK`);
    return true;
  } catch (e) {
    console.warn(`sentry connection check: ${label} - ERROR: ${e instanceof Error ? e.message : String(e)}`);
    return false;
  }
}
```

If the API base URL can change at runtime (e.g. the user points the app at a
different backend), re-run the probe when it changes rather than only at
startup.

## Pinia state scrubbing

If this project uses Pinia, add a state-scrubbing plugin before wiring
`Sentry.createSentryPiniaPlugin` in — captured store state is otherwise
attached to every event verbatim, including auth tokens and any other
secrets sitting in the store:

```ts
export function sentryPiniaPlugin() {
  return Sentry.createSentryPiniaPlugin({
    stateTransformer: (state) => {
      const { accessToken, refreshToken, /* ...other secrets... */, ...safe } = state;
      return safe; // also collapse any large arrays to just a count if they bloat the payload
    },
  });
}
```

Get this list from whatever the store actually holds — grep the Pinia store
for token/secret/credential-shaped keys rather than guessing. Register it
with `pinia.use(sentryPiniaPlugin())`.

## Sourcemap upload (recommended, optional)

Without sourcemaps, stack traces in Bugsink show minified code — useless for
debugging. `@sentry/vite-plugin` uploads them automatically on build:

```js
// vite.config
import { sentryVitePlugin } from '@sentry/vite-plugin'
// ...
plugins: [
  // gate on non-VITE_-prefixed build secrets so a build without them
  // (local dev, CI without credentials) still succeeds — just without upload
  env.BUILD_BUGSINK_URL && env.BUILD_BUGSINK_AUTH_TOKEN
    ? sentryVitePlugin({
        url: env.BUILD_BUGSINK_URL,
        authToken: env.BUILD_BUGSINK_AUTH_TOKEN,
        org: 'none',              // Bugsink has no orgs; any non-empty string is accepted
        project: env.BUILD_BUGSINK_PROJECT_SLUG,
        release: { name: buildInfo.commit, create: false, finalize: false, inject: true },
        sourcemaps: { filesToDeleteAfterUpload: ['./dist/**/*.map'] },
      })
    : null,
],
build: { sourcemap: true },
```

`BUILD_BUGSINK_URL`/`BUILD_BUGSINK_AUTH_TOKEN`/`BUILD_BUGSINK_PROJECT_SLUG`
are deliberately **not** `VITE_`-prefixed — a `VITE_` prefix means Vite
inlines the value into the shipped client bundle, which would leak the
upload auth token to every visitor. Keep them build-time-only secrets, and
delete the local `.map` files after a successful upload so they aren't
served publicly alongside the JS.

## Sample-error test view

Add a route that isn't linked from normal navigation, so you (and later,
anyone running a Bugsink triage) can confirm the frontend side of the
pipeline works end to end without waiting for a real bug:

```vue
<script setup lang="ts">
onMounted(async () => {
  try { await http.get("/sample-error"); } catch { /* expected */ }
  setTimeout(() => { throw new Error("frontend sample error for Sentry/Bugsink verification"); }, 0);
});
</script>
```

Word the message recognizably (contains "sample error ... for ...
verification") — anyone triaging Bugsink later needs to be able to tell this
apart from a real bug at a glance rather than investigate it.
