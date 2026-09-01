# Current work — start here

_Last updated 2026-09-01. This is the live working record: what we're doing, what we decided,
and why. It supersedes the older `PLAN-*.md` files for anything current — those remain as the
historical design record. Read this first._

> **State at handoff:** A new Google Business Profile (GBP) Posts loop was built for `art`
> while the operator was away, per a design handed off in chat (mirroring the pattern of
> `kb/handoffs/looping-seo-ranking.md` from MMC). Phases 1–5 of that design are now built,
> tested, and **committed locally** (`6511628`, `bd01b22`, `d9a2e1e`, `b3d95a1`, `fe368da`,
> `a5af381` — **not pushed**, per decision 7). See "GBP Posts loop — Phases 1–4 built" and
> "GBP Posts loop — Phase 5 completed" below for full detail, what's still needed (Phases 6–7,
> gated on the operator's Google API access), and a punch list of what to check on return.
> Issue #1 and the two `run_loop.py` defects from the prior session remain closed and pushed as
> described further below; nothing about that work changed this session.

## GBP Posts loop — Phase 5 completed, 2026-09-01 (autonomous session, continued)

Phase 5 (approval-time target locking) is now fully built, hardened via **seven rounds** of
adversarial Codex review, and committed (`a5af381`). Everything below was found and fixed
across those rounds — recorded in full because several are exactly the kind of subtle,
easy-to-miss bugs this whole review discipline exists to catch, and a future reader should not
have to re-derive them from a diff.

**What Phase 5 actually does:** `tools/approve_gbp_post.py` resolves and locks the concrete
publish target (Google `accountId`/`locationId`/`place_id`, from `projects/art/loops/gbp/
locations.json` — every location in it is still `verified: false` today, since Phase 0 hasn't
happened) and commits a payload hash tying that lock to the exact drafted content, entirely
inside **one** lock acquisition. Approval reads a GBP proposal's current status AND its
implementation from the event-sourced projection (`event_log.sync_proposal_projection`), never
the cached JSON — the event log is this loop's sole commit point, a stricter model than the
pre-existing SEO/generic proposal machine (which keeps its own proven, unchanged crash-recovery
model with events as best-effort observability — see CLAUDE.md's Phase 7 section).

**What the seven review rounds found and fixed, in order:**
1. **First draft** was structurally wrong: it called `review_pending.decide()` as a black box
   (its own lock cycle) and only then acquired a *second* lock to append the GBP-specific
   lock event — a real race window where a concurrent draft/reject could land in the gap, or a
   crash between the two cycles could leave a proposal "approved" with no lock at all.
   Rewritten as a single atomic operation under one lock hold.
2. `place_id` was resolved but never locked onto the event; UNC Campus/Thornton were blocked
   only "by convention" in one function, not independently at load time too.
3. `review_pending.py` still let `decide(..., "approve")` **and** `"retry"` succeed for a
   `gbp-post-draft` proposal directly — a second, completely unlocked approval path that
   bypassed the whole mechanism. Now refused outright, routed to `approve_gbp_post.py`.
   Narrowing `approve_gbp_post.py`'s own allowed-from-statuses to exclude the (unused-for-GBP)
   Codex-review-subsystem states created a *dead end* (unapprovable AND unrescuable) — reverted
   to the full set once the alternate path was closed.
4. `_normalize_name()` (shared with `gbp_constraints.py`, reused for the forbidden-location
   check) didn't touch zero-width/invisible Unicode characters (ZWSP, ZWNJ, ZWJ, LRM, RLM,
   ARABIC LETTER MARK, WORD JOINER, BOM, soft hyphen) — `"UNC" + U+200B + "Campus"` slipped
   past untouched. Fixed by mapping them to an ordinary space (not deleting — deleting merged
   the words into `"unccampus"`, matching neither form) before the existing NFKC+casefold+
   whitespace-collapse pipeline. General Unicode **confusables** (e.g. Cyrillic "С" standing
   in for Latin "C") were explicitly *not* attempted — `locations.json` is operator-authored
   config, never external input, and the realistic failure mode is an accidental copy-paste
   character, not deliberate obfuscation; anyone able to hand-craft a homoglyph there could
   edit this Python file's own forbidden-name set just as easily. Documented inline as a
   deliberate scope boundary, matching this workspace's existing position on the Codex
   review-verdict mechanism (CLAUDE.md's Phase 7 section: a quality control, not a security
   boundary).
