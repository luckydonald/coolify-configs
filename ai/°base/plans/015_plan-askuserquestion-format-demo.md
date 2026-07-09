# Plan: AskUserQuestion Format Demo

## Context
User wanted to see all available AskUserQuestion formats for AI hook testing purposes — no code changes needed.

## Formats demonstrated

| Format | Question used |
|--------|--------------|
| Single select, no preview | Python version target |
| Multi select, no preview | Testing libraries |
| Single select, with code previews | Error surfacing style |
| Multi select, no preview | Codebase scope |

## Summary of format options

- **Single select** — one answer; `multiSelect: false`
- **Multi select** — many answers; `multiSelect: true`
- **Preview pane** — code/mockup shown on focus; only works with single select
- **Option descriptions** — subtitle under each label; works on all formats
- **"Other" free-text** — always injected automatically by the UI

## Verification
No code changes; verified visually by the question prompts appearing and the user responding.
