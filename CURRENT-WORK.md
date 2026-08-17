# Current work — start here

_Last updated 2026-08-16. This is the live working record: what we're doing, what we decided,
and why. It supersedes the older `PLAN-*.md` files for anything current — those remain as the
historical design record. Read this first._

---

## Working agreement

**One issue at a time.** Fix it, confirm it works, then move to the next. No multi-item task
lists. Decisions get recorded here as they're made, so no context is lost between sessions.

---

## Where things stand

**Done and committed** (`58a97d6`, local only — nothing pushed):

- The daily rank job `LoopAgency-Art-SEO-DailyRank` is **disabled**. It stays off until the
  collector is fixed, then returns at a reduced cadence.
- `projects/*/loops/*/runs/` and `projects/*/diagnostics/` are **untracked and gitignored**
  (58 files). Files remain on disk; the loop and dashboard are unaffected.

**In progress:** nothing. Issue #1 is specified below but not started.

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
| 7 | **Nothing gets pushed automatically.** Local commits only; pushing is the operator's. |

---

## Issue #1 — the DataForSEO rank collector (next up)

**File:** `tools/dataforseo.py`, `pull_local_rank` (~lines 238–283), plus its `--verify` tests.

Three defects and one policy change:

1. **All 12 keywords go out in one request; the live endpoint processes only the first.**
   → one request per keyword. Treat a non-success task status as a connector error, never as
   an empty result — that conversion is what turned API rejections into false "not ranking".
2. **Results are matched by URL-fragment substring with no domain check**, which is how
   competitors' rankings were recorded as the client's.
   → match on host *and* path with segment boundaries. Reuse the matcher already written and
   tested in `tools/lib/timeseries.py` (`classify_local_rank_row`, `_path_matches`); its
   fixtures include `/massage/` correctly not matching `/massage-therapy-guide/`.
3. **The stored position counts every SERP element** (ads, map pack), not the organic index.
   → record the true organic position. Keep the existing field name so MMC's briefing keeps
   parsing; add `matched_domain` beside it.
4. **Policy (decision 4):** discard non-matching results. Do not write `result_url` or any
   other competitor identifier into the snapshot.

**Also needed: a history cutoff.** Once fixed, the numbers change meaning — competitor entries
vanish, real ones appear — and compared against the old records that reads as a huge swing and
fires false alarms. `tools/run_loop.py` `_section_history` must respect a cutoff so comparisons
cannot reach behind it.

**Verification**
- A live run issues 12 requests and returns 12 results.
- Every recorded position resolves to the client's own site.
- No competitor domain or URL appears anywhere in the snapshot.
- The first scheduled run after re-enabling raises **zero** historical rank alarms. If it
  raises any, the cutoff is not working — do not wave it through as expected noise.
- `tools/tests/phase1_exit_criteria.py` stays green (211/211) and every module's `--verify`
  passes.

---

## Backlog — not scheduled; planned when we reach it

- **Make rank data actually steer decisions.** The gap between intent and build (finding 5).
  Largest item, and closest to the original purpose of the project.
- **Make "did it work?" mean something.** There is no "worse" outcome — anything short of a
  guardrail breach is filed as a *verified winner*, including a page that slipped four
  positions (`run_loop.py` `_evaluate_prior_experiments`, ~lines 549–562). Needs the operator
  to set the thresholds: what counts as "no change" vs "worse".
- **Fix the data-sufficiency check.** `run_loop.py:518` compares `min_sample_size` against the
  whole site's impressions rather than the page being judged, so it never blocks a verdict.
  The correct row is already fetched twelve lines later.
- **Fix the half-blind second-opinion review.** `lib/review_protocol.py:149–168` reads
  `results` and `indexation`; the data is written as `rows` and `indexation_rows`, so both
  fields are always empty. An address-format mismatch sits behind it (rank rows store
  `/physical-therapy/`, proposals store the full URL).
- **Redact real client queries from three tracked planning docs** —
  `PLAN-SEO-PROGRAM-INTEGRATION.md`, `PLAN-REVIEW-LOG-SEO-PROGRAM.md`,
  `PLAN-SEO-TIMESERIES.md`. They quote real searches as evidence, so they need redacting
  rather than ignoring. Also: the client's real street addresses are baked into test fixtures
  in `tools/dataforseo.py` and `tools/spec_validate.py` and should be swapped for invented ones.
- **The map listing.** A correct one-off test (2026-08-12) found the client ranks #2
  organically in Greeley and still takes zero clicks, because a three-entry map pack sits above
  the organic results. Denver is absent from the top 100 entirely. This is where leads are
  being lost, and it is mostly operational work rather than code.
- **Documentation consolidation.** The root `PLAN-*.md` files have sprawled; they burn tokens
  on every read and obscure what is current. This file is the intended replacement for
  "what's happening now" — the consolidation is folding the rest down.
- **Dashboard scheduling and publishing** — both only once the data is correct (decision 5).
