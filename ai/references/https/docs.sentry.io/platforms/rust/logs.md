---
title: "Logs"
description: "Structured logs allow you to send, view, and query logs sent from your applications within Sentry."
url: https://docs.sentry.io/platforms/rust/logs/
---

# Set Up Logs in Rust | Sentry for Rust

With Sentry Structured Logs, you can send text-based log information from your applications to Sentry. Once in Sentry, these logs can be viewed alongside relevant errors, searched by text-string, or searched using their individual attributes.

## [Requirements](https://docs.sentry.io/platforms/rust/logs.md#requirements)

Logs in Rust are supported in Sentry Rust SDK version `0.42.0` and above. Additionally, the `logs` feature flag needs to be enabled.

```toml
[dependencies]
sentry = { version = "0.48.3", features = ["logs"] }
```

## [Setup](https://docs.sentry.io/platforms/rust/logs.md#setup)

To enable logging, just add the `sentry` dependency with the `logs` feature flag. This will set `enable_logs: true` by default in your `sentry::ClientOptions`. You can opt-out from sending logs even while using the `logs` feature flag by explicitly initializing the SDK with `enable_logs: false`.

## [Usage](https://docs.sentry.io/platforms/rust/logs.md#usage)

Once the feature is enabled and the SDK is initialized, you can send logs by using the logging macros. The `sentry` crate exposes macros that support six different log levels: `logger_trace`, `logger_debug`, `logger_info`, `logger_warn`, `logger_error` and `logger_fatal`. The macros support logging a simple message, or a message with parameters, with `format` syntax:

```rust
use sentry::logger_info;

logger_info!("Hello, world!");
logger_info!("Hello, {}!", "world");
```

You can also attach additional attributes to a log using the `key = value` syntax before the message:

```rust
use sentry::logger_error;

logger_error!(
    database.host = "prod-db-01",
    database.port = 5432,
    database.name = "user_service",
    retry_attempt = 2,
    beta_features = false,
    "Database connection failed"
);
```

The supported attribute keys consist of any number of valid Rust identifiers, separated by dots. Attributes containing dots will be nested under their common prefix when displayed in the UI.

The supported attribute values correspond to the values that can be converted to a `serde_json::Value`, which include primitive types for numbers, `bool`, and string types. As of today, array and object types will be converted to strings using their JSON representation.

## [Integrations](https://docs.sentry.io/platforms/rust/logs.md#integrations)

### [`tracing` Integration](https://docs.sentry.io/platforms/rust/logs.md#tracing-integration)

To use the `tracing` integration with logs, add the necessary dependencies to your `Cargo.toml`.

```toml
[dependencies]
sentry = { version = "0.48.3", features = ["tracing", "logs"] }
tracing = "0.1.41"
tracing-subscriber = "0.3.19"
```

Then, use standard `tracing` macros to capture logs.

```rust
use sentry::integrations::tracing::EventFilter;
use tracing::{error, info, warn};
use tracing_subscriber::prelude::*;

fn main() {
    let _guard = sentry::init((
        "https://<key>@o<orgId>.ingest.sentry.io/<projectId>",
        sentry::ClientOptions {
            release: sentry::release_name!(),
            enable_logs: true,
            ..Default::default()
        },
    ));

    tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer())
        .with(sentry::integrations::tracing::layer())
        .init();

    info!("Hello, world!");

    warn!(
        database.host = "prod-db-01",
        database.port = 5432,
        retry_attempt = 2,
        "Database connection slow"
    );

    error!(error_code = 500, "Critical system failure");
}
```

The fields of `tracing` events are automatically captured as attributes in Sentry logs.

By default, `tracing` events at or above info level are captured as Sentry logs.

This behavior can be customized by applying a custom `event_filter` when creating the layer. The following snippet shows the default `event_filter` that's applied when using the `sentry` crate with the `logs` feature flag.

```rust
use sentry::integrations::tracing::EventFilter;

let sentry_layer =
    sentry::integrations::tracing::layer().event_filter(|md| match *md.level() {
        // Capture error and warn level events as both logs and events in Sentry
        tracing::Level::ERROR => EventFilter::Event | EventFilter::Log,
        // Ignore trace level events, as they're too verbose
        tracing::Level::TRACE => EventFilter::Ignore,
        // Capture everything else as both a breadcrumb and a log
        _ => EventFilter::Breadcrumb | EventFilter::Log,
    });
```