5. The two required event appends (`human_decision_recorded`, `gbp_proposal_approved`) weren't
   atomic as a *pair* — a crash between them could leave the event-sourced projection saying
   "approved" with no lock event ever existing, permanently. Fixed by making
   `gbp_proposal_approved` (which alone carries the status transition, hash, and locked IDs)
   the first and only *required* event; `human_decision_recorded` is now best-effort second,
   matching the pre-existing `review_pending._emit_human_decision` precedent elsewhere.
6. Reading status from the event-sourced projection closed one race but exposed a second: since
   `draft_gbp_post.py`'s revision path appends a `proposal_revised` event and *only then*
   rewrites the cache, a crash between those two steps could leave `draft_gbp_post.py` reading
   a stale cached "draft" status and allowing a fresh redraft with no event — silently
   diverging an already-approved lock's hash from the proposal's actual content once
   "review-pending" was restored to the approvable-from set (finding 3, above).
   `draft_gbp_post.py` now reads its own status gate from the same event-sourced projection.
   `approve_gbp_post.py` itself had the same class of bug in reverse: it correctly *checked*
   status via the projection but still *hashed* `implementation` from the cache — fixed to
   prefer the projection's implementation (populated from the latest `proposal_revised` event)
   over the cache, with an explicit `is not None` check (not a truthy `or`, which would have
   wrongly preferred a non-empty stale cache over a degenerate-but-valid empty `{}` from the
   event log) — and to actually write that resolved value back onto the cache, which an
   earlier round computed correctly for hashing but never assigned back before the final save.
7. `review_pending.py`'s generic `reject`/`review` transitions still wrote the cache *before*
   emitting the event, treating the event as best-effort for every action type — including a
   GBP proposal's reject, the one case where the whole design's safety property (a future
   publisher must trust the event log, not the cache) actually matters. Now branches: for a
   `gbp-post-draft` proposal, the event is emitted first and required (a failure aborts the
   whole decision, cache untouched); every other action type keeps its exact original,
   unchanged, already-tested behavior. `codex_review_proposal.py` (Phase 7's SEO-only review
   pipeline) had no action-type check at all and could mutate a GBP proposal's event-sourced
   status from a stale read if ever invoked on one by hand — now refuses any `gbp-post-draft`
   proposal outright at its single shared `_load_proposal()` entry point. A `load_location_
   mapping()` duplicate-name check was also extended to catch normalized (case/whitespace)
   variants, not just exact-string duplicates.

