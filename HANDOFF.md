# Handoff — read this first in a new session

Read this before touching anything else. It replaces needing to scroll a long prior chat transcript. Canonical docs are `AgentColabPlan.md` (design), `PLAN-REVIEW-LOG.md` (plan review history), `RISK-REGISTER.md` (findings + risk acceptances), `CLAUDE.md` (operating instructions), `PHASE2_READINESS_CHECKLIST.md` (Phase 2 go/no-go tracking) — read those next if you need depth.

## Resume here

**Phase 3 shipped 2026-07-21 (see "Phase 3" section below) — read that before anything else if you're picking this up fresh.** The auto-implement + verify-loop capability is built, tested (73/73), and pushed (`59f40f8`), but **not yet activated for `art`** — `art` is still `propose-only` with `manual_approval_only: true` on every action, unchanged. `art` also has **6 undecided draft proposals** sitting in `pending/` right now (two extra `run_loop.py art seo` runs on 2026-07-21, on top of the 3 from 2026-07-18 that were already reviewed and rejected — see R9/keyword_exclusions history below). Nate has not yet run `/review-pending art seo` on these 6. No Task Scheduler job exists yet either — `art seo` only runs when someone types the command by hand (`PHASE3-SCHEDULING.md` has the ready-to-run `schtasks` commands, deliberately never executed by tooling).

**Step 6 is done.** Steps 0–5 of the accepted Phase 2 execution plan were done, committed, and pushed to `origin/master` on 2026-07-16. Step 6 (live smoke test against `art`, then its first real run) completed 2026-07-18 — see "Step 6 — completed" below.

## Status as of 2026-07-16 (end of the Phase-2-onboarding session)

**Phase 1 is complete** (Python, 32/32 checks originally, since grown — see below). The Node→Python correction is documented in `RISK-REGISTER.md` R8. **This project is Python-only** — see `CLAUDE.md` "Implementation language" and the `language-choice-approval` memory: never introduce a non-Python language without the user's explicit prior approval.

**Phase 2 kickoff is underway.** The first real project, **`art`** (acceleratedrehabtherapy.com, Nate's own site), is fully onboarded: `projects/art/project.md` + `projects/art/loops/seo/spec.md` exist, validate clean, and are committed. Nate has a working, verified live GSC credential stored in Windows Credential Manager. Two independent Codex review rounds ran against this session's work (the Steps 1–3 connector build, and the `art` intake) — all findings from both were fixed. An AEO (Answer Engine Optimization) documentation scope addition also landed on top of the `art` onboarding.

### What happened since Step 0 (newest first)

- `db93fc7` — Documented AEO as a reviewer-guidance consideration for `art` (goals + priority-pages sections in `project.md`, a Notes bullet in `spec.md`). Docs only — no new metric/guardrail/connector; GSC/DataForSEO have no AI-answer-engine-citation signal to measure against.
- `acc0279` — Fixed 3 Codex findings from an independent review of the `art` intake: added a docs-only "Priority reference pages" section to `project.md` (review aid, not an enforced filter — the loop still proposes from all GSC data ranked by clicks); replaced misleading `rollback: revert PR` text in `spec.md` with an honest description (no PR path exists for this project) and marked all three `allowed_actions` `manual_approval_only: true`; tightened `project.md`'s repo-access wording (`D:\Dev\artwebsite` is never read/written by this loop's tooling).
- `92d7b64` — **Step 5**: onboarded `art` — `project.md`, `loops/seo/spec.md` (inputs `[gsc]` only, `site_url: sc-domain:acceleratedrehabtherapy.com`, weekly Monday cadence, default 5-position guardrail, no seed keyword list — discovers ranking pages from live GSC data), `memory.md`, `pending/`, `runs/` scaffolded. Validated clean.
- `0b7a54b` — **Step 4 prep**: Nate's GSC credential turned out to be a service-account JSON key (not a raw token), so added `tools/lib/gsc_auth.py` (mints short-lived access tokens from the JSON, `webmasters.readonly` scope only), transparent chunked storage in `tools/lib/credentials.py` (Windows Credential Manager caps a blob at ~1280 chars; the JSON is ~2.3k), `--store --from-file` CLI support, and `tools/lib/tls.py` (Windows-truststore HTTPS fix, R8). Pinned `google-auth`, `requests`, `truststore`.
- `87145bc` — Fixed 4 findings from an independent Codex review of the Step 1–3 connector build: (1) GSC returns `page` as a full URL while DataForSEO targets are paths — merge now normalizes both to the URL path component; (2) `spec_validate.py` now rejects unknown connector names in `inputs` pre-run (was previously only caught at run time); (3) GSC date ranges are endpoint-inclusive — fixed an off-by-one in the `metrics_window_days` window calculation; (4) hardened the `.env` ACL check to compare fully-qualified account names instead of a bare-username suffix match.
- `37e44e1` — **Step 3**: `tools/smoke_test.py` (connectors-only live check — resolves aliases, calls each connector once, redacted summary, bad-alias clean-failure simulation; writes nothing, takes no lock).
- `87138ad` — **Step 2**: wired `gsc.py`/`dataforseo.py` into `run_loop.py`'s `_fetch_metrics` dispatch; `spec_validate.py` gained conditional requirements per connector; GSC is the primary metrics source, DataForSEO only enriches matching rows with `serp_position`.
- `c768161` — **Step 1**: `tools/lib/credentials.py` — Windows Credential Manager (keyring) first, ACL-gated `.env` fallback second, `--store`/`--check`/`--verify`.
- `f6ca0fd` — **Step 0**: committed the outstanding Codex Rounds 3–4 review edits and `PHASE2_READINESS_CHECKLIST.md`.

