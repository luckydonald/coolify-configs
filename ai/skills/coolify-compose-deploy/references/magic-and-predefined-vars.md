# Magic environment variables and env var behavior details

## Magic environment variable types

Syntax: `SERVICE_<TYPE>_<SERVICE_NAME>` (optionally `_<PORT>` for URL/FQDN, or
`=<path>` appended to add a path). Values are generated once, then reused
consistently across the stack and persisted across redeployments.

| Type | Generated value |
|---|---|
| `URL` | URL based on your wildcard domain |
| `URL_<PORT>` | URL with proxy routing to a specific port |
| `URL=/path` | URL with a path appended |
| `FQDN` | FQDN portion of the generated URL |
| `FQDN_<PORT>` | FQDN with proxy routing to a specific port |
| `USER` | Random string, 16 characters |
| `PASSWORD` | Random password without symbols |
| `PASSWORD_64` | Random password without symbols, 64 characters |
| `PASSWORDWITHSYMBOLS` | Random password with symbols |
| `PASSWORDWITHSYMBOLS_64` | Random password with symbols, 64 characters |
| `BASE64` / `BASE64_32` | Random string, 32 characters (not actually base64-encoded) |
| `BASE64_64` | Random string, 64 characters |
| `BASE64_128` | Random string, 128 characters |
| `REALBASE64` / `REALBASE64_32` | Base64-encoded random string, 32 chars |
| `REALBASE64_64` | Base64-encoded random string, 64 chars |
| `REALBASE64_128` | Base64-encoded random string, 128 chars |
| `HEX_32` | Hexadecimal random string, 32 characters |
| `HEX_64` | Hexadecimal random string, 64 characters |
| `HEX_128` | Hexadecimal random string, 128 characters |

Example, for an app with resource UUID `vgsco4o` on wildcard domain `example.com`:

```yaml
services:
  appwrite:
    environment:
      - SERVICE_URL_APPWRITE                 # http://appwrite-vgsco4o.example.com
      - SERVICE_URL_APPWRITE=/v1/realtime    # .../v1/realtime
      - SERVICE_URL_APPWRITE_3000            # proxied to port 3000
      - DOMAIN_NAME=${SERVICE_FQDN_APPWRITE} # appwrite-vgsco4o.example.com
      - SERVICE_SPECIFIC_PASSWORD=${SERVICE_PASSWORD_APPWRITE}
      - ENCRYPTION_KEY=${SERVICE_REALBASE64_64_APPWRITE}
  not-appwrite:
    environment:
      - APPWRITE_PASSWORD=${SERVICE_PASSWORD_APPWRITE}  # reuses the same password
      - SERVICE_URL_API=/api                            # distinct name -> new URL generated
```

Underscore-containing service names can't take a port suffix in the var name
— use hyphens instead:

```
SERVICE_URL_APPWRITE-SERVICE_3000   # correct
SERVICE_URL_APPWRITE_SERVICE_3000   # broken — ambiguous with a port suffix
```

Magic vars in compose files sourced from Git require Coolify v4.0.0-beta.411+.

## Predefined variables (available to reference in your own vars)

### Application-level

| Variable | Value |
|---|---|
| `COOLIFY_FQDN` | Fully qualified domain name(s) of the application |
| `COOLIFY_URL` | URL(s) of the application |
| `COOLIFY_BRANCH` | Branch name of the source code |
| `COOLIFY_RESOURCE_UUID` | Unique resource identifier |
| `COOLIFY_CONTAINER_NAME` | Generated container name |
| `SOURCE_COMMIT` | Commit hash of the source; excluded from builds by default to preserve cache — enable "Include Source Commit in Build" if you need it |
| `PORT` | Defaults to the first `Port Exposes` value if unset |
| `HOST` | Defaults to `0.0.0.0` if unset |

### Service stack

| Variable | Value |
|---|---|
| `SERVICE_NAME_<SERVICE>` | The service name as deployed — useful in preview deployments where the actual name varies |

## Build vs runtime variables

Every env var has two independent toggles, both on by default:

| Configuration | Build phase | Running container |
|---|---|---|
| Build + Runtime (default) | available | available |
| Build only | available | not available |
| Runtime only | not available | available |

- **Build variables**: for Dockerfile builds become `ARG`; for Compose/Nixpacks
  builds are passed via `--env-file`. Stored outside the build context
  (`/artifacts/build-time.env`), so they never end up baked into the image.
- **Runtime variables**: written to a `.env` file loaded via compose's
  `env_file` directive when the container starts.

If a var is only needed at runtime (e.g. an API key read on startup), turn off
`Build Variable` so it never touches the build phase at all.

## Docker Build Secrets

By default build vars go in as `--build-arg`, which get recorded in image
metadata (`docker history` can reveal them). Enabling **Use Docker Build
Secrets** (requires BuildKit / Docker 18.09+) switches to
`--secret id=KEY,env=KEY` instead — secrets are mounted only during the `RUN`
step that needs them and never land in a layer.

- For Dockerfile builds, Coolify auto-adds `# syntax=docker/dockerfile:1` and
  injects `--mount=type=secret` into `RUN` instructions — no manual Dockerfile edits needed.
- For Compose builds, Coolify adds a native `secrets:` section to the compose
  file instead of rewriting anything.
- Falls back to plain `--build-arg` if BuildKit isn't available on the build server.
- `COOLIFY_BUILD_SECRETS_HASH` is derived from all secret values, so build
  cache is preserved when secrets haven't changed and invalidated when they have.

## Multiline and Literal flags (Normal view only)

- **Multiline**: preserves newlines/special characters — needed for SSH keys,
  certs, multi-line config. Wrapped in single quotes at deploy time. Build-time
  multiline vars are passed as `ARG KEY` (no inline value) plus a separate
  `--build-arg`, to avoid breaking Dockerfile syntax.
- **Literal**: disables `$VAR`-style interpolation inside the value — needed
  when a value itself contains `$` that shouldn't be treated as a reference
  (passwords with `$`, regex like `^user\d+$`). Hidden when Multiline is on,
  since multiline values are always literal.

Both are Developer-view-invisible: in Developer view (plain `.env`-style
editor) a multiline var shows as
`KEY=(Multiline environment variable, edit in normal view)` and can only be
edited in Normal view; the same applies to locked secrets.
