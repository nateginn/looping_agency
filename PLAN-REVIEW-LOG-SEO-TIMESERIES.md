# Plan Review Log: keyword-level SEO time series + progress dashboard

Started 2026-08-16, ~01:25 local (MDT). `MAX_ROUNDS=8`.

- Plan under review: `PLAN-SEO-TIMESERIES.md`
- Builder / orchestrator: Claude (Opus 5)
- Adversarial critic: OpenAI Codex (`codex-cli 0.144.1`, model `gpt-5.6-luna`,
  `model_reasoning_effort = high`), read-only sandbox every round, single resumed session
  `01a00974-d5b9-7531-a071-3a9c6e64f026` so it cannot re-litigate settled points
- Context: Nate asked for this overnight, unattended, and stated he would be unavailable to
  answer questions — so disagreements are resolved between the two models, and every
  unresolved judgment call is surfaced as an explicit Open Question **with a stated default**
  rather than silently decided.

---

## Round 1 — Codex: `VERDICT: REVISE` (15 findings)

Read `CLAUDE.md`, the plan, `PLAN-SEO-PROGRAM-INTEGRATION.md`, `run_loop.py`, all 22 run
artifacts, `tools/gsc.py`, and the MMC files. Full critique:

1. **BLOCKER — Local-rank history is not recoverable as chartable rank.** D4 means most targets
   were never queried; D5 means every historical number is mixed-feature `rank_absolute`, so
   even ART-owned URLs are not valid organic positions. The plan's "own positions only" chart
   conflicts with the approved plan's decision and its required rebaseline.
   *Fix:* remove numeric historical local-rank charts, keep forensic classifications only, make
   any numeric chart depend on P0.1's corrected connector plus its coded rebaseline.
2. **HIGH — F3's measured sequence is wrong.** The distinct pulls are `397, 716, 716, 716, 910`;
   the second `397` was a carried-forward snapshot — exactly the error the plan warns about.
3. **HIGH — "910 keywords, 900 one-impression noise" is false.** 910 rows, 707 unique queries,
   397 rows with exactly one impression.
4. **HIGH — The per-keyword view is underspecified and can misrepresent reality.** 129 queries
   appear on more than one page, but the dashboard promises one row per keyword with "its page"
   and one position. *Fix:* track `(keyword, page)` as the entity or specify and test an
   aggregation rule; missing target queries must remain "no observation", never zero.
5. **MEDIUM — The "stable" all-time universe is not stable.** New observations silently expand
   membership. *Fix:* freeze/version it or emit an explicit change notice.
6. **HIGH — Phase B aggregates metrics incorrectly.** CTR and position cannot be summed.
   *Fix:* `clicks=Σ`, `impressions=Σ`, `ctr=clicks/impressions`, impression-weighted position,
   with explicit missing-date semantics.
7. **HIGH — Phase B has no verification coverage.** Nothing tests the second request, the
   backfill, the 90-day upsert, provisional days, idempotence, API failure, or redaction.
8. **MEDIUM — F6 names the wrong artifact fields.** `run.json` stores `start_date`/`end_date`,
   not `startDate`/`endDate`.
9. **HIGH — Determinism contradicts the design.** A build-time `generated-at` in the header
   cannot coexist with byte-identical-output verification.
10. **HIGH — Generated HTML lacks an injection/escaping requirement.** Queries and URLs are
    external data being embedded into HTML and inline JS in a public artifact.
11. **MEDIUM — The external-reference check is too weak.** "No URL in link text" does not
    detect external `<script>`, `<link>`, `<img>`, CSS `url()`, iframes, or fetch/XHR.
12. **HIGH — The literal Python-only rule is unresolved.** The plan proposes client-side inline
    JavaScript. *Fix:* default to static HTML/SVG + native controls, or get an explicit human
    exception.
13. **MEDIUM — MMC compatibility is asserted more strongly than specified.** MMC rounds CTR to
    4dp and position to 1dp; the plan requires equality without specifying rounding.
14. **MEDIUM — The public-repo question has no stated default**, yet step A5 commits the
    artifacts anyway. *Fix:* state one; safest is hold.
