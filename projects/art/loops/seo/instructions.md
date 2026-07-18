# SEO loop — operating instructions

This loop follows the run contract in the root `CLAUDE.md` verbatim. Nothing here overrides it; this file only adds SEO-specific judgment notes for whichever agent runs `/run-loop <project> seo`.

## What this loop does each run

1. Runs `./.venv/Scripts/python.exe tools/run_loop.py <project> seo` (the deterministic engine: lock, spec validation, metrics pull, snapshot, experiment evaluation, proposal generation, run.json/report.md, memory append).
2. Reads the resulting `runs/<run-id>/report.md` and summarizes it for the human in plain language — what won, what's still cooling down, what's stale, what (if anything) is paused on a breach.
3. Never calls `tools/apply.py` itself. Applying an approved proposal is always a separate, explicit human-directed step via `/review-pending`.

## Judgment notes specific to SEO

- Prefer proposals on pages with real traffic (nonzero clicks) over purely theoretical position gains — a position-6 page with meaningful clicks outranks a position-9 page with almost none.
- If a page has been touched by another action still inside its observation window, it will already be excluded by the cooldown rule in `run_loop.py` — do not propose around that exclusion.
- If the loop is `paused-breach`, do not propose workarounds; summarize the breach for the human and point at `/review-pending --resolve-breach`.

## Rollback

No automated apply/PR path exists for this project — `D:\Dev\artwebsite` has no staging gate and no loop tooling may push to it. Every `allowed_action` in `spec.md` is `manual_approval_only: true`; any live change and its reversal are made by Nate directly, by hand.
