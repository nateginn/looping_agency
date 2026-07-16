# Handoff — read this first in a new session

Read this before touching anything else. It replaces needing to scroll a long prior chat transcript. Canonical docs are `AgentColabPlan.md` (design), `PLAN-REVIEW-LOG.md` (plan review history), `RISK-REGISTER.md` (findings + risk acceptances), `CLAUDE.md` (operating instructions) — read those next if you need depth.

## Status as of 2026-07-15 (uncommitted work in progress)

**Phase 1 is functionally built but NOT yet closed out.** The following files are edited on disk but **not committed** and **not re-verified** (the verification run was interrupted mid-session):

- `tools/apply.mjs` — now refuses a Tier-1 apply unless the loop's spec has `approval_mode: tier1-enabled` (fixes a real bug Codex found: propose-only loops could still apply approved Tier-1 proposals). Applied-markers now write to a new `applied/` dir instead of polluting `pending/`.
- `tools/run-loop.mjs` — lock staleness TTL now reads `max_run_duration_minutes` from the loop's own spec (clamped, best-effort) instead of a hardcoded 60 minutes (Codex found the hardcoded version could leave a stale lock blocking a run for up to an hour on a loop with a 5-minute TTL).
- `tools/review-pending.mjs` + `.claude/skills/review-pending/SKILL.md` — exposed a `--review` CLI flag so the full `draft → reviewed → approved` path is reachable (Codex flagged that `approve` could skip straight from `draft`; the plan's wording arguably permits the shortcut, so it's kept, but the fuller path is now available too).
- `projects/_demo/loops/seo/spec.md` — `approval_mode` changed to `tier1-enabled` (demo-only exception, documented inline in that file) so `_demo` can exercise `apply.mjs` end-to-end.
- `tools/tests/phase1-exit-criteria.mjs` — two new checks added and wired into `main()`: `testProposeOnlyRefusesTier1Apply`, `testStaleLockTtlComesFromSpec`.

**This came from one Codex read-only adversarial code-review pass** (gpt-5.4, sandbox read-only) that returned `VERDICT: REVISE` with 4 findings. All 4 have fixes above; **none of this is logged in `RISK-REGISTER.md` yet** (needs a new R7 row, same style as R3).

**`projects/_demo/loops/seo/`'s committed run history (`runs/`, `pending/*.json`) reflects the OLD pre-fix code** — it was generated before the marker relocation and before `_demo` was switched to `tier1-enabled`. It needs to be regenerated from a clean state so the two-consecutive-run proof is honest.

## Exact next steps (in order)

1. Run `node tools/tests/phase1-exit-criteria.mjs` — confirm all ~30 checks `PASS`. Fix anything that doesn't.
2. Reset `projects/_demo/loops/seo/{runs,pending,state.json,memory.md}` to clean, then redo the two-consecutive-run sequence: `node tools/run-loop.mjs _demo seo --scenario normal`, approve+apply one proposal via `tools/review-pending.mjs` / `tools/apply.mjs`, wait ~10s (the demo spec's observation window is ~9s), run again. Confirm run 2 shows a verified winner and `applied/*.marker.json` exists (not `pending/*.applied-marker.json`).
3. Add an **R7** row to `RISK-REGISTER.md`: all 4 Codex findings above, resolution = fixed (3) / kept-by-design-with-added-`--review`-option (1).
4. Commit everything.
5. **Push to GitHub**: `git remote add origin https://github.com/nateginn/looping_agency.git` (if not already set) then push. The user wants this repo public and wants every future commit pushed there (so Claude Cowork can read status remotely) — this is also saved in this project's memory (`github-remote.md`).
6. Once Phase 1 is genuinely closed: **Phase 2 infrastructure prep the user explicitly authorized without needing to be present** (still no real credentials, no real project, no live API calls):
   - Fill in the real GSC Search Analytics API call in `tools/gsc.mjs` and the real DataForSEO API call in `tools/dataforseo.mjs` — both must keep their existing safety gate (throw without an injected credential resolver), so this is safe to write now even though it can't run live yet.
   - Write a Phase 2 intake checklist (e.g. `templates/loops/seo/intake-checklist.md`) listing exactly what the user will need to supply when `/intake-project` runs for real (domain, repo path + whether it auto-deploys on push, goals, caps, credential aliases).
   - Write `tools/watchdog.mjs` — the out-of-band watchdog from `AgentColabPlan.md`'s "Scheduling & operations" section (checks a loop's expected run artifact exists for its cadence window). Tier 0, local-only, no live systems.
   - Note in `RISK-REGISTER.md`/`CLAUDE.md` that this connector-implementation work happened ahead of formal Phase 2 kickoff, per explicit user instruction dated 2026-07-15.
7. Update this file (`HANDOFF.md`) once the above is actually done, so it keeps reflecting reality rather than going stale.

## Hard boundaries (do not cross without the user present)

- No real project intake (`/intake-project` against an actual client).
- No real credentials — nothing in Windows Credential Manager, nothing live-tested against GSC/DataForSEO.
- Never touch the user's website repo (separate from this workspace, lives under `Dev/`) — it auto-deploys on push with no staging gate, so any push there is Tier 2/human-only (see `RISK-REGISTER.md` R6).
- Phase 2's actual kickoff (a real project's live SEO loop) requires the user's explicit go-ahead, per `CLAUDE.md`.

## Fast sanity checks

```
node tools/tests/phase1-exit-criteria.mjs   # should be all PASS
git log --oneline                            # see what's committed
git status                                   # should be clean once step 4 above is done
git remote -v                                # should show origin -> github.com/nateginn/looping_agency (after step 5)
```
