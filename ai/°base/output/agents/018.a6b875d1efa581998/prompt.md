In /home/user/git/luckydonald/base, search the test suite (likely under a tests/ directory paralleling scripts/°base/git/°split_lib/) for any tests that:
1. Test `update_history_master()` behavior when it hits a conflict (status == "conflict") — check what shape of dict they assert on (do they check for a "message" key, or just "status"/"pending"?).
2. Test the CLI `_update_history_master` function or `cli.main()` for update-history-master — specifically anything asserting on stdout output format (e.g. checking printed text equals `str({...})` or checking for certain substrings).
3. Any existing `.expected.md`/`.expected.txt` fixture files in ai/°base/errors/ that pair with a conflict scenario, to see if there's a precedent for what "fixed" output should look like.
4. The `CherryPickConflict`/`MergeConflict` exception tests, if any, confirming their message text is tested.

Report file paths and relevant test code/assertions so I can safely modify _run_steps (in history_master.py) to preserve the conflict exception's message text, and modify cli.py's conflict-printing to be more helpful, without breaking existing tests.