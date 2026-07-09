---
title: "Capturing Errors"
description: "Use the SDK to manually capture errors and other events."
url: https://docs.sentry.io/platforms/rust/usage/
---

# Capturing Errors | Sentry for Rust

Sentry's SDK hooks into your runtime environment and automatically reports errors, uncaught exceptions, and unhandled rejections as well as other types of errors depending on the platform.

Key terms:

* An *event* is one instance of sending data to Sentry. Generally, this data is an error or exception.
* An *issue* is a grouping of similar events.
* The reporting of an event is called *capturing*. When an event is captured, it’s sent to Sentry.

The most common form of capturing is to capture errors.

While capturing an event, you can also record the breadcrumbs that lead up to that event. Breadcrumbs are different from events: they will not create an event in Sentry, but will be buffered until the next event is sent. Learn more about breadcrumbs in our [Breadcrumbs documentation](https://docs.sentry.io/platforms/rust/enriching-events/breadcrumbs.md).

## [Capturing Errors](https://docs.sentry.io/platforms/rust/usage.md#capturing-errors)

In Rust, you can capture any `std::error::Error` type.

```rust
let result = match function_returns_error() {
    Ok(result) => result,
    Err(err) => {
        sentry::capture_error(&err);
        return Err(err);
    }
};
```

Integrations may provide more specialized capturing methods.

```rust
use sentry::integrations::anyhow::capture_anyhow;

let result = match function_returns_anyhow() {
    Ok(result) => result,
    Err(err) => {
        capture_anyhow(&err);
        return Err(err);
    }
};
```

## [Capturing Messages](https://docs.sentry.io/platforms/rust/usage.md#capturing-messages)

Another common operation is to capture a bare message. A message is textual information that should be sent to Sentry. The SDK doesn't automatically capture messages, but you can capture them manually.

Messages show up as issues on your issue stream, with the message as the issue name.

```rust
sentry::capture_message("Something went wrong", sentry::Level::Info);
```

The SDK can be initialized with the `attach_stacktrace` option set to `true` (default: `false`) to attach a stack trace to all events created out of `capture_message` calls.

## Pages in this section

- [Set the Level](https://docs.sentry.io/platforms/rust/usage/set-level.md)
