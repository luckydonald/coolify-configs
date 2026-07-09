# Bugsink / Sentry — Python backend

Part of the `bugsink-setup` skill — see `../SKILL.md` for the shared
environment-variable list, deployment wiring, and verification checklist.
This page covers the backend-specific pieces only.

## SDK init

Put Sentry init in its own module (`sentry.py` next to the app factory) so
it's one obvious place to extend later:

```python
def init_sentry() -> None:
    if not SENTRY_DSN:
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    # add more integrations for whichever libraries this backend actually
    # uses — e.g. SqlalchemyIntegration, AsyncPGIntegration, HttpxIntegration,
    # AsyncioIntegration, SysExitIntegration. Sentry auto-detects installed
    # libraries too, but listing them explicitly makes the wiring visible
    # and lets you drop the ones the project doesn't have.

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT or None,
        release=SENTRY_RELEASE or GIT_COMMIT_FULL,  # see "Release/build metadata" below
        dist=BUILD_TIME,
        integrations=[FastApiIntegration(), StarletteIntegration()],
        send_default_pii=False,  # opt in explicitly if this project wants IPs/user data sent
        traces_sample_rate=_parse_sample_rate(SENTRY_TRACES_SAMPLE_RATE),
    )
    sentry_sdk.set_tag('git_commit', GIT_COMMIT_FULL)
    sentry_sdk.set_tag('git_branch', GIT_BRANCH)
    sentry_sdk.set_tag('build_time', BUILD_TIME)
```

Two non-obvious placement details matter:

- **Call `init_sentry()` at module import time, before the app object is
  created** — not inside a startup event. Exceptions during route
  registration or other import-time setup would otherwise never reach Sentry.
- **`FastApiIntegration`/`StarletteIntegration` only instrument request
  handlers, not the ASGI lifespan protocol.** An exception raised during
  startup (a failed DB migration, a missing config value) is invisible to
  Sentry unless you capture it explicitly:
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      try:
          await run_startup_checks()
      except Exception as e:
          import sentry_sdk
          sentry_sdk.capture_exception(e)  # no-op if Sentry isn't configured — safe unconditionally
          raise
      yield
  ```
  This one line is easy to skip and means the single most important class of
  startup failure never shows up in error tracking. Don't skip it.

Add the dependency with the extra matching the web framework, e.g.
`sentry-sdk[fastapi]` (Flask/Django have their own extras).

## Release/build metadata

Sentry groups events by `release`; without one, every deploy's errors get
lumped together and you can't tell which version broke. Resolve it through a
fallback chain so it works both from a local git checkout and from inside a
Docker image where `.git` isn't present:

```python
GIT_COMMIT_FULL = (
    env('SOURCE_COMMIT', default='', required=False)  # explicit override wins
    or _git('rev-parse', 'HEAD')                        # local dev / CI with .git present
    or _read_file(APP_DIR / 'git-commit.txt')            # baked into the image at build time
    or 'unknown'
)
# same pattern for GIT_BRANCH and BUILD_TIME
```

If the deploy is a Docker build, bake `git-commit.txt`/`git-branch.txt` (and
a `build-time.txt` with the build timestamp) into the image in a stage that
still has access to `.git`, then `COPY` them into the final image — the
running container usually doesn't have `.git` available at all. Use the git
commit hash as `release` (not a hand-maintained version string — it's always
accurate and needs no bump step) and the build time as `dist`.

If this project is a subdirectory of a bigger monorepo deployed via a
platform-managed "Base Directory" (Coolify and similar), `.git` usually
isn't reachable from *any* build stage, not just hard to get to — see
`monorepo-deploys.md` for the build-arg-based fix and a real crash this
caused when skipped.

If the frontend also resolves build metadata (see `vue.md`), keep both
sides' fallback chains in sync so the same deploy reports the same
`git_commit`/`build_time` tags from both ends — that's what makes
frontend/backend events from the same deploy correlate cleanly in Bugsink.

This step is optional in the sense that Sentry works without it — but skip
it and every future "which deploy introduced this error" question becomes
guesswork.

## The tunnel (self-hosted Bugsink only)

Skip this section entirely if Bugsink/Sentry is reachable directly from the
browser with working CORS. Build it if Bugsink is self-hosted and you're not
certain the browser can reach it directly (the common case, and worth
building defensively even before you've confirmed events are missing). The
frontend half of this (probing which transport actually works) lives in
`vue.md` — this is the backend half it depends on.

A same-origin POST endpoint that forwards the raw Sentry envelope
server-side:

```python
@routes.post('/tunnel')
async def sentry_tunnel(request: Request) -> Response:
    configured_dsn = VITE_SENTRY_DSN or SENTRY_DSN
    if not configured_dsn:
        raise HTTPException(status_code=503, detail='Sentry not configured on this server')

    body = await request.body()
    if not body:
        return Response(status_code=204)

    envelope_header = json.loads(body.split(b'\n', 1)[0])
    event_dsn = envelope_header.get('dsn', '')

    # SSRF guard: only ever forward to the host this server was configured
    # for. Without this check, the endpoint is an open proxy that forwards
    # POST bodies to any host an attacker names in the envelope.
    if urlparse(event_dsn).netloc != urlparse(configured_dsn).netloc:
        raise HTTPException(status_code=403, detail='DSN host not allowed')

    parsed = urlparse(event_dsn)
    project_id = parsed.path.strip('/')
    upstream_url = f'{parsed.scheme}://{parsed.netloc}/api/{project_id}/envelope/'
    async with httpx.AsyncClient() as client:
        upstream = await client.post(upstream_url, content=body,
                                      headers={'Content-Type': 'application/x-sentry-envelope'}, timeout=10.0)
    return Response(status_code=upstream.status_code, content=upstream.content)
```

The SSRF guard is not optional — an unguarded tunnel forwards arbitrary POST
bodies anywhere the caller names.

Mount it wherever this project's API routes live (e.g.
`/api/v1/sentry/tunnel` alongside the rest of `/api/v1/...`), and tell the
frontend that exact path.

## Sample-error route

Add a deliberately-throwing route so you (and later, anyone running a
Bugsink triage) can confirm the backend side of the pipeline works end to
end without waiting for a real bug:

```python
@routes.get('/sample-error')
async def sample_error() -> None:
    raise RuntimeError('backend sample error for Sentry/Bugsink verification')
```

Word the message recognizably (contains "sample error ... for ...
verification") — anyone triaging Bugsink later needs to be able to tell this
apart from a real bug at a glance rather than investigate it.