15. **MEDIUM — The scheduled-wrapper claim lacks an integration test** proving a dashboard
    failure cannot mask a failed loop.

> Codex also confirmed that Phase A's separation from `run_loop.py` does protect the run
> contract, the approval state machine, and the push/deploy boundaries as written.

### Claude's response — Draft 2

**Accepted in full: 1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14.** Accepted and taken further:
2, 3, 15.

Three of Codex's findings were checked against the artifacts rather than taken on trust, and
the checks changed the plan more than the findings did.

- **#2 → my own count was wrong in the same way, and worse.** Draft 1 claimed 5 GSC
  observations. The real number is **9**. Draft 1's measurement script read
  `doc["search_analytics"]` and so read *nothing* from the four schema-v1 snapshots
  (2026-07-16 … 07-21), which store the bare dict at top level. Those hold 0, 177, 245 and 245
  rows. **The plan that exists to prevent carried-forward/normalisation errors committed one.**
  Kept in the plan as F1 verbatim, and promoted to a required test (check 3) that asserts the
  177 rows survive normalisation. Corrected sequence: `0 → 177 → 245 → 245 → 397 → 716 → 716
  → 716 → 910`.
- **#1 → confirmed empirically and it is far worse than stated.** Tested the D4 hypothesis
  against all 16 real local-rank observations: **31 non-null positions exist in the entire
  history, and all 31 are index 0 of their location's batch. Zero exceptions.** Ten of twelve
  daily targets have never been queried once — `null` means "not asked", not "not ranking" —
  and the two that were return `rank_absolute` matched by bare URL-substring, resolving to
  `mgmc.org`, `occ-ortho.com`, `beaminghealth.com`, `denverhealth.org`. So Draft 2 ships **no
  numeric local-rank chart of any kind**, replaces it with a collection-integrity panel, adds
  check 16 asserting the `batch_index == 0` invariant on real data, and adds check 17 asserting
  no field named `position` exists in the emitted series at all — the decision is enforced
  structurally rather than by discipline. This independently corroborates P0.1's cost estimate
  (2 billed tasks/run today vs 12 once tasks execute) from a different direction.
- **#3 → measured.** 910 rows / 707 unique queries / 397 rows at exactly 1 impression / 6 rows
  and 5 queries with any clicks. Also measured the universe rule's real output: **40 queries**,
  not the 60–90 Draft 1 guessed.
- **#4 → the real case is sharper than the abstraction.** The top brand query is split across
  `http://` and `https://` variants of the *same* homepage (15 clicks and 3). Draft 2 adds a
  page-normalisation rule, makes `(query, normalized_page)` the storage entity, specifies
  impression-weighted rollup with a `page_count` label, and makes check 10 the real 18/55 case.
- **#12 → upheld, and Draft 1 was simply wrong.** The dashboard now ships **zero JavaScript**;
  native `<title>`/`<details>`/`<table>` replace the interactivity, and the `dataviz` skill's
  crosshair default is documented as a deliberate downgrade because a repo rule outranks a
  house style. This also removes most of #10's surface.
- **#15 → removed rather than tested.** Draft 1 wanted to edit `tools/scheduled/task.cmd`, the
  **shared** wrapper for every scheduled job. Codex asked for a test proving a dashboard
  failure cannot mask a failed loop; the better answer is to make it impossible — Draft 2
  touches the scheduling layer not at all (Part 7), and relies on the page's self-announcing
  freshness strip instead.
- **#14 → default stated: hold.** All three generated artifacts are `.gitignore`d. "No worse
  than the status quo" is not consent.

Also added, unprompted, from the same round of checking: F2's *irregular spacing* finding (two
pairs of pulls minutes apart, one ten-day gap → a real time axis is mandatory), and F8's
`sample_size` trap (it is total impressions, not row count — confusing the two silently breaks
every truncation check).

Nothing was rejected this round.

---

## Round 2 — Codex: `VERDICT: REVISE` (14 findings)

