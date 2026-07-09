---
name: "coolify-compose-deploy"
description: "Write and edit docker-compose.coolify.yml (or plain docker-compose.yml deployed via Coolify) for Coolify's Docker Compose deployment mode. Covers exposing services via domains vs ports, required/shared/magic environment variables, predefining storage (host directories, files with content), excluding services from healthchecks, connecting to predefined networks, and Raw Compose Deployment Traefik labels. Use this whenever the user is deploying an app to Coolify with Docker Compose, asks about SERVICE_URL_/SERVICE_FQDN_/SERVICE_PASSWORD_ magic variables, Coolify env var syntax like ${VAR:?}, or why a Coolify service isn't reachable/routable — even if they only say 'deploy this to Coolify' without mentioning compose explicitly."
---

# Coolify Docker Compose Deployments

Coolify treats your `docker-compose.yml` (often named `docker-compose.coolify.yml`)
as the **single source of truth**. Settings you'd normally click through in the
Coolify UI — env vars, storage, healthcheck exclusions — must be expressed in the
compose file itself; the UI mostly *reflects* what's in the file rather than the
other way around.

## Exposing services

Three ways a service in the stack becomes reachable, pick per-service:

1. **Domain (via Coolify's proxy, the common case).** Assign a domain in the
   Coolify UI. If the service listens on container port 80, `http://example.com`
   is enough. For any other port, put the port in the domain:
   `http://example.com:3000` — this only tells Coolify which container port to
   route to; the proxy still serves on the normal port (80/443) externally.
2. **Direct host port mapping — bypasses the proxy.** Use compose's `ports:`.
   This exposes the port on the host directly, outside Traefik, so no domain
   routing/TLS applies. Bind to `127.0.0.1` if it should stay host-local:
   ```yaml
   services:
     backend:
       image: your-backend:latest
       ports:
         - "127.0.0.1:3000:3000"   # omit the IP to bind all interfaces
   ```
   Careful reusing a dev compose file as-is for deployment — `ports:` here can
   accidentally expose services (e.g. a database) that were only meant to be
   reachable inside the stack's network.
3. **Private/internal — no `ports:`, no domain.** Not reachable from outside;
   other services in the same stack reach it by service name, e.g. `auth`
   calls `http://backend:3000`.

## Environment variables

Coolify auto-detects vars referenced with `${...}` in compose and surfaces them
as editable fields in its UI. A hardcoded value with no `$` still reaches the
container but stays invisible in the UI.

```yaml
services:
  myapp:
    environment:
      - HARDCODED=hello                          # passed through, not shown in UI
      - SOME_VAR=${SOME_VAR}                      # editable, uninitialized in UI
      - WITH_DEFAULT=${WITH_DEFAULT:-hello}       # editable, defaults to "hello"
      # Required: deploy is blocked (red border in UI) until these are set
      - DATABASE_URL=${DATABASE_URL:?}
      - PORT=${PORT:?3000}                        # required, but prefilled with 3000
```

Required-var validation (`:?`) happens before container creation, so a missing
required var fails fast instead of starting a half-configured container.

**Shared (team/project/environment) variables** aren't detected directly from
the compose file. Reference them from the Coolify UI side: create the shared
variable, then set the *value* of your app's own env var field to
`{{team.NAME}}`, `{{project.NAME}}`, or `{{environment.NAME}}` — the compose
file itself just declares an ordinary `${VAR}` that the UI value flows into.

### Magic environment variables

Coolify can generate values for you with `SERVICE_<TYPE>_<NAME>` syntax —
handy for passwords and inter-service URLs that must stay consistent across
redeploys without you managing secrets by hand:

```yaml
services:
  app:
    environment:
      - SERVICE_URL_APP                              # http://app-<uuid>.example.com
      - SERVICE_FQDN_APP                              # app-<uuid>.example.com
      - DATABASE_PASSWORD=${SERVICE_PASSWORD_DB}       # random password, reused everywhere referenced
      - SECRET_KEY=${SERVICE_BASE64_64_APP}            # 64-char random string
```

Full type list (URL/FQDN, USER, PASSWORD variants, BASE64/REALBASE64/HEX
variants) is in `references/magic-and-predefined-vars.md`. One gotcha worth
remembering inline: **service names with underscores can't take a port
suffix** in the variable name — use hyphens in the service name instead
(`SERVICE_URL_MY-SERVICE_3000`, not `SERVICE_URL_MY_SERVICE_3000`). Magic vars
in Git-sourced compose files need Coolify ≥ v4.0.0-beta.411.

For build-time vs runtime var flags, Docker Build Secrets, and the
`Multiline`/`Literal` UI checkboxes, see
`references/magic-and-predefined-vars.md` — these are UI-side settings, not
compose syntax, but they affect how a compose-declared var actually behaves.

## Storage

Compose volumes work as usual, plus two Coolify-only extensions on a `bind` mount:

```yaml
services:
  app:
    volumes:
      # Have Coolify create an empty host directory before first deploy
      - type: bind
        source: ./data
        target: /data
        is_directory: true

      # Have Coolify create a file with this exact content (supports env interpolation)
      - type: bind
        source: ./init.sql
        target: /docker-entrypoint-initdb.d/init.sql
        content: |
          ALTER USER app WITH PASSWORD '${DB_PASSWORD}';
```

A top-level `configs:` section is the standard-Compose alternative to the
`content:` bind-mount trick — use whichever reads more naturally for the file
in question.

## Excluding a service from healthchecks

One-shot containers (migrations, seed jobs) shouldn't count toward the stack's
overall health — otherwise the whole deployment can be reported unhealthy
after the job container exits normally:

```yaml
services:
  migrate:
    image: your-app:latest
    command: ["./manage.py", "migrate"]
    exclude_from_hc: true
```

## Connecting across stacks

Each compose stack normally gets its own isolated network. To let one stack
reach another stack's services (e.g. an app stack talking to a shared database
stack), enable **Connect to Predefined Network** on the stack. Trade-off:
Coolify renames services with a `-<resource-uuid>` suffix to avoid collisions,
which breaks plain-name Docker DNS — reference the other stack's service using
its suffixed name, not the bare service name from its own compose file.

## Raw Compose Deployment (advanced, opt-in)

Selecting the "Docker Compose" raw build pack disables most of Coolify's magic
(env var detection niceties still apply, but you're otherwise on your own for
routing). Coolify still injects `coolify.managed=true`,
`coolify.applicationId=<id>`, `coolify.type=application` labels automatically.
To route traffic through Coolify's Traefik proxy in this mode, you must add
the Traefik labels yourself:

```yaml
services:
  app:
    labels:
      - traefik.enable=true
      - "traefik.http.routers.app.rule=Host(`example.com`) && PathPrefix(`/`)"
      - traefik.http.routers.app.entryPoints=http
```

Reach for Raw Compose Deployment only when the normal domain-assignment flow
genuinely doesn't fit — it trades away most of the conveniences above.

## Aside: contributing a service template upstream

If the actual goal is submitting a one-click service template to the Coolify
project itself (not deploying your own app), that's a different, narrower
workflow — metadata header comments (`# documentation:`, `# slogan:`,
`# category:`, `# tags:`, `# logo:`, `# port:`), a 1,000+ GitHub-star
requirement for the source repo, and testing via the "Docker Compose Empty"
deploy option before opening a PR against coollabsio/coolify. Ask if this is
actually what's wanted before treating it as the main task — see
`references/service-template-contribution.md` for the full steps.
