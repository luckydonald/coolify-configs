---
name: feedback-commit-prefix-ssp-tag
description: "Use \"[base] [ssp] \" prefix (not just \"[base] \") for commits in the git branch-split feature work in luckydonald/base."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5f38f4ba-0ce0-4e78-b9e6-2e81d245a371
---

For commits related to the clean/unclean/history branch-split feature (`scripts/°base/git/°split_lib/`, `get-base.py`, etc.) in the `luckydonald/base` repo, use the commit summary prefix `[base] [ssp] ` instead of just `[base] `.

**Why:** The user explicitly asked for this tag instead of a bare `[base]` prefix. They'd also already renamed prior commits in this area to use `[ssp]` via their own interactive rebase before asking, so this is a standing convention for this feature's history, not a one-off.

**How to apply:** When following the lplp commit-with-lplp-style convention (`[where] topic: ai: Run: ...`), for this specific feature area use `[base] [ssp] topic: ai: Run: ...` as the `[where]` part. Applies to the git-split/branch-split feature specifically; unrelated `[base]`-scoped work (e.g. dumper, ai settings sync) is unaffected unless told otherwise.