Codex independently reproduced both of Draft 2's headline claims — "9 distinct GSC
observations, 8 usable" and "31 non-null local-rank rows, all at batch index 0" — and noted
that the second is only reproducible *after* deduplicating by `as_of` and reconstructing batch
order, because `batch_index` does not exist in the snapshots. That observation became finding
#4 and changed the design.

1. **HIGH — The run-directory count is still wrong.** 23 directories: 22 snapshot-bearing, one
   partial-failure without a snapshot. Draft 2 says 22 throughout and A5 says "all 22 runs".
2. **HIGH — The universe size of 40 is definition-dependent and ambiguous.** Ranking raw
   `(query,page)` rows and ranking rolled-up queries give materially different sets.
3. **HIGH — Check 11's "129 multi-page queries" is stale after the adopted normalisation.**
   129 raw, but only **69** after `http`/`https` page normalisation.
4. **HIGH — `batch_index` reconstruction is underspecified.** Deriving it from current row
   order and the current spec can silently misclassify historical data. *Fix:* reconstruct from
   that run's `run.json` tool-call `targets`/`locations`, and return `unknown` rather than infer.
5. **MEDIUM — The integrity panel still carries `raw_rank_absolute`**, a number users may read
   as a rank. "No chart" does not stop a table from presenting an invalid number.
6. **HIGH — Phase B cannot use `tools/gsc.py` as implemented.** `pull_metrics()` hard-maps
   `keys[0] → keyword`, `keys[1] → page`; a `["date"]` response would not produce a date field.
7. **HIGH — Phase B has no ongoing invocation path.** Scheduling is untouched and `/run-loop`
   only rebuilds Phase A, so the "week-by-week" series goes stale unless run by hand.
8. **MEDIUM — Check 7 omits the "`run.json` exists but has no GSC call" branch** that F7 claims.
9. **MEDIUM — `row_limit` is not recorded in `run.json`**, so the builder cannot know a
   historical request's limit; defaulting to 1000 can make the truncation flag wrong.
10. **MEDIUM — The 36→12 local-rank wording is temporally false.** The change happens *between*
    two observations on 2026-07-26, 16 minutes apart.
11. **MEDIUM — The truncation guard does not prove the UI cannot misread capped totals.**
12. **MEDIUM — "≤480 rows for 16 months" and "settles in ~3 days" are stated too definitively.**
    16 calendar months can exceed 480 days; the 3-day rule is a heuristic.
13. **MEDIUM — The `/run-loop` hook is generic** and could fire the SEO renderer after non-SEO
    loops.
14. **CUT — Phase A is still broader than the operator's request.** Technical-health charts,
    backlink charts and the integrity panel add implementation and interpretation surface to
    what was asked to be a keyword time series; MMC already surfaces much of that monitoring.

### Claude's response — Draft 3

**Accepted in full: 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13.** **#2 accepted and measured.**
**#14 accepted in part**, with the disagreement recorded below.

- **#1, #3, #10 → measured and corrected.** 23 directories / 22 snapshot-bearing. 129 raw
  multi-page queries → **69** after normalisation (check 11 now asserts both sides of the
  transform, plus clicks/impressions conservation across it). The 36→12 boundary is named by
  exact timestamps: `2026-07-24T03:09:56Z` and `2026-07-26T05:42:02Z` carry 36;
  `2026-07-26T05:58:58Z` onward carries 12 — the change is *inside* 2026-07-26, 16 minutes
  apart. Draft 3 also states as a rule that the implementation **enumerates** rather than
  hard-coding any of these counts.
- **#2 → measured, and my number was wrong for a third time.** Under the raw-row rule the
  universe is **50**; under the rolled-up-query rule it is **61**. Draft 2 said 40 because the
  measuring script forgot to seed the set with the 12 spec targets. Adopted the **rolled-up**
  rule (it is the entity the operator thinks in, and it is a strict superset — the 11 extra
  members include real commercial variants like `physical therapy greeley co` and
  `auto accident chiropractor denver` that the raw rule was hiding). Check 19 now asserts
  **both** numbers so the ambiguity cannot silently return. Three counting errors in three
  drafts is the argument for asserting the rule's output rather than trusting prose.
