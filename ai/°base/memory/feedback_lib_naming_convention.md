---
name: feedback_lib_naming_convention
description: "In luckydonald/base, put new shared/cross-file helper logic in a `°name_lib` package (like `°reffiles_lib`, `°split_lib`), not in the flat `_lib.py` files — and give that package's public functions plain names, no leading underscore."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: bd057432-52a3-4fad-8478-454be4c9c79e
---

When adding shared logic that multiple scripts/hooks need to call, create a new namespaced package directory `°name_lib/` (with `__init__.py` re-exporting the public API), imported via `importlib.import_module("°name_lib")` — mirroring the existing `°reffiles_lib`, `°split_lib`, `°settings_lib`, `°dllink_lib` packages. Do not add new functions to the flat `_lib.py` files (e.g. `scripts/°base/ai/hooks/_lib.py`) — those are for their original narrow purpose (prompt-log hooks), not a general dumping ground.

**Why:** Corrected during the memory-deletion-sync fix (`ai/°base/plans/` work on `record-memory/hook.py` + `°memory_lib`) — the initial plan proposed adding functions to `_lib.py`; the user redirected to the `°*_lib` package convention instead, since that's the established pattern for genuinely shared/reusable logic in this repo.

**How to apply:** Within a new `°name_lib` package, name public functions without a leading underscore (e.g. `delete_memory`, `is_tracked`) — matching `°reffiles_lib`'s `handle_referenced_files`/`is_tracked` style. Leading-underscore names stay fine for a *file's own local/private* helpers (e.g. `hook.py`'s existing `_sync_file`, `_commit`) — the no-underscore rule is specifically for the shared package's public API surface.
