# Bugsink / Sentry — monorepo subdirectory deploys (Coolify and similar)

Optional page — only relevant if orientation turns up the shape of problem
described below. Skip it for a normal single-repo deploy.

Part of the `bugsink-setup` skill — see `../SKILL.md` for the shared
environment-variable list and verification checklist. This page is a
cross-cutting addendum to the "Release/build metadata" step in
`python.md`/`rust.md`, not a stack of its own.

## The shape of the problem

Platforms like Coolify let you deploy one subdirectory of a monorepo as its
own service, via a "Base Directory" (or equivalent) setting. That's often
exactly what you want — the service builds and deploys independently of the
rest of the repo. But it quietly changes what "the build context" means: a
Docker build rooted at `<repo>/<subproject>/` simply cannot see anything
above `<subproject>/`, including `.git` at the repo root. This isn't a "hard
to reach, but possible with an extra build stage" situation — `.git` is
outside the context entirely, so no stage inside that Dockerfile can reach
it, full stop.

The `python.md`/`rust.md` guidance to "bake `git-commit.txt` in a stage that
still has access to `.git`" assumes you *can* get `.git` into some stage.
Under a subdirectory Base Directory, that assumption doesn't hold — don't
spend time trying to widen the build context to smuggle `.git` in (risky to
experiment with on a live deploy pipeline, and usually fighting the
platform's intended scoping). There's a fix below that doesn't need `.git`
at all.

## Detect this during "orient yourself"

Two independent signals, both worth checking even if only one is obviously
true:

- **Does this project already have its own git-info generation convention?**
  Some codebases have a script that writes a commit-hash file before
  building — names vary (`gitinfo_values.py`, `version.py`,
  `build_info.json`, ...). Check whether that generated file is gitignored.
  If it is, and the deploy platform builds straight from a git clone (rather
  than a locally-prepared working directory where a dev already ran the
  generator), the file will never exist in the image. Importing it directly
  at module load time is a real crash risk, not a hypothetical one: exactly
  this — a bare `from gitinfo_values import ...` — killed every worker
  process in production with `ModuleNotFoundError` for one project,
  discovered only by reading the prod logs after the fact. If such a
  mechanism exists, wrap the import in a guarded fallback (try/except, or an
  `Option`-returning helper) — never a bare import — so a missing file
  degrades to "unknown" instead of crashing the process.
- **Does the deploy platform scope the build to a subdirectory of a bigger
  repo?** A "Base Directory" pointing at `<subproject>/` inside a monorepo is
  the telltale sign `.git` won't be reachable, regardless of whether the
  project has its own git-info convention.

If both are true, treat the fallback chain in `python.md`/`rust.md` as
required, not optional.

## The fix that doesn't need `.git`

Most platforms that build from a git clone still know the commit being
deployed — they just don't expose `.git` itself to a build scoped to a
subdirectory. They pass it along some other channel instead. Coolify, for
instance, automatically passes a `SOURCE_COMMIT` build ARG to `docker build`
— no configuration needed, confirmed working across multiple subprojects in
one monorepo, and it works precisely *because* it's independent of what the
build context contains.

```dockerfile
ARG SOURCE_COMMIT=""
ARG GIT_BRANCH=""
RUN echo "${SOURCE_COMMIT:-unknown}" > /app/git-commit.txt \
    && echo "${GIT_BRANCH:-unknown}" > /app/git-branch.txt \
    && date -u +%Y-%m-%dT%H:%M:%SZ > /app/build-time.txt
```

Pass it through from `docker-compose.yml` (this is the part that's easy to
forget — the build ARG has to be threaded through explicitly, Coolify
doesn't inject it into the container automatically):

```yaml
build:
  context: .
  args:
    SOURCE_COMMIT: ${SOURCE_COMMIT:-}
    GIT_BRANCH: ${GIT_BRANCH:-}
```

This slots into the exact same fallback chain the language guides already
describe — `git-commit.txt` is the same link, "baked into the image at build
time," just built without needing `.git` in the context. If the project also
has its own pre-existing git-info mechanism (per the detection step above),
treat it as the *first*, most-authoritative link in the chain rather than
replacing it with this — it's usually richer (dirty-tree flags, tags, full
commit message, ...) on the deploy paths where it *is* available, and this
fix only needs to cover the paths where it isn't.

## Worked resolution order

Putting it together, for a project with both an existing git-info
convention and this deploy shape:

1. The existing project git-info mechanism, if importable (guarded, never a
   bare import).
2. `SOURCE_COMMIT`/`GIT_BRANCH` env vars — some platforms expose these to
   the running container too, not just as build args; cheap to check first
   in case that's true here.
3. A live `git rev-parse HEAD` (safely wrapped — helps when running the app
   directly on a dev machine that does have `.git`, and is a no-op harmless
   failure everywhere else).
4. The baked `git-commit.txt`/`git-branch.txt`/`build-time.txt` files — what
   actually resolves it under the platform's scoped build.
5. `None`/`"unknown"`, so release-tagging code never crashes even when every
   other source is unavailable.

Verify by deliberately checking each link once wired up: does the release
tag in Bugsink actually show a real commit hash after a deploy through the
platform, not just "unknown"? An "unknown" release tag that nobody notices
is the quiet way this kind of fix silently doesn't work.
