# Contributing a one-click service template to Coolify

Different goal than deploying your own app: this is about getting a compose
file merged into Coolify's own template catalog (coollabsio/coolify repo).
The source project needs 1,000+ GitHub stars to qualify.

## 1. Metadata header

At the top of the template's compose file:

```yaml
# documentation: https://docs.example.com/
# slogan: A brief description of your service
# category: One word, broad app type
# tags: tag1,tag2,tag3
# logo: svgs/your-service.svg
# port: 1234
```

Always set `port` — Coolify's Caddy proxy can't auto-detect it for templates.

## 2. Compose body

Use Coolify's magic env vars (see `references/magic-and-predefined-vars.md`)
and mark critical vars required with `:?`:

```yaml
services:
  app:
    image: your-service:latest
    environment:
      - DATABASE_URL=${DATABASE_URL:?}
      - API_KEY=${API_KEY:?}
      - PORT=${PORT:?8080}
      - DEBUG=${DEBUG:-false}
    volumes:
      - ${COOLIFY_VOLUME_APP}:/data
```

## 3. Logo

SVG strongly preferred (WebP/JPG only as a last resort). Filename must match
the compose service name exactly, placed in the `svgs/` folder of the Coolify
repo — e.g. service `wordpress` → `svgs/wordpress.svg`.

## 4. Test locally

Use the **Docker Compose Empty** deployment option in Coolify — it mimics the
one-click flow without needing the PR merged first.

## 5. Open the PR

- Add the `.yaml` under `/templates/compose` in `coollabsio/coolify`
- Include the logo under `svgs/`
- Coolify consumes a parsed version of templates from
  `templates/service-templates.json`

## Also documenting it in the Coolify docs (separate repo/PR)

Target the `coollabsio/coolify-docs` repo, `next` branch:

1. Logo under `/docs/public/images/services/` — base name matching the
   service slug (`my-service.svg`, or `-logo`/`_logo` suffix variants; the
   resolver tries several naming patterns before falling back).
2. New file `/docs/services/<slug>.md` (slug lowercase, kebab-case, matches filename):
   ```yaml
   ---
   title: "Service Name"
   description: "Short description for the card and search results."
   category: "Analytics"
   ---
   ```
   `og.description` (optional, longer SEO text) and `disabled: true` (hide
   from listing while keeping the page reachable) are also supported.
3. Body content — brief "what is this" + links section, following the
   existing docs' template.
4. The services overview page and `/services/all` are generated automatically
   from these frontmatter files by `scripts/generate-service-list.mjs` and
   `scripts/generate-services-page.mjs` (run automatically by `bun run dev` /
   `build` / `preview`, or manually via `bun run generate:services`) — don't
   hand-edit `List.vue` or `all.md`.
5. PR against `next`, verified locally with `bun run dev`.

If the service hasn't been requested yet, search
https://github.com/coollabsio/coolify/discussions/categories/service-template-requests
first and open/upvote a request there too.
