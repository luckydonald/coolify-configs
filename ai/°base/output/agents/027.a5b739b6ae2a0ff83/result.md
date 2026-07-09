Response written to `/home/user/git/luckydonald/base/ai/skills/coolify-compose-deploy-workspace/iteration-1/migration-healthcheck/with_skill/outputs/response.md`.

Final answer text:

---

Yeah, that's almost certainly it. Coolify rolls up the health of every service in the stack to decide if the whole deployment is "healthy." A one-shot migration container has no long-running process, so once it exits (even with code 0), Coolify sees a stopped/exited container and counts that against the stack's overall health — even though nothing is actually wrong.

The fix is to tell Coolify to ignore that specific service when computing stack health, using the Coolify-specific `exclude_from_hc` key on the migration service in your `docker-compose.coolify.yml`:

```yaml
services:
  migrate:
    image: your-app:latest
    command: ["./manage.py", "migrate"]   # or your migration tool of choice
    exclude_from_hc: true                 # <-- don't count this one-shot job in stack health

  app:
    image: your-app:latest
    depends_on:
      - migrate
    # ...
```

Notes:
- `exclude_from_hc: true` is a Coolify-only compose extension — it's ignored by plain `docker compose` elsewhere, so it's safe to keep in a compose file you also use for local dev/CI.
- This only stops the migration container's exit status from being counted toward the stack's health rollup. It doesn't change how/when the container runs — if you need the app to actually wait for migrations to finish first, keep (or add) `depends_on`, and consider a `condition: service_completed_successfully` on that `depends_on` entry so `app` doesn't start until `migrate` has exited cleanly.
- Apply the same flag to any other one-shot jobs in the stack (seed scripts, cache warmers, etc.) — the same "exits normally but tanks stack health" issue applies to all of them, not just migrations.