**Current test suite: 49/49 exit-criteria checks, plus every module's `--verify` self-test, all green, no network calls.**

## THE USER HAS EXPLICITLY AUTHORIZED PHASE 2 KICKOFF (2026-07-16)

Decisions the user already made — do not re-ask, and note how each played out:

- **First real project: the user's (Nate's) own website.** → Onboarded as `art` (acceleratedrehabtherapy.com). Propose-only mode. Repo `D:\Dev\artwebsite` auto-deploys on push with no staging gate (Tier 2, human-only, R6) — this loop's tooling never reads or writes it.
- **Credential resolver library: `keyring`.** → Built (`tools/lib/credentials.py`), pinned in `requirements.txt`.
- **`.env` fallback is acceptable** with the mandatory restrictive-ACL check. → Built and hardened (fully-qualified-account-name comparison, not a suffix match).
- **He already has a working GSC credential.** → Turned out to be a **service-account JSON key**, not a raw token — `google-auth` added for token minting (see `0b7a54b` above).
- **Secrets never enter the chat transcript.** → Confirmed in practice: Nate stored the credential himself via `--store art-gsc-readonly --from-file <path>` in his own terminal, verified with `--check`, then deleted the source JSON file. No secret was ever pasted into the conversation.

## Exact next steps — the accepted Phase 2 execution plan

### Steps 0–5 — DONE (see commit list above)
Framework review closeout, credential resolver, real-connector wiring, connectors-only smoke test, service-account JSON support, and onboarding `art` are all committed and pushed. Do not re-plan or redo any of this.

### Step 6 — Live smoke test, then first run (COMPLETE — 2026-07-18)
1. `./.venv/Scripts/python.exe tools/smoke_test.py art` — first attempt 2026-07-16 returned HTTP 200 OK but 0 rows (Search Console still processing data for the newly-verified property); retried 2026-07-18 and returned 177 real rows, auth OK. Reviewed and accepted.
2. First real run: `./.venv/Scripts/python.exe tools/run_loop.py art seo` — also has two data points:
   - 2026-07-16T21:24:31.879Z (`run 2026-07-16T21-24-31-879Z-pc50bg`): `status: ok`, but `sample_size: 0`/0 keyword rows (GSC hadn't indexed data yet, same cause as the smoke test's 0-row result that day) → 0 proposals, a legitimate empty outcome, not a failure.
   - 2026-07-18T22:12:26.323Z (`run 2026-07-18T22-12-26-323Z-b8xtu9`): `status: ok`, `sample_size: 451`, ~187 keyword rows → **3 draft proposals created**: `title-tag-rewrite` and `meta-description-rewrite` on `http://acceleratedrehabtherapy.com/` (home page, two different low-traffic keywords), and `internal-link-addition` on `/auto-injury/`. All Tier 1, `manual_approval_only: true`, `pending/` review awaiting Nate.
   - Note for review: the first two proposals target the same page (home) via different action types — not a tooling conflict, but worth Nate's attention since GSC's http/https variants of the home page show up as separate rows and most candidate keywords in this window have 0 clicks (very early/sparse data), so the "top-clicks" ordering was effectively a tie-break among near-equal candidates this first cycle.
