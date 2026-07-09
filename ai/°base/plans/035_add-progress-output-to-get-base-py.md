# Add Progress Output To `get-base.py`

## Summary

Make the standalone launcher visibly report its bootstrap progress, including when used through the documented curl-pipe command. Keep all launcher status on stderr, flushed immediately, so it is visible during long git operations and does not interfere with normal stdout behavior.

## Key Changes

- Add a small `status()` helper in `scripts/°base/git/get-base.py`:
  - Format messages as `get-base.py: ...`
  - Print to `sys.stderr`
  - Use `flush=True`

- Emit status around each setup phase:
  - Resolve repo root: report the target repo path
  - Base remote: report whether it is being added or already exists, including the URL
  - Fetch: report `fetching base/base`
  - Worktree: report whether `.git/base-tools` is being created or refreshed
  - Auto mode: when no args are passed, report the current branch and selected delegated command
  - Delegation: report the final `split.py` command before `os.execvp`

- Keep command behavior unchanged:
  - Continue using captured output where the script needs to inspect git output
  - Continue respecting an existing `base` remote URL
  - Continue delegating via `os.execvp`
  - Continue not touching the current checkout

- Update documented curl examples in the script docstring and `README.md` from `curl -fsSL ...` to `curl -fSL ...` so curl itself shows transfer progress before Python starts. The script then shows setup progress after the download finishes.

## Tests

- Extend `scripts/°base/tests/test_get_base.py` to capture stderr around `main()` with mocked `os.execvp`.
- Assert the stderr includes the meaningful progress sequence, at minimum:
  - repo root
  - base remote state
  - fetch step
  - worktree create/refresh step
  - delegated command
- Add/adjust an auto-mode assertion to confirm the selected command is reported when argv is empty.
- Keep existing behavior tests for remote preservation, worktree refresh, auto argv selection, and checkout untouched.

## Verification

- Run:
  ```bash
  uv run --project scripts/°base python -m unittest scripts/°base/tests/test_get_base.py -v
  ```
- Then run the full suite:
  ```bash
  uv run --project scripts/°base python -m unittest discover -s scripts/°base/tests -v
  ```

## Assumptions

- Status should be always-on for this launcher because its purpose is bootstrap visibility.
- Status belongs on stderr so both local invocation and `curl ... | python3 -` show progress without changing stdout contracts.
- It is acceptable that curl progress only appears after removing `-s`; no Python code can print status before curl has finished downloading the script.