**Explicitly out of scope, confirmed by Codex as legitimate boundaries, not oversights:**
Unicode confusables (see point 4 above); `tools/lib/lock.py`'s stale-lock TTL-based forced
recovery (pre-existing, documented, codebase-wide crash-recovery design shared by every tool
in this workspace — CLAUDE.md's run-contract step 1 — not GBP-specific); the pre-existing
SEO/generic proposal machine's own cache-first/event-best-effort model for every action type
*other than* `gbp-post-draft` (CLAUDE.md explicitly documents it as keeping "its own proven
crash-recovery model" by design).

**Verification:** all directly-touched self-tests green (`approve_gbp_post.py` 32/32,
`gbp_publish_state.py` 27/27, `gbp_constraints.py` 43/43, `draft_gbp_post.py` 51/51,
`event_log.py` 27/27), the full `tools/tests/phase1_exit_criteria.py` regression suite holds at
225/225, and the real `projects/art/loops/gbp/locations.json` was directly exercised (no live
API calls, no cost) to confirm Greeley/Denver both still correctly refuse (`verified: false`)
and UNC Campus/Thornton both still correctly refuse (structurally forbidden) end to end.

**A test-hygiene bug was also found and fixed while verifying this**, unrelated to Phase 5's
own logic: `tools/tests/gbp_scaffold_smoke.py`'s cleanup used `shutil.rmtree(..., ignore_errors
=True)`, which silently fails to remove a directory containing `tools/snapshot.py`'s
deliberately-read-only `snapshot.json` on Windows — leaking one leftover `runs/<run-id>/`
directory per invocation into the real (gitignored, so `git status` never caught it)
`projects/art/loops/gbp/runs/`. Eight had accumulated from earlier phases' testing; the most
recent one's deliberately-poisoned `search_analytics` section (planted by the test itself to
prove the SEO-selector gate holds) was then getting carried forward into a brand-new
invocation's very first "ordinary case" check, causing an apparent regression. Fixed by
chmod'ing every file writable before `rmtree`; the leaked directories were deleted (100%
synthetic fake-credential test output, safe to remove, no git impact either way).

**Still not attempted, same reasons as before:** Phase 6 (`tools/publish_gbp_post.py` — the
only thing that would ever call Google's API) and Phase 7 (`measure_gbp_post.py`), both
blocked on Phase 0 (verified `place_id`/`cid` per location, a Google Cloud project with the
right APIs enabled, and the one-time human-verified pairing between DataForSEO's and Google's
identifiers) — none of which can be done without the operator.

---

## GBP Posts loop — Phases 1–4 built, 2026-08-31 (autonomous session)

A new sibling loop, `art/gbp`, drafts Google Business Profile Post proposals from genuine
content-gap evidence (never from a rank/pack signal alone — see the framing note in
`projects/art/loops/gbp/spec.md`). Built end to end while the operator was away, hardened via
14 rounds of adversarial Codex review across the four phases (2 on Phase 1, 3 on Phase 2, 2 on
Phase 3, 4 on Phase 4, 1 on the small event-log extension), each round run against real
production data (`D:\Dev\MMC\registry\clients\art.yaml`, `projects/art/gbp-profiles.md`,
`projects/art/loops/gbp/known-gaps.yaml`) as well as synthetic fixtures. Local commits only,
**not pushed** — decision 7 applies here exactly as everywhere else in this repo.

**What's built and where:**
- **Phase 1** (`6511628`) — `tools/dataforseo_maps.py` / `tools/dataforseo_local_pack.py`:
  DataForSEO Maps-rank and local-pack connectors, matching this client's own listing only by a
  verified `place_id`/`cid` (never name/domain), wired into `connector_registry.py`/
  `spec_validate.py`/`run_loop.py`. **Not yet enabled** — needs a verified `place_id`/`cid` per
  location (Phase 0, see below) before it can go in any spec's `inputs`.
- **Phase 2** (`bd01b22`) — `projects/art/loops/gbp/spec.md` (the loop itself, scaffolded but
  **not** in `project.md`'s `loops_enabled` and **not** scheduled), `known-gaps.yaml`
  (machine-readable content/structural gaps sourced from `gbp-profiles.md`), and
  `templates/loops/seo/gbp-checklist.md` (the human-run structural checklist
  `PLAN-SEO-PROGRAM-INTEGRATION.md` named but never created). Also a real structural fix to
  shared code: `run_loop.py`'s SEO-shaped selector/evaluator are now gated on
  `spec["loop"] == "seo"`, not merely on what connectors happen to be configured — closing a
  carry-forward loophole a Codex review found in the original claim.
- **Phase 3** (`d9a2e1e`) — `run_loop.py`'s `_pick_gbp_actions`: drafts a proposal only from a
  `known-gaps.yaml` entry explicitly routed `content_gap_evidence`, re-hashed fresh every run.
  The real `known-gaps.yaml` currently has exactly one eligible entry (Greeley deep-tissue
  massage) and produces exactly one real proposal on a live run. The most consequential fix
  here: the first version's rationale interpolated the gap's free-text `summary` verbatim,
  which could itself carry a rank-fix claim (the real entry's summary literally cites a GSC
  position) — the rationale is now built exclusively from closed/structured fields.
- **Phase 4** (`b3d95a1`) — `tools/lib/gbp_constraints.py` + `tools/draft_gbp_post.py`: the
  drafting/validation tool and its cross-repo hard gate (parses + hashes a small projection of
  both `gbp-profiles.md`, via a new confirmation sidecar `gbp-profile-confirmations.yaml`, and
  MMC's `art.yaml`; refuses to draft on any contradiction, any unconfirmed/stale fact, or a
  closed/inactive target location). This phase took the most hardening — see its own commit
  message for the full list, but the standout: the parser initially read `service_constraints`
  from the wrong YAML path and silently returned empty against the real `art.yaml`, disabling
  every trademark/promotability check, undetected by its own self-test because the test
  fixture matched the same wrong schema. Fixed and re-verified against the real file.
- **Phase 5** (`fe368da` start, `a5af381` completion) — approval-time target locking, fully
  built and hardened via seven further rounds of Codex review. See "GBP Posts loop — Phase 5
  completed" below for the full defect history; this bullet is kept only for the phase-by-phase
  narrative continuity of this section.

**Still not attempted:** Phase 6 (`tools/publish_gbp_post.py` — the only
thing that would ever call Google's API to create a Post) and Phase 7 (`measure_gbp_post.py`
— a capability spike). Both are blocked on Phase 0: a verified `place_id`/`cid` per location,
a Google Cloud project with posting/measurement APIs enabled (subject to Google's own access
review), and the one-time human-verified pairing between DataForSEO's identifiers and Google's
`accountId`/`locationId` for the same physical location. None of that can be done without the
operator. Given how many real, serious bugs four rounds of review found even in the
lower-risk phases (1–4), building the actual live-publish path speculatively — for public
medical-business content, with no way to verify it against a real API — was judged too risky
to attempt blind; it deserves the operator's explicit sign-off on the approach, not just a
best-effort implementation checked only by code review.

**Punch list for the operator's return:**
1. Skim the six commit messages above (`git log --oneline 821d93f..a5af381` or `git log -6`)
   for the full detail — this summary is necessarily compressed.
2. Nothing here changed `art/seo`'s behavior or state — the two are independent sibling loops.
   `art/gbp` is inert (not scheduled, not in `loops_enabled`) until explicitly turned on.
3. Decide whether to push (`821d93f..a5af381`) — per decision 7, that's the operator's call,
   never automated.
4. When ready to proceed toward Phase 0: the concrete asks are in
   `projects/art/loops/gbp/spec.md`'s "Phase 0" section.
5. `known-gaps.yaml`'s confirmation dates and `gbp-profile-confirmations.yaml`'s blanket
   `source_content_hash` were seeded from the single "as of 2026-08-12" date in
   `gbp-profiles.md` — genuinely per-fact confirmation would need the operator to reconfirm
   each fact individually, the same way `art.yaml`'s facts were originally obtained.
6. `projects/art/gbp-profiles.md` (pre-existing, not touched this session) names real
   competitors with review counts (Cornerstone, Weld, REV, The Joint) — flagged during Phase 2
   review as arguably in tension with standing decision 4's letter ("no data about other
   businesses is stored, at all"), but left untouched since it predates this session and
   scrubbing a human-authored document is the operator's call, not something to do unasked.

---

## Working agreement

**One issue at a time.** Fix it, confirm it works, then move to the next. No multi-item task
lists. Decisions get recorded here as they're made, so no context is lost between sessions.

---

## Where things stand

**Done, committed and pushed.** `origin/master` is at `821d93f` as of 2026-08-24; the
2026-08-16 push carried 11 commits (`ef5c8ec..f026bb1`), and the 2026-08-24 push carried 3 more
(`f026bb1..821d93f`) — both the operator's own, by hand. Decision 7 still holds, no tooling
pushes anything.

- The daily rank job `LoopAgency-Art-SEO-DailyRank` is **disabled** (`58a97d6`). It stays off
  until the cadence is deliberately restarted; the collector itself is now fixed.
  `LoopAgency-Art-SEO` (weekly, Mon 06:00) and `-Technical` (Thu 06:00) are enabled.
- `projects/*/loops/*/runs/` and `projects/*/diagnostics/` are **untracked and gitignored**
  (58 files). Files remain on disk; the loop and dashboard are unaffected. Note the earlier
  committed copies are still in published history — decision 3 accepted that.
- **Issue #1 is closed** (`dccb53a`) — see "Issue #1 — closed" below for what the first
  correct measurement said.
- **MMC's collector is fixed too** — see "MMC" below. Both repos are consistent.
- **`art/seo` is back on `propose-only`** (2026-08-16). See "Automation is off, for real" below.
- **Two `run_loop.py` defects fixed, 6 pending proposals rejected** (`9ec1306`, **pushed**). See
  "Two run_loop.py defects fixed" below.

**In progress:** nothing. The backlog below is unscheduled and unordered.

---

## Automation is off, for real (2026-08-16)

Three records disagreed: decision 6 said automation stays off, `SEO_PROGRAM.md` Part 3A
priority 0 said keep the loop propose-only/manual — and `projects/art/loops/seo/spec.md` still
said `approval_mode: tier1-enabled` + `auto_implementation_enabled: true`, left over from the
2026-08-03 Phase 7 activation. The spec now says `propose-only` + `false`.

`approval_mode` is the switch that actually matters. `apply.py:216-222` reads it **fresh from
the spec at apply time** and refuses every Tier-1 apply regardless of which path reached it —
human `approved` via `/review-pending`, or `approved-for-implementation` via `/codex-seo-review`.
`auto_implementation_enabled: false` alone would only have gated the Codex path (`apply.py:233`),
leaving the human-approval route open, so it is defence in depth rather than the lock.

Nothing was ever auto-implemented under the activation: zero proposals reached `applied`, and
`pending/` was empty when the switch flipped, so no already-created proposal carries a stale
cached `manual_approval_only: false` into the frozen state. Proposals are still *generated* —
`propose-only` blocks application, not drafting.

**To restore it:** flip both keys back, but only on an explicit recorded go/no-go, and not
before the backlog's "did it work?" item lands. Today there is still no "worse" outcome, so
the loop's success verdict remains vacuous — which is the reason decision 6 exists.

---

## What the audits established (2026-08-16)

Two independent audits were run against the code and the repository.

1. **Recommendations are built on sound data.** Which changes to suggest, whether one worked,
   and whether to halt are computed purely from Google Search Console. Traced line by line.
2. **Nothing has ever been changed on the website.** 9 proposals created, all rejected or
   held, zero applied. Nothing live to undo.
3. **No patient data, no health information, no secrets** in the repo or its history —
   confirmed by five independent searches. There is no connection to any records, booking, or
   billing system; inputs are Google's aggregate stats and public search-results scraping.
4. **The DataForSEO rank collector has never worked.** Across all 16 observations in history,
   every non-null result is the first target of its batch — no exceptions. Ten of twelve
   targets have never been queried.
5. **The system does not match its intended purpose.** The stated design is that GSC *and*
   DataForSEO data combine to direct changes. What exists: proposals from GSC alone, by fixed
   rule (rows at position 3–20, sorted by clicks, top 3). No LLM decides anything. DataForSEO
   data is fetched, paid for, and read by no decision code at all. **This gap is the largest
   open item in the backlog.**
6. **GSC's per-query view sees about a third of real clicks.** Google withholds rare queries.
   Measured on one 28-day window: 72 site clicks vs 23 attributable to a named query. Whether
   the withheld clicks are brand or non-brand is **not** measured. See
   `PLAN-SEO-TIMESERIES.md` Part 1b.

---

## Standing decisions

| # | Decision |
|---|---|
| 1 | **Cost is not a constraint.** $50 credit available. All 12 keywords should be checked. |
| 2 | **Cadence tapers.** Daily only during the current heavy-fix period, then twice-weekly, then weekly, then further out as the site's issues get fixed. |
| 3 | **Repo stays public** (needed for Claude Chat access). The 46 already-published commits are accepted. Mitigation is to stop publishing client raw data going forward — **not** to rewrite history. |
| 4 | **No data about other businesses or websites is stored, at all.** The collector must inspect a result to tell whether it is the client's, then **discard** non-matches — keeping "matched / did not match", never a competitor domain or URL. This forgoes competitive intel that is normally useful; operator's call. |
| 5 | **Do not publish the dashboard** — no hosted page, no commit — until the underlying data is correct. |
| 6 | **Automation stays off** until "did it work?" is trustworthy. |
| 7 | **Nothing gets pushed automatically.** Local commits only; pushing is the operator's. Held on 2026-08-16: the 11-commit push was done by hand. |
| 8 | **`SEO_PROGRAM.md` stays untracked and local** (2026-08-16). It is a 910-line distillation of three third-party courses, and its Part 3A is a named assessment of the client's site defects and traffic. Publishing it to the public repo is a call that hasn't been made, so it is deliberately not committed — not forgotten. It is not gitignored either, so it keeps showing as `??` in `git status` as a visible reminder. |
| 9 | **Automation is off at the spec, not just on paper** (2026-08-16). `art/seo` is `approval_mode: propose-only` + `auto_implementation_enabled: false`. Restoring either needs an explicit recorded go/no-go — see "Automation is off, for real" above. |

---

## Issue #1 — closed 2026-08-16

All three defects and the policy change are fixed, and the first correct measurement in this
system's history has been taken. The weekly `LoopAgency-Art-SEO` task was disabled for the
duration and re-enabled after acceptance passed.

**What changed**

- `tools/lib/serp_match.py` (new) — the shared "is this result ours, and where?" matcher.
  `tools/lib/timeseries.py` now imports it rather than keeping its own copy, so the
  connector, the dashboard and the one-shot diagnostic cannot drift apart again.
- `tools/dataforseo.py` — one POST per target; tasks matched by the keyword the API echoes
  back, never by array index; every non-`20000` outcome recorded as an **error**, never as an
  empty result. Position is the true organic index (cross-checked against `rank_group`, which
  agreed on all 12 live rows). Non-matching results are discarded — no competitor host, URL
  or title reaches disk. Each row carries `status: ok | absent | error`, and one failing
  target no longer discards the other eleven. `pull_metrics` got the same fix but **fails
  closed** instead, because it is a `critical` connector feeding `search_analytics`.
- `domain` is now required for both DataForSEO inputs (`connector_registry.py`). There is no
  "no domain configured" fallback — that would revive the substring match behind a default.
- `tools/run_loop.py` — `_section_history` refuses to hand a pre-fix (`schema_version < 2`)
  `local_rank` section to the attention comparison, and `_rank_of` replaces
  `(_numeric(x) or 0)`, which had been reading a missing position as **rank 0** — the best
  rank there is. report.md now distinguishes "not in results (of 99 seen)" from "NO ANSWER".
- `tools/seo_timeseries.py` — the integrity panel is split into a corrected era and a
  pre-fix era; the D4 claim is scoped and past-tense once a v2 observation exists, instead of
  silently becoming false.

**Why a version stamp rather than `local_rank_baseline_run_id`:** `_build_snapshot` carries
un-refreshed sections forward verbatim, so a pre-fix section lives on inside run directories
created after the fix. A run-id cutoff would hand it straight to the comparison; the version
travels with the data it describes.

**What the first correct measurement said** (run `2026-08-17T02-17-09-484Z-t5060s`, 12 billed
tasks). 4 of 12 targets rank; 7 are genuinely absent; 1 gave no answer (`40101`) and is
recorded as missing data, not as an absence:

| | Greeley | Denver |
|---|---|---|
| physical therapy | **#46** | absent (78 seen) |
| chiropractor | absent (99 seen) | absent (96 seen) |
| auto injury | *no answer* | **#18** |
| work comp | **#21** | absent (39 seen) |
| massage | absent (77 seen) | absent (89 seen) |
| acupuncture | **#35** | absent (79 seen) |

The old stored value for `physical therapy greeley` was `40` — which was `beaminghealth.com`'s
`rank_absolute`. The new `46` is this site's own organic index. **The two numbers were never
measuring the same thing**, which is exactly why the cutoff exists. The homepage also ranks
for several of these (#29 Greeley physical therapy, #17 Greeley acupuncture) where the
targeted service page does not.

**Acceptance, all passed**
- 12 requests issued, 12 rows returned, one per configured target.
- Every recorded position resolves to the client's own site (`matched_domain`), and
  `rank_group` agreed with the counted organic index on every row.
- Zero non-client hosts anywhere in the snapshot.
- **Zero** historical rank alarms. Counterfactually, 17 prior observations would have been
  compared without the cutoff; 0 were.
- `tools/tests/phase1_exit_criteria.py` 225/225 (was 211/211), and every module `--verify`
  passes. Two pre-existing failures in `lib/timeseries.py` — stale hardcoded run counts that
  went red whenever a new run landed — were rewritten as invariants.

**Correction to this file's own earlier wording.** It said "the first scheduled run after
re-enabling raises zero historical rank alarms". That is not a durable test: with v2-only
history, zero findings are guaranteed by construction through the *second* v2 observation
(`consecutive_runs: 2` needs both `history[0]` and `history[1]`), and from the **third** a
real rank move is *supposed* to fire. The correct standing criterion is *comparisons draw
exclusively on v2 history* — asserted offline in `phase1_exit_criteria.py`, which also proves
a leaked pre-fix section would fire.

---

## Two run_loop.py defects fixed, 6 pending proposals rejected — 2026-08-24

**Commit `9ec1306`, pushed to `origin/master` at `821d93f`** by the operator's own hand
(decision 7 — pushing this repo is the operator's own call, same as everything else here).

**Defect 1 — round-robin action assignment never actually round-robinned.**
`_pick_new_actions` computed `n = min(max_count, len(candidates), len(allowed_actions))`, then
assigned `allowed_actions[i % len(allowed_actions)]` to each of the top `n` click-ranked
candidates. Because `n` could never exceed `len(allowed_actions)`, the modulo could never wrap
— the "round robin" was dead code, and worse, it silently capped a run's proposal volume to
however many action types were configured, even when `max_count` and candidate supply both
allowed more. `art/seo`'s spec happens to have exactly 3 allowed actions and `max_count=3`, so
this had zero visible effect there — it only bites a spec with fewer action types than
candidates. Fixed: `n = min(max_count, len(candidates))`; `spec_validate.py` already requires
`allowed_actions` to be non-empty, so the modulo stays safe.

**Defect 2 — min_sample_size compared against the wrong number.**
`_evaluate_prior_experiments` gated an applied proposal's evaluation on
`metrics["sample_size"] >= p["min_sample_size"]` — but `metrics["sample_size"]` is impressions
summed across *every* keyword row site-wide, not the specific page/keyword being judged. That
site total was almost always above `min_sample_size` regardless of whether the one row in
question had enough traffic yet, so the check never actually blocked a verdict (this was
backlog item "Fix the data-sufficiency check", `run_loop.py:518` in the old line numbering).
Fixed: the per-target row was already being fetched a few lines later for the match itself —
that row's own `impressions` field is now what gets compared to `min_sample_size`.

`tools/tests/phase1_exit_criteria.py` had two assertions (`test_degradable_connector_failure_...`,
`test_keyword_exclusions_filters_candidates`) that hard-coded the round-robin cap bug's effect
(GOOD_SPEC's single allowed_action capped proposal creation to 1 regardless of candidate count)
— updated to the corrected counts (3 and 2). Full suite: 225/225 after the fix.

**The 6 proposals sitting in `art/seo/pending/`** (3 from the 2026-08-17 run, 3 from
2026-08-24) were rejected via `/review-pending`: 3 targeted the clinic's own already-ranking
brand term "accelerated rehab" (0–2 clicks — not a real ranking problem), and 3 targeted
near-brand-noise query variants. `keyword_exclusions` in `projects/art/loops/seo/spec.md` was
extended with those 3 variants — `"accelerated massage and rehab"`, `"accelerated pt"`,
`"accelerate rehab"` — verified as substring-safe against the real brand terms
("accelerated rehab", "accelerated rehab therapy" both still un-excluded) before rejecting, so
next Monday's run doesn't immediately recreate the same class of proposal.

---

## MMC — the briefing side, done 2026-08-16 (`D:\Dev\MMC`)

Fixed in that repo, by a session there, not from here. Recorded so the two halves stay legible
together — a change to this connector's output shape is always a two-repo change.

`collector/sources/looping.py` `_summarize_local_rank` used to count every null position as a
genuine "not in top results", which would have briefed Issue #1's one `error` row as a real
absence — the same failure mode, one repo over. It now splits three ways from the row's explicit
`status`: `local_rank_positions` (ok), `local_rank_absent` (carrying `organic_results_seen`, so
"not in 99 results" is auditable), and `local_rank_errors` (carrying `error_reason` /
`status_code`). `local_rank_null_count` was **removed** rather than left as a field that invites
re-merging the two outcomes. Added `local_rank_counts` (cross-checked against the section's own
`request_count`/`success_count`/`error_count`) and `local_rank_meta`.

**The era boundary is carried as a `series_key`** derived from `schema_version` + `rank_metric`,
and only matching keys may be compared. That is stricter than the date-based rule this repo
first suggested, for a reason specific to MMC: its collector reads whichever run is *latest*, so
a missed run leaves a pre-cutover snapshot in place and a date rule would stamp v1 data
"corrected". This repo didn't need that form because `_section_history` walks every run
directory, not just the newest. `PROMPT.md` still names 2026-08-17 as human-facing context, but
no code branches on it.

`PROMPT.md` now requires "not ranking" and "no answer from the rank check" as separate phrases
with separate counts, forbids writing an error as an absence or a drop, escalates a total pull
failure to *Decisions awaiting Nate* instead of narrating a ranking collapse, and refuses deltas
across `series_key`. `collector/drift.py` needed no change. Two KB pages asserting the superseded
2026-07-26 partial fix were corrected.

**Correction worth carrying forward:** v1 sections *do* have `rank_metric` (value
`"organic_rank_position"`). Only `schema_version` is genuinely new. This repo's brief to MMC got
that wrong; detect the era on `rank_metric`'s **value**, not on a key's presence.

**Left open deliberately on MMC's side:** no drift check for a total local-rank collection
failure — with 1 error in 12 being ordinary, any threshold is either noise or arbitrary, so
`PROMPT.md` escalates the all-failed case instead. If a hard check is ever wanted, the
non-arbitrary one is `request_count == number of configured targets` (12): that is the specific
canary for D4 returning, and it has a right answer rather than a tolerance. It is already
asserted in this repo's test suite, so it would be defence in depth.

---

## Backlog — not scheduled; planned when we reach it

- **Make rank data actually steer decisions.** The gap between intent and build (finding 5).
  Largest item, and closest to the original purpose of the project. Now unblocked — as of
  Issue #1 there is real rank data to steer with for the first time.
- **Make "did it work?" mean something.** There is no "worse" outcome — anything short of a
  guardrail breach is filed as a *verified winner*, including a page that slipped four
  positions (`run_loop.py` `_evaluate_prior_experiments`, ~lines 549–562). Needs the operator
  to set the thresholds: what counts as "no change" vs "worse".
- **Fix the half-blind second-opinion review.** `lib/review_protocol.py:149–168` reads
  `results` and `indexation`; the data is written as `rows` and `indexation_rows`, so both
  fields are always empty. An address-format mismatch sits behind it (rank rows store
  `/physical-therapy/`, proposals store the full URL).
- **Redact real client queries from three tracked planning docs** —
  `PLAN-SEO-PROGRAM-INTEGRATION.md`, `PLAN-REVIEW-LOG-SEO-PROGRAM.md`,
  `PLAN-SEO-TIMESERIES.md`. They quote real searches as evidence, so they need redacting
  rather than ignoring. Also: the client's real street addresses are baked into test fixtures
  in `tools/dataforseo.py` and `tools/spec_validate.py` and should be swapped for invented ones.
  Two more found during Issue #1: `tools/serp_diagnostic.py`'s `--verify` and
  `tools/lib/timeseries.py`'s check-15 fixtures hardcode real competitor domains. New fixtures
  written in Issue #1 all use synthetic `.invalid` hosts; these are the leftovers.
- **The map listing.** A correct one-off test (2026-08-12) found the client ranks #2
  organically in Greeley and still takes zero clicks, because a three-entry map pack sits above
  the organic results. Denver is absent from the top 100 entirely. This is where leads are
  being lost, and it is mostly operational work rather than code.
- **Documentation consolidation.** The root `PLAN-*.md` files have sprawled; they burn tokens
  on every read and obscure what is current. This file is the intended replacement for
  "what's happening now" — the consolidation is folding the rest down.
- **Dashboard scheduling and publishing** — both only once the data is correct (decision 5).
