# Bugsink / Sentry — Rust backend

Part of the `bugsink-setup` skill — see `../SKILL.md` for the shared
environment-variable list, deployment wiring, and verification checklist.
This page covers the backend-specific pieces only.

## SDK init

Put Sentry init in its own module (`sentry.rs` next to wherever `main`
lives) so it's one obvious place to extend later:

```rust
pub fn init_sentry() -> Option<sentry::ClientInitGuard> {
    let dsn = std::env::var("SENTRY_DSN").unwrap_or_default();
    if dsn.is_empty() {
        return None;
    }

    Some(sentry::init((dsn, sentry::ClientOptions {
        environment: std::env::var("SENTRY_ENVIRONMENT").ok().map(Into::into),
        release: std::env::var("SENTRY_RELEASE").ok().map(Into::into).or_else(|| release_tag()),
        dist: build_time().map(Into::into),
        send_default_pii: false,  // opt in explicitly if this project wants IPs/user data sent
        traces_sample_rate: parse_sample_rate(&std::env::var("SENTRY_TRACES_SAMPLE_RATE").unwrap_or_default()),
        ..Default::default()
    })))
}
```

`sentry::init` returns a guard — keep it alive for the lifetime of the
process (e.g. `let _sentry_guard = init_sentry();` in `main`, held in a
variable that isn't dropped). It flushes buffered events on drop with a
two-second deadline; if it's dropped early (or never bound to a variable),
events queued right before shutdown are silently lost.

One non-obvious detail matters more here than in most languages: **the
Sentry client must be initialized before the async runtime starts**, so
`#[tokio::main]`/`#[actix_web::main]` can't be used — those macros start the
runtime before any of your code runs. Panic and error reporting installed by
`sentry::init` needs to happen first so it can hook the whole process,
including tasks spawned during runtime startup. Structure `main` like this
instead:

```rust
// WRONG — #[tokio::main] starts the runtime before init_sentry() can run
// #[tokio::main]
// async fn main() { ... }

// RIGHT
fn main() {
    let _sentry_guard = init_sentry();

    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .unwrap()
        .block_on(async {
            // app entrypoint
        });
}
```

Unhandled panics are captured automatically by the default `PanicIntegration`
— no extra wiring needed there, unlike Python's lifespan gotcha where
startup exceptions need an explicit `capture_exception` call.

Add the dependency, plus a framework integration if this backend uses one
(axum/tower shown here — Actix Web and `tokio-rs/tracing` have their own
integrations, see `sentry`'s docs.rs page if this project uses those
instead):

```toml
[dependencies]
sentry = { version = "0.48.3", features = ["tower-axum-matched-path"] }
sentry-tower = "0.48.3"  # if not already re-exported by the `sentry` crate's tower feature
```

```rust
use sentry::integrations::tower::{NewSentryLayer, SentryHttpLayer};
use tower::ServiceBuilder;

let app = Router::new().route(/* ... */).layer(
    ServiceBuilder::new()
        .layer(NewSentryLayer::<Request<Body>>::new_from_top()) // binds a fresh Hub per request for correct error<->request correlation
        .layer(SentryHttpLayer::new().enable_transaction()),    // starts a transaction per request; omit if tracing is disabled
);
```

Without `NewSentryLayer`, errors captured mid-request still reach Sentry but
without the request context (URL, headers, method) attached — worth having
even if tracing spans aren't needed.

## Release/build metadata

Sentry groups events by `release`; without one, every deploy's errors get
lumped together and you can't tell which version broke. The `sentry` crate
ships a `sentry::release_name!()` macro that resolves to the Cargo package
name/version at compile time — convenient, but it tracks the crate version,
not the deploy. If this project's other stack-side (see `python.md`/`vue.md`)
already keys releases off the git commit, keep Rust on the same scheme so
frontend/backend/other-backend events from one deploy share a `release` tag:

```rust
fn release_tag() -> Option<std::borrow::Cow<'static, str>> {
    std::env::var("SOURCE_COMMIT").ok()                              // explicit override wins
        .filter(|s| !s.is_empty())
        .or_else(|| run("git", &["rev-parse", "HEAD"]))              // local dev / CI with .git present
        .or_else(|| std::fs::read_to_string("git-commit.txt").ok())  // baked into the image at build time
        .map(|s| s.trim().to_string().into())
}

fn run(cmd: &str, args: &[&str]) -> Option<String> {
    std::process::Command::new(cmd).args(args).output().ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
}
// same pattern for git_branch, tagged with sentry::configure_scope after init
```

If the deploy is a Docker build, bake `git-commit.txt`/`git-branch.txt` (and
a `build-time.txt`) into the image in a stage that still has access to
`.git`, then `COPY` them into the final image — same as the Python guide,
since the running container usually has no `.git` at all. Use the git commit
as `release` and the build time as `dist`.

If this project is a subdirectory of a bigger monorepo deployed via a
platform-managed "Base Directory" (Coolify and similar), `.git` usually
isn't reachable from *any* build stage, not just hard to get to — see
`monorepo-deploys.md` for the build-arg-based fix and a real crash this
caused when skipped.

This step is optional in the sense that Sentry works without it (fall back
to `sentry::release_name!()` if this backend has no sibling stack to stay in
sync with) — but skip the cross-stack alignment and every future "which
deploy introduced this error" question spanning multiple services becomes
guesswork.

## The tunnel (self-hosted Bugsink only)

Skip this section entirely if Bugsink/Sentry is reachable directly from the
browser with working CORS. Build it if this Rust service is the backend a
Vue (or other) frontend talks to, and you're not certain the browser can
reach Bugsink directly — see `vue.md`'s tunnel section for the frontend half
this depends on.

A same-origin POST endpoint that forwards the raw Sentry envelope
server-side (shown as an axum handler; adapt the extractor types for
Actix/other frameworks):

```rust
async fn sentry_tunnel(body: axum::body::Bytes) -> impl axum::response::IntoResponse {
    let configured_dsn = std::env::var("VITE_SENTRY_DSN")
        .or_else(|_| std::env::var("SENTRY_DSN"))
        .unwrap_or_default();
    if configured_dsn.is_empty() {
        return (StatusCode::SERVICE_UNAVAILABLE, "Sentry not configured on this server").into_response();
    }
    if body.is_empty() {
        return StatusCode::NO_CONTENT.into_response();
    }

    let header_line = body.split(|&b| b == b'\n').next().unwrap_or(&[]);
    let envelope_header: serde_json::Value = match serde_json::from_slice(header_line) {
        Ok(v) => v,
        Err(_) => return (StatusCode::BAD_REQUEST, "malformed envelope").into_response(),
    };
    let event_dsn = envelope_header.get("dsn").and_then(|v| v.as_str()).unwrap_or("");

    // SSRF guard: only ever forward to the host this server was configured
    // for. Without this check, the endpoint is an open proxy that forwards
    // POST bodies to any host an attacker names in the envelope.
    let (Ok(event_url), Ok(configured_url)) = (Url::parse(event_dsn), Url::parse(&configured_dsn)) else {
        return (StatusCode::FORBIDDEN, "DSN host not allowed").into_response();
    };
    if event_url.host_str() != configured_url.host_str() {
        return (StatusCode::FORBIDDEN, "DSN host not allowed").into_response();
    }

    let project_id = event_url.path().trim_start_matches('/');
    let upstream_url = format!("{}://{}/api/{}/envelope/",
        event_url.scheme(), event_url.host_str().unwrap_or_default(), project_id);

    let client = reqwest::Client::new();
    match client.post(upstream_url)
        .header("Content-Type", "application/x-sentry-envelope")
        .body(body)
        .timeout(std::time::Duration::from_secs(10))
        .send().await
    {
        Ok(upstream) => (upstream.status(), upstream.bytes().await.unwrap_or_default()).into_response(),
        Err(_) => StatusCode::BAD_GATEWAY.into_response(),
    }
}
```

The SSRF guard is not optional — an unguarded tunnel forwards arbitrary POST
bodies anywhere the caller names.

Mount it wherever this project's API routes live (e.g.
`/api/v1/sentry/tunnel` alongside the rest of `/api/v1/...`), and tell the
frontend that exact path.

## Sample-error route

Add a deliberately-failing route so you (and later, anyone running a
Bugsink triage) can confirm the backend side of the pipeline works end to
end without waiting for a real bug:

```rust
async fn sample_error() -> impl axum::response::IntoResponse {
    panic!("backend sample error for Sentry/Bugsink verification");
}
```

Word the message recognizably (contains "sample error ... for ...
verification") — anyone triaging Bugsink later needs to be able to tell this
apart from a real bug at a glance rather than investigate it. A panic works
here because the default `PanicIntegration` reports it automatically; use
`sentry::capture_error(&err)` instead if this route should return a normal
error response rather than actually crash the request.

## Structured logs (optional)

If this project wants log-level detail in Bugsink (not just errors), the
`logs` feature flag sends `tracing`/`log` records as their own Sentry
events, separate from error capturing:

```toml
[dependencies]
sentry = { version = "0.48.3", features = ["tracing", "logs"] }
```

```rust
use tracing_subscriber::prelude::*;
tracing_subscriber::registry()
    .with(tracing_subscriber::fmt::layer())
    .with(sentry::integrations::tracing::layer())
    .init();
```

This is additive to error capturing, not a replacement — skip it unless the
project specifically wants log search/correlation inside Bugsink.
