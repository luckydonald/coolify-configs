---
title: "axum"
description: "Learn about monitoring your axum application with Sentry."
url: https://docs.sentry.io/platforms/rust/guides/axum/
---

# axum | Sentry for axum

The Sentry SDK offers a middleware for the [axum](https://github.com/tokio-rs/axum) framework that supports:

* Reporting errors and panics with the correct request correlation.
* Starting a [transaction](https://docs.sentry.io/concepts/key-terms/tracing.md) for each request-response cycle.

The integration actually supports any crate based on [tower](https://github.com/tower-rs/tower), not just `axum`.

## [Install](https://docs.sentry.io/platforms/rust/guides/axum.md#install)

Error Monitoring\[ ]Tracing

To add Sentry with the `axum` integration to your Rust project, add a new dependency to your `Cargo.toml`:

```toml
[dependencies]
axum = "0.8.4"
tower = "0.5.2"
tokio = { version = "1.45.0", features = ["full"] }
sentry = { version = "0.48.3", features = ["tower-axum-matched-path"] }
```

## [Configure](https://docs.sentry.io/platforms/rust/guides/axum.md#configure)

Initialize and configure the Sentry client. This will enable a set of default integrations, such as panic reporting. Then, initialize `axum` with the Sentry middleware.

This snippet sets up a service that always panics, so you can test that everything is working as soon as you set it up.

Macros like `#[tokio::main]` are not supported. The Sentry client must be initialized before the async runtime is started, as shown below.

```rust
use axum::{body::Body, http::Request, routing::get, Router};
use sentry::integrations::tower::{NewSentryLayer, SentryHttpLayer};
use std::io;
use tower::ServiceBuilder;

async fn failing() -> () {
    panic!("Everything is on fire!")
}

fn main() -> io::Result<()> {
    let _guard = sentry::init((
        "https://<key>@o<orgId>.ingest.sentry.io/<projectId>",
        sentry::ClientOptions {
            release: sentry::release_name!(),
            # ___PRODUCT_OPTION_START___ performance
            // Capture all traces and spans. Set to a lower value in production
            traces_sample_rate: 1.0,
            # ___PRODUCT_OPTION_END___ performance
            // Capture user IPs and potentially sensitive headers when using HTTP server integrations
            // see https://docs.sentry.io/platforms/rust/data-management/data-collected for more info
            send_default_pii: true,
            ..Default::default()
        },
    ));

    let app = Router::new().route("/", get(failing)).layer(
        ServiceBuilder::new()
            .layer(NewSentryLayer::<Request<Body>>::new_from_top()) // Bind a new Hub per request, to ensure correct error <> request correlation
            # ___PRODUCT_OPTION_START___ performance
            .layer(SentryHttpLayer::new().enable_transaction()), // Start a transaction (Sentry root span) for each request
            // If you're binding the layers directly on the `Router`, bind them in the opposite order, otherwise you might run into a memory leak
            # ___PRODUCT_OPTION_END___ performance
    );

    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(async {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:3001")
                .await
                .unwrap();
            axum::serve(listener, app.into_make_service())
                .await
                .unwrap();
        });

    Ok(())
}
```

## [Verify](https://docs.sentry.io/platforms/rust/guides/axum.md#verify)

Send a request to the application. The panic will be captured by Sentry.

```bash
curl http://localhost:3001/
```

Learn more about manually capturing an error or message in our [Usage documentation](https://docs.sentry.io/platforms/rust/guides/axum/usage.md).

To view and resolve the recorded panic, log into [sentry.io](https://sentry.io) and select your project. Select Issues, and then Errors & Outages in the sidebar, where you will find the newly created issue. Clicking on the issue's title will open a page where you can see detailed information and mark it as resolved.

## [Next Steps](https://docs.sentry.io/platforms/rust/guides/axum.md#next-steps)

* Explore [practical guides](https://docs.sentry.io/guides.md) on what to monitor, log, track, and investigate after setup

## Other Rust Frameworks

- [Actix Web](https://docs.sentry.io/platforms/rust/guides/actix-web.md)
- [tokio-rs/tracing](https://docs.sentry.io/platforms/rust/guides/tracing.md)

## Topics

- [Extended Configuration](https://docs.sentry.io/platforms/rust/guides/axum/configuration.md)
- [Capturing Errors](https://docs.sentry.io/platforms/rust/guides/axum/usage.md)
- [Enriching Events](https://docs.sentry.io/platforms/rust/guides/axum/enriching-events.md)
- [Source Context](https://docs.sentry.io/platforms/rust/guides/axum/source-context.md)
- [Data Management](https://docs.sentry.io/platforms/rust/guides/axum/data-management.md)
- [Tracing](https://docs.sentry.io/platforms/rust/guides/axum/tracing.md)
- [Logs](https://docs.sentry.io/platforms/rust/guides/axum/logs.md)
- [Metrics](https://docs.sentry.io/platforms/rust/guides/axum/metrics.md)
- [User Feedback](https://docs.sentry.io/platforms/rust/guides/axum/user-feedback.md)
- [Security Policy Reporting](https://docs.sentry.io/platforms/rust/guides/axum/security-policy-reporting.md)
- [Troubleshooting](https://docs.sentry.io/platforms/rust/guides/axum/troubleshooting.md)
