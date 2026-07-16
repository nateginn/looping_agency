# SEO loop — operating instructions

This loop follows the run contract in the root `CLAUDE.md` verbatim. Nothing here overrides it; this file only adds SEO-specific judgment notes for whichever agent runs `/run-loop <project> seo`.

## What this loop does each run

1. Runs `python tools/run_loop.py <project> seo` (the deterministic engine: lock, spec validation, metrics pull, snapshot, experiment evaluation, proposal generation, run.json/report.md, memory append).
2. Reads the resulting `runs/<run-id>/report.md` and summarizes it for the human in plain language — what won, what's still cooling down, what's stale, what (if anything) is paused on a breach.
3. Never calls `tools/apply.py` itself. Applying an approved proposal is always a separate, explicit human-directed step via `/review-pending`.

## Judgment notes specific to SEO

- Prefer proposals on pages with real traffic (nonzero clicks) over purely theoretical position gains — a position-6 page with meaningful clicks outranks a position-9 page with almost none.
- If a page has been touched by another action still inside its observation window, it will already be excluded by the cooldown rule in `run_loop.py` — do not propose around that exclusion.
- If the loop is `paused-breach`, do not propose workarounds; summarize the breach for the human and point at `/review-pending --resolve-breach`.

## Rollback

Every SEO action type in `spec.md` reverts via "revert PR" — i.e. the Tier-1 `applied` transition for this loop is a PR branch, and reverting means closing/reverting that PR. No SEO action type in this template lacks a clean rollback; if you add one that doesn't, mark it `manual_approval_only: true` in the spec.
