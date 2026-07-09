# Download References Script And UV Hook Fix

## Summary
- Add `scripts/°base/ai/references/download-link.py` as an executable `uv` script for downloading documentation into `ai/references`.
- Fix the `PostToolUse` decision hook failure by running `save-decision/hook.py` through `uv`, preserving its `pydantic` model implementation.
- Keep normal tests deterministic with mocked HTTP, and add opt-in live smoke tests.

## Key Changes
- `download-link.py`:
  - Use a `uv` script shebang with inline dependency metadata, using `markdownify` for HTML-to-Markdown conversion.
  - Accept URL from `argv[1]`, piped stdin, or an interactive prompt.
  - If stdin is non-TTY and empty, exit nonzero with usage examples.
  - Default output root is `ai/references`; treat `ai/resources` in the examples as typos.
  - Add `--output-root PATH` for tests and alternate local use.
  - Print resolved download URL and written output path.
- URL handling:
  - Store under `{output_root}/{scheme}/{host}/{path...}`, with `https`, not `https:`.
  - Direct `.md` URLs write to their URL path.
  - Non-`.md` URLs try Markdown candidates first, then convert original HTML into `{original_path}/_.md`.
  - Read the Docs HTML pages use footer revision when present, writing `{original_path}/{revision}.md`.
- Git forge handling:
  - Implement host adapters for GitHub, GitLab/self-hosted GitLab, Bitbucket, Gitea/Forgejo/Codeberg, SourceHut, SourceForge, Launchpad, AWS CodeCommit, and best-effort Radicle/self-hosted patterns.
  - Resolve mutable branch/tag blob URLs to commit permalinks before choosing the output path.
  - Use raw file URLs for actual download; GitHub uses `raw.githubusercontent.com`.
  - Fail with an actionable unsupported-pattern error when a forge URL cannot safely map to a raw file.
- Hook fix:
  - Keep `pydantic` in `scripts/°base/ai/hooks/save-decision/hook.py`.
  - Change the generated/shared PostToolUse `save-decision` hook command to invoke `uv run --project "$(git rev-parse --show-toplevel)/scripts/°base" python "$(git rev-parse --show-toplevel)/scripts/°base/ai/hooks/save-decision/hook.py" <tool>`.
  - Update `ai/tool-settings/settings.json`, regenerate `.claude/settings.json` and `.codex/hooks.json`, and cover rendering in sync tests.
  - Do not change unrelated hooks unless tests show the same dependency issue there.

## Test Plan
- Mocked download-link unit tests:
  - Input from arg, stdin, prompt, and empty non-TTY failure.
  - Direct `.md`, Markdown fallback, HTML fallback, fragments, and Read the Docs revision naming.
  - Provided examples using `ai/references`, including GitHub branch-to-commit path and raw download URL.
  - GitHub branch links, commit permalinks, and branch names containing slashes.
  - Forge adapter parsing for the listed hosters and explicit unsupported-pattern errors.
- Hook regression tests:
  - Existing `save-decision` parsing/rendering tests still pass under `uv run --project scripts/°base`.
  - Add/verify settings-sync tests assert the PostToolUse decision hook command uses `uv run --project scripts/°base`.
  - Simulate a Codex `request_user_input` PostToolUse payload through the rendered command shape enough to prove `pydantic` imports from the uv environment.
- Commands:
  - `uv run --project scripts/°base python -m unittest ai.scripts.tests.test_download_link -v`
  - `uv run --project scripts/°base python -m unittest scripts/°base/tests/test_save_decision.py scripts/°base/tests/test_save_decision_codex.py -v`
  - `uv run --project scripts/°base python -m unittest scripts/°base/tests/test_ai_settings_sync.py -v`
  - `uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v`
  - Optional live: `DOWNLOAD_LINK_LIVE=1 uv run --project scripts/°base python -m unittest ai.scripts.tests.test_download_link_live -v`

## Assumptions
- Public documentation URLs are the primary use case; private repo tokens are not added in this pass.
- Existing unrelated worktree files are left untouched.
- Existing downloaded references may be overwritten at the same path.
- Live tests are opt-in to avoid network-dependent default failures.
