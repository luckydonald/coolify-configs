---
title: "Vue Router"
description: "Learn about Sentry's Vue Routing integration."
url: https://docs.sentry.io/platforms/javascript/guides/vue/features/vue-router/
---

# Vue Router | Sentry for Vue

If you're using Vue Router, use our provided routing instrumentation to get better parameterized transaction names.

Our routing instrumentation supports `vue-router` v2, v3, and v4.

Here is a full example of how to use it:

```javascript
import Vue from "vue";
import App from "./App";
import * as Sentry from "@sentry/vue";
import Router from "vue-router";
import HelloWorld from "@/components/HelloWorld";
import Foo from "@/components/Foo";
import Bar from "@/components/Bar";

Vue.use(Router);

const router = new Router({
  // your router configuration
});

Sentry.init({
  Vue,
  dsn: "https://<key>@o<orgId>.ingest.sentry.io/<projectId>",
  tracesSampleRate: 1.0,
  integrations: [Sentry.browserTracingIntegration({ router })],
});

new Vue({
  el: "#app",
  router,
  components: { App },
  template: "<App/>",
});
```

## [Configuration](https://docs.sentry.io/platforms/javascript/guides/vue/features/vue-router.md#configuration)

You can pass an optional configuration object as a second argument to the browser tracing integration:

```javascript
Sentry.browserTracingIntegration({
  router,
  routeLabel: "path",
});
```

The available options are:

| Key        | Type   | Default | Description                                                                                                                                                                                                                                                           |
| ---------- | ------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| routeLabel | string | `name`  | The label to use for the route transactions. Can be either `name` or `path`. When this is `name`, the transaction will use `route.name`, if it is set, and else use the path of the route. By setting this to `path` you can opt-out of this and always use the path. |