3. `PHASE2_READINESS_CHECKLIST.md` §5 flipped to `[x]`; `RISK-REGISTER.md` note added (Phase 2 kickoff explicitly user-authorized 2026-07-16; Step 6 completed 2026-07-18); this file updated; committed and pushed.

**Superseded — see "Phase 3" section below for current next-action.** (Historical note: the 3 proposals from the 2026-07-18 run were reviewed and rejected 2026-07-18 — see R9/keyword_exclusions in `RISK-REGISTER.md` and the `30cc05f` commit. They were built on "Accelerate Health" branded noise; a `keyword_exclusions` filter was added and set for `art` as a result.)

### Out of scope for this plan
Content-social/ads loops; Task Scheduler registration (commands documented in `PHASE3-SCHEDULING.md`, deliberately never executed by tooling); enabling `dataforseo` for `art` (deferred until the first GSC-only reports are reviewed); any AEO-specific connector/guardrail (deferred until a real AEO data source exists — today's AEO addition is reviewer guidance only). Auto-implement + push-to-live for `art` specifically is now a **capability** (Phase 3, see below) but not yet an **activated** one — that's a separate human decision, not implied by shipping the code.

## Phase 3 — auto-implement approved proposals, close the verify loop (COMPLETE, shipped 2026-07-21)

**What shipped:** once a human approves a Tier-1 title-tag-rewrite or meta-description-rewrite proposal (internal-link-addition stays manual — no safe automated edit strategy), `apply.py` now writes the change as a commit on an isolated local git branch inside `D:\Dev\artwebsite` (never pushes — `git worktree`, so Nate's own checkout is never touched). A new read-only GitHub compare-API check detects when Nate has pushed it live and promotes the proposal to `applied`, which unblocks `run_loop.py`'s previously-dead verification logic (`_evaluate_prior_experiments`) to actually measure before/after Google position. Full design history in `PLAN.md` + `PLAN-REVIEW-LOG.md` (4 rounds of adversarial Codex plan review, then a Codex build with 2 fix rounds, all independently re-verified — 73/73 tests, up from 54, plus 4 module `--verify` self-tests). Pushed as `59f40f8`.

**What did NOT change:** `art` is still `propose-only`, still `manual_approval_only: true` on every action. Nothing auto-implements for `art` today — the capability exists but two separate spec edits (flip `approval_mode: tier1-enabled`, remove `manual_approval_only` per action) are required to actually activate it, and that's explicitly a human decision, not bundled into this ship.

**Next action for Nate:**
1. `/review-pending art seo` — 6 undecided draft proposals are sitting in `pending/` (from the two 2026-07-21 runs), all still `draft`, none reviewed.
2. Separately: decide whether/when to activate Phase 3 auto-implement for `art` (the two spec edits above) — no rush, the capability isn't going anywhere.
3. Separately: decide whether to register the Task Scheduler jobs in `PHASE3-SCHEDULING.md` (weekly `art seo` run + daily watchdog) — currently nothing runs `art seo` except by hand.

### Verification bar
All module `--verify` self-tests + exit-criteria suite (49/49) through `.venv/Scripts/python.exe`, no network. Live smoke test reviewed by Nate before the first real run; first run produces valid redacted `run.json`/`report.md` with zero writes outside `projects/art/`.

## Hard boundaries (do not cross without the user present)

- **Never introduce a non-Python implementation language without the user's explicit prior approval** (R8 lesson; standing memory rule).
- **Never ask the user to paste a raw secret into chat**; never write one to any repo file. Aliases only.
- `D:\Dev\artwebsite`: pushing/merging/updating a remote-tracking branch is Tier 2/human-only, always (R6) — no exceptions. Since Phase 3 (2026-07-21), tooling *may* write a local, unpushed commit/branch there via an isolated `git worktree` for an approved, non-`manual_approval_only` proposal — but only once a spec has explicitly opted in. Never assume this is active for a project without checking its `spec.md`.
- `art` stays `propose-only` (every action `manual_approval_only: true`) until Nate explicitly changes that in `spec.md` — not implied by anything shipping in `tools/`.
- Don't attempt to work around a harness safety-classifier hard block — stop and hand it to the user.

## Fast sanity checks

```
./.venv/Scripts/python.exe tools/tests/phase1_exit_criteria.py   # all PASS, 73/73
./.venv/Scripts/python.exe tools/review_pending.py art seo --list  # 6 undecided draft proposals as of 2026-07-21
git log --oneline                                                  # see what's committed
git status                                                          # should be clean
git remote -v                                                       # origin -> github.com/nateginn/looping_agency
```