- **#4 → accepted, with a stronger derivation than Codex proposed.** `batch_index` is
  reconstructed as the row's ordinal within the *contiguous run of rows sharing its
  `location_name`* — precisely the order `pull_local_rank` appends them in — and then
  **cross-validated** against that run's `run.json` `dataforseo-local-rank` args. Any
  disagreement, or non-contiguous grouping, yields `unknown`, and **`unknown` is never
  `not-queried`**: an inferred field driving an accusation must not be guessed. New check 16b.
- **#5 → quarantined by field name.** The raw value is never rendered in the panel at all and
  survives only in `series.json`'s `forensic` block and the CSV, under the key
  `raw_rank_absolute_not_a_rank`. New check 27c asserts the panel's DOM contains no such digits.
- **#6 → correct and load-bearing; Draft 2 was simply wrong.** `pull_metrics` would have
  returned a list whose `keyword` was a date. `gsc_daily.py` now owns its own request body and
  parser and **imports** `gsc.py`'s auth/redaction/error helpers rather than editing them, so
  `tools/gsc.py` is not modified at all (check 36).
- **#7 → accepted, and answered structurally rather than by adding a scheduler.** Phase B is
  stated as manual/on-demand where the operator will read it, the dashboard banners a stale
  daily series after 14 days, and — the substantive fix — the refresh window is made
  **gap-proof**: it always starts at *wherever the file actually ends* minus the provisional
  overlap, not at a fixed 90-day lookback. Any cadence therefore returns a complete series;
  only an invocation gap exceeding GSC retention loses data, and that is detected and reported
  as `retention_gap` rather than left as a silent hole. New check 30.
