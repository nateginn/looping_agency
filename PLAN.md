# Plan: Complete Phase 2 Step 6 — first real `art` SEO loop run
_Round 1 — revised after Codex critique (see PLAN-REVIEW-LOG-step6.md)_

## Goal

`HANDOFF.md` names Step 6 ("live smoke test against `art`, then its first real run") as the only remaining Phase 2 kickoff step for this workspace's first real client project (`art` / acceleratedrehabtherapy.com, SEO loop, propose-only). A retry of `tools/smoke_test.py art` just succeeded with 177 real GSC rows (vs. 0 rows on 2026-07-16, when Search Console was still processing). This plan executes the first real `run_loop.py art seo` run with actual data, reconciles now-stale tracking docs, and hands off cleanly to the human for `/review-pending` — without touching `D:\Dev\artwebsite` or attempting any apply.

## Approach

1. **Pre-run sanity check** — confirm no stale `run.lock` in `projects/art/loops/seo/` (none currently exists) and `state.json` is `{"status": "active"}` (confirmed clean). No credential re-check needed — the smoke test moments ago already proved `art-gsc-readonly` resolves and authenticates.

2. **Explicit human-acceptance checkpoint** — per `HANDOFF.md` Step 6.1 / `PHASE2_READINESS_CHECKLIST.md` §5, the live smoke-test result must be reviewed and accepted by the human before `run_loop.py` executes. Do not treat proceeding-to-run as implied by the smoke test having succeeded technically — present the smoke-test result (177 rows, auth OK) and get explicit go-ahead before Step 3, distinct from final sign-off on this plan as a whole.

3. **Run the loop for real**: `./.venv/Scripts/python.exe tools/run_loop.py art seo`. This pulls live GSC data for the current 28-day window, evaluates any prior `applied` proposals (none exist — `pending/` is empty), and picks up to 3 new draft proposals from top-clicks keywords with `3 < position <= 20`, cycling through `title-tag-rewrite` / `meta-description-rewrite` / `internal-link-addition`. Writes `runs/<run-id>/{snapshot.json,run.json,report.md}` and appends one `memory.md` line.

4. **Post-run validation** — before summarizing or committing anything, read `run.json` and `snapshot.json` directly (not just `report.md`): confirm each `tool_calls` entry has `ok: true`, note the actual `sample_size`/keyword row count, and confirm every write landed under `projects/art/loops/seo/` (no path outside the project namespace). This is the source of truth for whether the run is trustworthy, not proposal count alone.

5. **Summarize the result for the human**, branching on the validated data from Step 4, not on `proposals_created == 0` alone:
   - `ok` with proposals → list each proposal's page/keyword/action type. **Check for duplicate target pages** among the drafts (`_pick_new_actions` selects by keyword, not deduped by page, so two of the up-to-3 proposals can legitimately target the same URL) and flag any duplicates explicitly rather than presenting "3 proposals" as automatically clean. Note all are `manual_approval_only` Tier 1 with no automated apply path; `/review-pending art seo` is the next human step (out of scope for this plan).
   - `ok` with 0 proposals → check `snapshot.json`'s actual row/sample-size data first. If GSC returned real rows but none fell in the `3 < position <= 20` picking window, that's a legitimate "no strivable keywords yet" outcome — report it as such. Only flag a possible property-scope/permissions problem if the row count/sample size itself is still ~0.
   - Any non-`ok` status → stop and report the reason; no blind retry

6. **Reconcile stale tracking docs** against actual disk state (both the 2026-07-16 zero-row run, which already happened but was never committed, and today's real-data run) — only after Step 4's validation confirms the new run is trustworthy:
   - `HANDOFF.md` — update Step 6 status; it currently says the live smoke test "has not started," which is inaccurate
   - `PHASE2_READINESS_CHECKLIST.md` §5 — flip smoke-test items to `[x]`
   - `RISK-REGISTER.md` — add the Step 6.3 note (Phase 2 kickoff authorized 2026-07-16; smoke test + first real run completed; dates for both runs)

7. **Fix stale rollback text** in `projects/art/loops/seo/instructions.md` — it still describes a generic "revert PR" path, which `spec.md` explicitly overrode (a prior Codex-review fix, `acc0279`) with "no automated apply/PR path exists — Nate applies by hand."

8. **Repo-cleanliness check before commit** — run `git status --short` and review it explicitly; stage only the expected Step 6 files (`runs/`, `state.json`, `memory.md`, `HANDOFF.md`, `PHASE2_READINESS_CHECKLIST.md`, `RISK-REGISTER.md`, `instructions.md`) so a dirty worktree can't bundle unrelated changes into this closeout.

9. **Commit, then push with an explicit success check** — commit the staged files, then push to `origin/master`. Push success is part of the completion condition, not an assumed side effect: if push fails (auth/network), stop and report the local commit hash — do not represent Step 6 as fully reconciled/closed until the push is confirmed to have landed (consistent with existing repo pattern and the user's standing instruction to push on future commits).

## Key decisions & tradeoffs

- **Keep the 2026-07-16 zero-row run as a legitimate history entry rather than discarding/redoing it.** It was a real, valid execution that simply predated available data. Alternative considered: treat it as a failed/void attempt and scrub it from `memory.md` — rejected, since `memory.md` is documented as an append-only derived view and `runs/` is the system of record; rewriting history there would violate that contract.
- **Bundle doc reconciliation (HANDOFF/CHECKLIST/RISK-REGISTER) into this same session rather than deferring it.** Alternative: just run the loop and leave doc staleness for later — rejected, because `HANDOFF.md` is explicitly the "resume here" document for future sessions, and leaving it stale risks a future session re-attempting or misunderstanding Step 6.
- **No `/review-pending` or `apply.py` action in this plan.** Proposal approval is an explicit human judgment call (the `review-pending` skill uses `AskUserQuestion` per proposal, never infers from silence) and `apply.py` is not reachable for `art` regardless (`propose-only` + every `allowed_action` is `manual_approval_only: true`).
- **One commit covering both the run artifacts and the doc fixes**, rather than splitting into multiple commits — the run and the doc reconciliation are one coherent unit of work (closing out Step 6), and splitting them risks landing docs that reference run artifacts not yet committed.

## Risks / open questions

- Whether a fresh run today will actually return non-zero proposals, or GSC data exists but no keywords fall in the `3 < position <= 20` picking window (a legitimate "ok, 0 proposals" outcome distinct from a data-pull problem).
- Whether the `run-loop` skill's own doc text (still describing a Phase-1-only boundary that contradicts art's already-wired real connector) should be corrected as part of this pass or left for a separate cleanup — currently leaning toward flagging it but not fixing it here, since it's tooling-guidance drift rather than a run-blocking issue.
- Whether reconciling three separate tracking docs in one pass risks scope creep beyond "run the loop" — leaning toward yes-include-it, since `HANDOFF.md` staleness could otherwise mislead the very next session.

## Out of scope

- `/review-pending art seo` and any approve/reject/hold decision on proposals
- `tools/apply.py` — not reachable for `art` regardless of outcome
- Any push to `D:\Dev\artwebsite` (Tier 2, human-only, always — R6)
- Enabling `dataforseo`, Task Scheduler registration, content-social/ads loop work — all explicitly deferred per `HANDOFF.md`