### [`log` Integration](https://docs.sentry.io/platforms/rust/logs.md#log-integration)

To use the `log` integration with logs, add the necessary dependencies to your `Cargo.toml`.

```toml
[dependencies]
sentry = { version = "0.48.3", features = ["log", "logs"] }
log = "0.4"
# optional: use any log implementation you prefer
env_logger = "0.11"
```

Then, use standard `log` macros to capture logs.

```rust
use sentry_log::LogFilter;
use log::{info, warn, error};

fn main() {
    let _guard = sentry::init((
        "https://<key>@o<orgId>.ingest.sentry.io/<projectId>",
        sentry::ClientOptions {
            release: sentry::release_name!(),
            enable_logs: true,
            ..Default::default()
        },
    ));

    let logger = sentry::integrations::log::SentryLogger::with_dest(
        env_logger::Builder::from_default_env().build(),
    );
    log::set_boxed_logger(Box::new(logger)).unwrap();
    log::set_max_level(log::LevelFilter::Trace);

    info!("Hello, world!");
    warn!("Database connection slow");
    error!("Critical system failure");
}
```

### [Additional Integrations](https://docs.sentry.io/platforms/rust/logs.md#additional-integrations)

We're always looking to expand integration support for Logs. If you'd like to see support for additional logging libraries, please open a [new issue](https://github.com/getsentry/sentry-rust/issues/new/choose) with your request.

## [Options](https://docs.sentry.io/platforms/rust/logs.md#options)

### [`before_send_log`](https://docs.sentry.io/platforms/rust/logs.md#before_send_log)

To filter logs, or update them before they are sent to Sentry, you can use the `before_send_log` client option.

```rust
let _guard = sentry::init(("https://<key>@o<orgId>.ingest.sentry.io/<projectId>", sentry::ClientOptions {
    release: sentry::release_name!(),
    enable_logs: true,
    before_send_log: Some(std::sync::Arc::new(|log| {
        // filter out all trace level logs
        if log.level == sentry::protocol::LogLevel::Trace {
            return None;
        }
        Some(log)
    })),
    ..Default::default()
}));
```

## [Default Attributes](https://docs.sentry.io/platforms/rust/logs.md#default-attributes)

The Rust SDK automatically sets several default attributes on all log entries to provide context and improve debugging:

### [Core Attributes](https://docs.sentry.io/platforms/rust/logs.md#core-attributes)

* `environment`: The environment set in the SDK if defined. This is sent from the SDK as `sentry.environment`.
* `release`: The release set in the SDK if defined. This is sent from the SDK as `sentry.release`.
* `sdk.name`: The name of the SDK that sent the log. This is sent from the SDK as `sentry.sdk.name`.
* `sdk.version`: The version of the SDK that sent the log. This is sent from the SDK as `sentry.sdk.version`.

### [Message Template Attributes](https://docs.sentry.io/platforms/rust/logs.md#message-template-attributes)

If the log was parameterized, Sentry adds the message template and parameters as log attributes.

* `message.template`: The parameterized template string. This is sent from the SDK as `sentry.message.template`.
* `message.parameter.X`: The parameters to fill the template string. X can either be the number that represent the parameter's position in the template string (`sentry.message.parameter.0`, `sentry.message.parameter.1`, etc) or the parameter's name (`sentry.message.parameter.item_id`, `sentry.message.parameter.user_id`, etc). This is sent from the SDK as `sentry.message.parameter.X`.

### [Server Attributes](https://docs.sentry.io/platforms/rust/logs.md#server-attributes)

* `server.address`: The address of the server that sent the log. Equivalent to `server_name` that gets attached to Sentry errors.

### [User Attributes](https://docs.sentry.io/platforms/rust/logs.md#user-attributes)

If user information is available in the current scope, the following attributes are added to the log:

* `user.id`: The user ID.
* `user.name`: The username.
* `user.email`: The email address.

### [Integration Attributes](https://docs.sentry.io/platforms/rust/logs.md#integration-attributes)

If a log is generated by an SDK integration, the SDK will set additional attributes to help you identify the source of the log.

* `origin`: The origin of the log. This is sent from the SDK as `sentry.origin`.