- **#9 → the assumption is versioned and renamed.** `row_limit_source:
  "assumed-connector-default"`, and the flag is `truncation_suspected`, never `truncated`;
  check 9 asserts the name. Recording `row_limit` in `run.json` is noted as a one-line
  improvement belonging to P0.3, deliberately **not** done here, because Principle 2 forbids
  this work from touching the run path.
- **#11 → promoted from a flag to a rendering requirement**, with check 27b asserting on the
  parsed DOM that capped totals are marked partial and their deltas read "not comparable".
- **#12 → computed instead of asserted.** The tool computes `requested_span_days`, asserts it
  against `row_limit` and refuses rather than silently truncating; retention is treated as
  approximate; `provisional_days` is a named, configurable heuristic and is labelled as one.
- **#13 → guarded and made non-fatal.** The `/run-loop` step fires only for the `seo` loop, and
  a non-zero exit is a note that never changes the run's reported outcome.

**#14 — accepted in part; the disagreement, stated plainly.** Codex is right that Draft 2 had
grown past "a keyword time series", and right about which parts were expensive. **Cut:** the
per-page CWV small multiples and the indexation grid — the two most layout-expensive sections,
not keyword progress, and *already reported daily* by MMC's briefing
(`deindexed_priority_pages`, the CWV scores, `data_freshness`). **Declined:** removing
backlinks (two numbers, five observations, one small chart, ~20 lines — referring-domain growth
is a direct, low-ambiguity indicator of exactly the work being done) and removing the
local-rank integrity panel (it is the single highest-value finding this whole review produced,
it is what tells the operator that 83% of his paid daily rank checks were never issued, and it
is the acceptance baseline P0.1 will need). **Compromise:** a one-line technical *status strip*
— latest CWV range, indexation N/6 PASS, sitemap warnings, as-of date — replaces the cut
sections. No chart, no trend, no interpretation surface, ~10 lines, and it prevents "where did
my monitoring go".

---

## Round 3 — Codex: `VERDICT: REVISE` (13 findings)

Codex confirmed every corrected measurement (23/22 directories, 9 normalised GSC observations,
129→69, 61/50 universe rules) and independently validated the batch-index derivation against
the real artifacts: "all 31 non-null values are ordinal 0, with no observed ordering mismatch."

1. **BLOCKER — Phase B's refresh formula is backwards.** `min(last_recorded − provisional,
   today − retention)` requests the full retention window whenever the file ends in the past,
   and can request beyond retention. *Fix:* `max(...)`, and test both a 200-day gap and an
   older-than-retention gap.
2. **BLOCKER — Phase B is never connected to the dashboard.** The plan creates
   `gsc_daily.jsonl` but never says `seo_timeseries.py` reads it, or how the page chooses
   between Phase A and Phase B data.
3. **HIGH — `gsc_daily.jsonl` is omitted from the public-artifact decision.** Q1 names three
   files; Q2 defaults to building a fourth full of client data.
4. **HIGH — Retention and metadata defaults remain guessable.** `retention_days`, exact
   row-limit behaviour, and where `requested_span_days`/`returned_row_count`/`provisional_days`
   are recorded are undefined.
5. **MEDIUM — The batch-index derivation is empirically sound but underspecified for legacy
   runs.** Older `run.json`s hold unpinned targets plus a locations list, so reconstruction must
   reproduce the connector's *expansion and ordering*, not compare a flat target list.
6. **MEDIUM — Check 16b does not test location disagreement or real run-to-args reconciliation.**
7. **MEDIUM — Check 19's "raw-row rule" is still undefined**, so the asserted `50` is
   unfalsifiable.
8. **MEDIUM — The forensic-CSV claim contradicts the deliverables.** The only CSV defined is
   `gsc_queries.csv`, which has no local-rank fields.
9. **MEDIUM — The technical status strip collapses independently stale sources into one
   as-of date.** Pagespeed and indexation carry separate timestamps.
10. **MEDIUM — Backlinks are not unambiguously "growth."** The connector returns current index
    totals, and the real series falls 86 → 80 before returning to 81.
11. **MEDIUM — Near-duplicate GSC labelling is required but not operationalised** — no
    threshold, no label text, no check.
12. **LOW — The Phase B verification reference is stale** (20–27 in Part 4, 28–35 in Q2, actually
    28–36).
13. **LOW — Check 34 protects against API failure but not an interrupted write.**

> Codex also stated the local-rank integrity panel "is justified and should remain; it documents
> the actual collection defect without inventing ranks."

### Claude's response — Draft 4

**All 13 accepted.** Two were verified against the artifacts first, and both checks changed the
wording as much as the design.

- **#1 → a one-character error that falsified the whole gap-proofing argument.** `min` selects
  the *earlier* date, so a file ending 200 days ago would have re-requested the full retention
  window every time, and an older-than-retention gap would have requested a start date before
  retention begins. `max` is correct. Kept visible in the plan with the reasoning, and check 30
  now tests both directions explicitly. This is the kind of finding the whole exercise is for:
  the prose around it was right, the formula under it was inverted.
- **#2 → specified end to end.** `seo_timeseries.py` reads `gsc_daily.jsonl` +
  `gsc_daily.meta.json` when present and adds a **separate** "True weekly performance" section
  *above* the Phase A charts — explicitly **not** replacing or merging them, because they
  measure different things (F2) and silently swapping one for the other is exactly the
  misrepresentation this plan exists to prevent. Absent / present / stale behaviour all defined;
  check 27f asserts all three on the DOM.
- **#5, #6 → verified, and the legacy case is real.** The `2026-07-24` run's `run.json` carries
  **12 targets with no `location` key** plus `locations: ["Greeley","Denver","UNC Campus"]`,
  which the connector expanded to 36 rows. So a flat-list comparison would indeed have been
  wrong. The validator now replays `pull_local_rank`'s grouping exactly — unpinned targets
  expanded once per location in `run.json` order, first-appearance group order, input order
  preserved — and asserts the reconstructed `(location, keyword, page)` sequence equals the
  snapshot's **element by element**, with `unknown` on any length, location-order, or
  target-order mismatch.
- **#10 → verified, and Draft 3's own wording was the problem.** Referring domains went
  `86 → 80 → 81 → 81 → 81`: it *fell* by six, then flattened. Draft 3 called this
  "referring-domain growth… a direct indicator of the off-page work" — wrong on the data and
  causal on top of it. DataForSEO returns current *index* totals, which move when its crawler
  re-indexes, not when work is done. Narrowed to one small labelled table with sparklines,
  retitled "DataForSEO index totals, as of <date>", all growth and causal language removed.
  This also completes the narrowing Codex asked for in R2 #14.
- **#3, #4, #7, #8, #9, #11, #12, #13 → applied as specified.** `gsc_daily.jsonl` and its new
  `gsc_daily.meta.json` sidecar are gitignored; defaults are tabulated (`retention_days 480`,
  `provisional_days 3`, `row_limit 1000`, stale banner 14 days); the raw-row comparator is
  defined precisely enough to falsify the `50`; the phantom local-rank CSV claim is withdrawn
  rather than a second CSV invented; the technical strip carries separate pagespeed and
  indexation as-of dates with `stale`/`unknown` states; near-duplicate pulls get a **< 24 h**
  threshold, fixed annotation text, exclusion from deltas, and check 27d against the two real
  minute-apart pairs; check references corrected to 28–36; and `gsc_daily.jsonl` is written via
  same-directory temp file + `os.replace` with check 34b for the interrupted-write case.

Nothing was rejected this round.

---

## Round 4 — Codex: `VERDICT: REVISE` (2 findings)

Codex confirmed the corrected measurements, the batch derivation, the legacy expansion rule
against the real 36-row observation, the `max` formula, the metadata sidecar, atomic writes,
weekly integration, backlink relabelling and near-duplicate handling — and explicitly said it
would not cut anything further.

1. **Public-artifact ambiguity remains in Q1.** Part 3.3 gitignores five files; Q1's explicit
   default list still names three. An implementer following Q1 alone could publish the Phase B
   client history.
   *Fix:* make Q1 list both Phase B files, or replace the list with the ignored path.
2. **The sidecar consistency contract is incomplete.** Check 27f varies only
   `gsc_daily.jsonl`'s presence and check 34b protects only the JSONL, so a missing, stale, or
   partially written `.meta.json` can produce an ambiguous or incorrect weekly section.
   *Fix:* define missing/corrupt/mismatched sidecar behaviour and prove the dashboard refuses or
   clearly degrades unless both files are valid for the same pull.

### Claude's response — Draft 5

**Both accepted.**

- **#1 → replaced the enumeration with a path rule**, which is the more durable fix: Q1 now
  states `projects/*/loops/*/timeseries/` rather than listing files. An enumeration can fall out
  of sync with reality — it just did, in the same document, in one draft. A path cannot.
- **#2 → the two-file write is made *detectable* rather than assumed atomic.** Two files cannot
  be `os.replace`d in one transaction, so instead `gsc_daily.meta.json` now carries
  `jsonl_row_count`, which must equal the JSONL's real line count. An interruption between the
  two replaces leaves a stale meta that fails the equality and is caught. On any of — meta
  missing, meta unparseable, count mismatch — the weekly section is **omitted** and replaced by
  an explicit *"Daily GSC data is inconsistent (`<reason>`) — re-run `gsc_daily.py`"* notice,
  **never** silently degraded to the "absent" state: absent and broken are different conditions
  and the operator can only fix one of them. Check 27f grew from three cases to six (a–f), and
  each asserts Phase A's output is byte-identical to the no-daily-file build, so a Phase B fault
  can never damage Phase A. Check 34b gained the between-replaces interruption case.

Also fixed unprompted while re-reading: F3 still said the flag was `truncated: true` after F8
renamed it `truncation_suspected` — the exact enumeration-drift failure that #1 is about.

---

## Round 5 — Codex: `VERDICT: REVISE` (2 findings)

1. **The sidecar pairing check is still insufficient.** Equal line counts do not prove the meta
   describes the current JSONL; a provisional refresh or same-length replacement leaves stale
   metadata with an unchanged `jsonl_row_count`. *Fix:* store a content hash and require it to
   match, with a test for stale metadata at unchanged row count.
2. **The CSV contract contradicts the truncation verification.** `gsc_queries.csv`'s schema omits
   `truncation_suspected` while check 27b requires CSV rows to carry it.

### Claude's response — Draft 6

**Both accepted.** #1 is the sharpest finding of the whole review: the case Codex describes is
not an edge case, it is the *ordinary* write this tool performs — a provisional refresh rewrites
the last three days **without changing the line count**, so a stale sidecar would have passed a
count check on every routine re-pull. The meta now carries `jsonl_sha256` over the exact bytes;
both hash and count must match; check 27f gained the "sha mismatch, count still matching" case;
and check 34b now injects its interruption *during a provisional refresh specifically*, so the
test proves the hash rather than the count is doing the work. Noted in the plan that this is the
same mechanism, for the same reason, that MMC's briefing cursor already uses to bind itself to
the exact `inputs.json` it sent. #2 applied: `truncation_suspected` added to the CSV schema and
asserted per-row.

---

## Round 6 — Codex: `VERDICT: REVISE` (1 finding)

1. **The acceptance matrix omits the new critical check.** Part 5 describes the same-count SHA
   mismatch case, but the A2 gate still read "checks 20–27f" and 27f said "six cases" while
   listing seven — so the stale-metadata safeguard could be omitted from the implementation gate.

### Claude's response — Draft 7

Accepted. The SHA-mismatch-with-matching-row-count case was **promoted out of 27f into its own
numbered check 27g**, 27f is back to six cases (a–f), and the A2 gate reads 20–27g. A safeguard
that lives as an unnumbered sub-case of another check is a safeguard that can be skipped without
anyone noticing.

---

## Round 7 — Codex: **`VERDICT: APPROVED`**

> "Draft 7 is implementable without guessing. The SHA pairing now detects ordinary same-count
> provisional-refresh failures, the CSV contract is consistent, and the A2 gate explicitly
> includes 27g. I found no remaining defect that would produce wrong output or materially waste
> implementation effort."

**Converged at round 7 of a maximum of 8, with 47 findings raised and 47 accepted** (one — R2 #14,
the scope cut — accepted in part, with the declined half and its reasoning recorded above).

## What the argument actually produced

Five defects in the *plan's own measurements*, every one found by checking a Codex claim rather
than accepting it — and three of them were errors in my counting, not in Codex's:

- **D1** — Draft 1 counted 5 GSC observations. There are **9**. The measuring script did not
  normalise schema-v1 snapshots, so it silently discarded four real pulls holding 0/177/245/245
  rows. The plan warning against carried-forward and normalisation errors contained one.
- **D2** — Draft 2 counted the charted universe at 40. It is **61** under the adopted rule (and
  50 under the alternative). The script forgot to seed the 12 spec targets.
- **D3** — Draft 3's Phase B refresh window used `min` where `max` was required, which would have
  re-requested the entire retention window on every invocation and could have requested a start
  date before retention begins — falsifying the entire "gap-proof" argument built on top of it.
- **D4** — Draft 3 described backlinks as "referring-domain growth… a direct indicator of the
  off-page work". The real series is `86 → 80 → 81 → 81 → 81`: it **fell**. Wrong on the data
  and causal on top of it.
- **D5** — Draft 5's sidecar pairing used a row count, which cannot detect the most common write
  the tool makes (a provisional refresh of the last three days leaves the count unchanged).

And one measurement about the live system that nobody had recorded, found while testing Codex's
Round 1 blocker:

**Across all 16 local-rank observations in the entire history there are exactly 31 non-null
positions, and all 31 are index 0 of their location's batch — zero exceptions.** Ten of the
twelve daily rank targets have never been queried once; their `null` means "not asked", not "not
ranking". This independently corroborates `PLAN-SEO-PROGRAM-INTEGRATION.md` P0.1's cost estimate
(2 billed DataForSEO tasks per run today vs 12 once tasks actually execute) from a completely
different direction, and it is why this plan ships **no numeric local-rank chart at all** — a
line drawn through that data would have been a fabrication with a competitor's rankings in it.
