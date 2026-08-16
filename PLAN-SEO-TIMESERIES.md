# Plan: a keyword-level SEO time series and a visual progress dashboard

_Draft 7 by Claude (Opus 5), 2026-08-16, after Codex Rounds 1–6 (15 + 14 + 13 + 2 + 2 + 1 findings).
Argument transcript: `PLAN-REVIEW-LOG-SEO-TIMESERIES.md`._

**Operator's request (2026-08-16).** Log the data returned by each GSC / DataForSEO run so it
can be displayed as a graph tracking progress over time for each tracked keyword — "I would
be able to see a graphical representation of week by week progress as we improve our rankings
due to the SEO work that we're performing." Format is the implementer's call; "at least a
little bit visually appealing"; "I don't need anything complicated." Nate is unavailable
during this work, so every judgment call is either decided here with its reasoning stated, or
listed in **Open questions** with an explicit stated default.

**Scope, stated accurately.** This is a **read-and-render** feature. It moves no tier
boundary, creates no proposal, mutates no spec, touches no approval path, adds no credential,
and modifies no file under `D:\Dev\MMC`. Phase A is *pure derivation from artifacts already on
disk* — it cannot change what any run does. Phase B adds one **read-only** GSC call under an
already-granted read-only scope, outside the run contract. Nothing here pushes, merges, or
deploys.

---

## Part 1 — The measured facts this plan is built on

All measured 2026-08-16 from `projects/art/loops/seo/runs/` — **23 run directories, 22 of them
snapshot-bearing, one (`2026-08-10T12-00-01-321Z-2qbkux`, `status: partial-failure`) with no
snapshot** — plus a direct read of the connector code. **Drafts 1 and 2 each got several of
these wrong and Codex caught them; every correction is marked and kept.** Every number below
was re-derived after normalising schema-v1 snapshots, which is what exposed the first round of
errors.

> **Counts are never hard-coded in the implementation.** The builder enumerates run
> directories; the numbers in this document are dated measurements used to write assertions
> against, not constants the code depends on (Codex R2 #1).

### F1 — 23 run directories, but only 9 GSC observations — and the naive read gets even *that* wrong

`_build_snapshot()` (`run_loop.py:222`) carries every un-refreshed section forward verbatim,
so each run writes a complete snapshot most of whose sections are copies.

| Section | Dedup key | Snapshots containing it | **Distinct observations** |
|---|---|---|---|
| `search_analytics` (GSC) | `pulled_at` | 22 | **9** (1 empty → 8 usable) |
| `local_rank` (DataForSEO) | `as_of` | 18 | **16** |
| `backlinks` | `as_of` | 18 | **5** |
| `technical_health` pagespeed | `pagespeed_as_of` | 18 | **7** |
| `technical_health` indexation | `indexation_as_of` | 18 | **7** |

**Charting one point per run fabricates 13 GSC observations that were never measured.** This
is the central correctness constraint and the reason the fact table is keyed by *section
timestamp*, never by run. MMC already applies exactly this rule, narrowly, to site-wide totals
(`D:\Dev\MMC\collector\gsc_history.py` keys on `pulled_at`). This plan generalises it.

> **Correction to Draft 1 (Codex R1 #2, and worse than Codex stated).** Draft 1 claimed 5 GSC
> observations. The true count is **9**. The four runs of 2026-07-16 … 2026-07-21 are
> **schema-v1** snapshots — the bare `search_analytics` dict at top level with no
> `schema_version` key — and Draft 1's own measurement script read `doc["search_analytics"]`,
> found nothing, and recorded them as empty. **They are not empty**: they hold 0, 177, 245 and
> 245 keyword rows respectively.
>
> This is the exact failure mode the plan exists to prevent, committed inside the plan that
> warns about it. It is retained here in full rather than quietly fixed, because it is the
> strongest available argument for **check 3** (v1 normalisation) being a required test rather
> than a nicety: the normalisation is not defensive housekeeping, it is load-bearing, and
> skipping it silently deletes a third of the history.

### F2 — The GSC numbers are a 28-day *rolling* window, not a week

`run_loop.py:311-317` requests `start_date = today − 27`, `end_date = today`. Consecutive
weekly pulls **overlap by 21 of 28 days**. The briefing's "+6 clicks week over week" is the
difference of two overlapping trailing sums, not six clicks earned that week. Position is
likewise an impression-weighted mean over 28 days, so a real ranking improvement is damped
~4× and lags up to four weeks.

Worse for a "progress" chart: observations are **irregularly spaced** (2026-07-16, 07-18,
07-21, 07-21 *nine minutes later*, 07-24, 08-03, 08-04, 08-04 *eight minutes later*, 08-11).
Two pairs are minutes apart and necessarily near-identical; one gap is ten days. Plotted on an
even x-axis this reads as a jagged series with a plateau. **The x-axis must be a real time
axis** (points positioned by date, not by index), and near-duplicate pulls must be visibly
labelled as such.

**Consequence:** Phase A labels every GSC series **"trailing 28 days ending <date>"** and
never uses the word "weekly". Phase B is what makes a true week-over-week chart possible.

### F3 — Row counts are climbing toward a silent cap _(corrected)_

Rows per distinct GSC observation, in order:
**0 → 177 → 245 → 245 → 397 → 716 → 716 → 716 → 910.**

`gsc.py:77` sends `rowLimit=1000` and never paginates (already a recorded defect —
`PLAN-SEO-PROGRAM-INTEGRATION.md` P0.3). At this trajectory the next pull or two hits 1000 and
**site-wide totals silently stop growing** — a plateau that is an artifact of truncation, not
of the site.

This plan does not fix pagination (P0.3's job). It **detects and displays** it: any observation
whose row count is ≥ the request's assumed `rowLimit` is flagged **`truncation_suspected`** (the
name matters — the limit itself is an assumption, see F8), drawn with a distinct marker, and
banners the page. A capped total is never presented as a measured total, and its delta reads
"not comparable" rather than a number (§3.5).

> _Draft 1 wrote the sequence as "397 → 397 → 716 → 716 → 910", double-counting a
> carried-forward snapshot (Codex R1 #2). Corrected above._

### F4 — The GSC entity is `(query, page)`, not `query` _(new — Codex R1 #4)_

In the 2026-08-11 observation: **910 rows, 707 unique queries, 129 queries appearing on more
than one page.** A dashboard promising "one row per keyword, its page, its position" cannot be
built from this without an aggregation rule, and picking one page silently discards the rest.

The clicked rows make the problem concrete:

| query | page | clicks | impressions | position |
|---|---|---|---|---|
| accelerated rehab therapy | `https://acceleratedrehabtherapy.com/` | 15 | 47 | 1.0 |
| accelerated rehab therapy | `http://acceleratedrehabtherapy.com/` | 3 | 8 | 2.0 |
| accelerated rehab | `https://…/` | 2 | 15 | 8.7 |
| best vestibular doctor near me | `https://…/` | 1 | 1 | 1.0 |
| dry needling near me | `https://…/` | 1 | 4 | 1.5 |
| stellate ganglion block near me | `https://…/` | 1 | 2 | 3.0 |

The top brand query is split across `http://` and `https://` variants of the *same page*.
Charted naively that is two series, one apparently losing 3 clicks to the other.

**Rules adopted:**
- **Page normalisation** before anything else: scheme → `https`, host lower-cased and `www.`
  stripped, port dropped, fragment/query dropped, path preserved verbatim. The two homepage
  rows above merge into one.
- **Storage entity is `(query, normalized_page)`**; rows sharing that key after normalisation
  are merged by `clicks = Σ`, `impressions = Σ`,
  `position = Σ(position × impressions) / Σ impressions`.
- **A query-level rollup** is computed with the same arithmetic across that query's pages, and
  carries `page_count`. Any query-level position is labelled *"across N pages"* whenever
  `page_count > 1`. Never a bare number implying one page.
- **A query absent from an observation is `null` (no observation), never `0`.** GSC omits rows
  below its own disclosure floor; absence is unknown, not zero. No zero-filling, no
  interpolation, no carry-forward into a chart.

**Measured effect of normalisation** (Codex R2 #3): the latest observation has **129
multi-page queries before** page normalisation and **69 after** — the other 60 were purely
`http://` vs `https://` duplicates of one page. Both numbers are asserted in check 11; the
129 is explicitly the *pre*-normalisation diagnostic, and 69 is the real post-normalisation
count. Drafts that quote only 129 are quoting the wrong side of the transform.

### F5 — The GSC long tail is thinner than Draft 1 claimed _(corrected)_

Of the 910 rows in the latest observation: **397 rows have exactly 1 impression**, 542 have
≤ 2, and **only 6 rows — 5 distinct queries — have any clicks at all.**

> _Draft 1 said "900 of them are one-impression noise" (Codex R1 #3). That was rhetoric, not a
> measurement. The corrected numbers are above and are what the universe rule uses._

**Measured universe size — and the rule it depends on** (Codex R2 #2). "Top 25 by impressions"
is ambiguous between raw `(query, page)` rows and rolled-up queries, and the two give different
answers:

Both rules seed the set with the 12 `spec.targets` queries, then union per observation; the
only difference is the unit that "clicked" and "top 25 by impressions" are evaluated over
(Codex R3 #7 — the comparator must be defined or check 19's `50` is unfalsifiable):

| Rule | Unit evaluated | Universe size |
|---|---|---|
| raw-row (comparator only) | each `(query, page)` row **as it appears in the snapshot, before page normalisation**; a row is "clicked" if `clicks > 0`; top 25 taken over rows by `impressions`; the query string is what enters the set | 50 |
| **rolled-up (adopted)** | each query **after page normalisation and per-query aggregation** (F4); "clicked" if the query's summed clicks > 0; top 25 taken over queries by summed impressions | **61** |
| spec targets alone | — | 12 |

Both de-duplicate by exact query string (GSC returns queries already lower-cased); neither
applies `keyword_exclusions`.

**Adopted: rolled-up queries**, because that is the entity the operator thinks in ("keywords")
and it is a strict superset of the raw-row rule (every raw-rule member is also a rolled-up
member; 11 queries are added, including `physical therapy greeley co` and
`auto accident chiropractor denver` — real commercial variants the raw rule was hiding).

> _Draft 1 guessed 60–90. Draft 2 said 40 — measured, but with a script that forgot to seed the
> set with the 12 spec targets. The correct figure under the adopted rule is **61**. Third
> counting error in three drafts, which is the argument for check 19 asserting the universe
> rule's output rather than trusting a number in prose._

**Exclusions are marked, never dropped.** Some universe members are `spec.keyword_exclusions`
matches (`accelerate health lakewood`, `accelerated performance`) or obvious scraper-footprint
queries (`trauma recovery after accident injury (intitle:links | …)`). They stay in the table
with an "excluded from proposals" badge. Hiding them would make the dashboard disagree with the
raw data for reasons the operator cannot see; `keyword_exclusions` governs *what the loop
proposes*, not *what the operator is allowed to look at* — the same reporting-not-filtering rule
`PLAN-SEO-PROGRAM-INTEGRATION.md` P0.3 applies to `brand_terms`.

### F6 — Local rank: **31 of 31 non-null results in all of history are index 0 of their batch**

_This began as Codex R1 #1 (a BLOCKER) and the check made it considerably worse than Codex
stated._

Three independent defects, all already recorded in `PLAN-SEO-PROGRAM-INTEGRATION.md` P0.1:

- **D4 — batching.** `pull_local_rank` (`dataforseo.py:238-247`) builds one task array per
  location and issues **a single POST** to `https://api.dataforseo.com/v3/serp/google/organic/live/advanced`,
  then pairs `body["tasks"][i]` to `targets[i]`. The live endpoint processes only the first
  task.
- **D1 — no domain check.** The match is `target["page"] in item["url"]` — a bare substring
  test against any organic result's URL.
- **D5 — wrong field.** `organic_rank_position` is populated from `rank_absolute`, which counts
  *all* SERP features, not the organic index.

**The empirical test.** Across all 16 local-rank observations there are exactly **31 non-null
positions. All 31 are index 0 of their location's batch. Zero exceptions.** Ten of the twelve
daily targets have therefore **never been queried, once, in the entire history** — their
`null` means "not asked", not "not ranking". And every one of the 31 that *was* asked returns
`rank_absolute` for a URL matched by bare substring; the resolved URLs include
`www.mgmc.org`, `occ-ortho.com`, `beaminghealth.com` and `denverhealth.org` — competitors —
alongside a handful of genuine `acceleratedrehabtherapy.com` matches.

This corroborates P0.1's independent cost estimate (2 billed tasks/run today vs 12 once tasks
actually execute) from a completely different direction.

**Decision, adopting Codex R1 #1 in full and then going further:** **no numeric local-rank
chart of any kind, historical or current.** Not a line, not a sparkline, not a "latest rank"
stat tile. There is no correct way to draw a rank line from data where 83% of the series were
never collected and the remainder is a mixed-feature index attributed by substring.

**Batch position is derived, not stored — so its provenance is explicit** (Codex R2 #4).
`snapshot.json` rows carry no `batch_index`. It is reconstructed as *the row's ordinal within
the contiguous run of rows sharing its `location_name`*, which is exactly the order
`pull_local_rank` appends them in (`dataforseo.py:250-275`: one location at a time, targets in
order). Two guards, because an inferred field driving a `not-queried` verdict must not be
guessed:

- **Cross-validation must replay the connector's own expansion, not compare a flat list**
  (Codex R3 #5 — verified: the `2026-07-24` run's `run.json` carries **12 targets with no
  `location` key** and `locations: ["Greeley","Denver","UNC Campus"]`, which the connector
  expanded into 36 rows). The validator therefore reproduces `pull_local_rank`'s grouping
  exactly: a target **with** a `location` key joins that location's list; a target **without**
  one is appended to **every** location's list, iterating `locations` in the order `run.json`
  records them; group order is first-appearance order; within a group, target order is
  preserved. It then asserts the reconstructed `(location_name, keyword, page)` sequence equals
  the snapshot's row sequence, **element by element**, not as a set.
- **`unknown` on any mismatch.** Non-contiguous `location_name` grouping, a length mismatch, a
  location-order mismatch, a target-order mismatch, or a missing/absent
  `dataforseo-local-rank` tool call all yield `batch_index: "unknown"`.
- **`unknown` is never `not-queried`.** An `unknown` row is classified `unknown` and is
  reported as such in the panel. Silence is preferable to an inferred accusation.

What Phase A ships instead of a chart is a **collection-integrity panel** — a table stating,
per target: *queried / never queried / unknown*, and for the queried ones, the classification
of each observation as `own` / `competitor` (with the competitor's registrable domain) /
`absent`, plus the plain-text finding above. That is genuinely useful — it quantifies D4's
blast radius from real data and gives P0.1 its acceptance baseline — and it cannot be misread
as a ranking trend.

**The invalid number is quarantined, not displayed** (Codex R2 #5). "No chart" does not stop a
table from presenting `rank_absolute` as if it meant something. So the raw value is **never
rendered in the panel at all** — not as a column, not as a stat, not in a tooltip. It survives
**only** in `series.json` under a `forensic` sub-object, under the key
`raw_rank_absolute_not_a_rank`, so anyone reading it has been told what it is by the field name
itself. *(Draft 3 also said "and in the CSV"; the only CSV this plan defines is
`gsc_queries.csv`, whose schema has no local-rank fields — Codex R3 #8. There is no local-rank
CSV, and the claim is withdrawn rather than a second CSV invented.)*

Numeric local-rank charting is **deferred behind P0.1**: it lands only after D4/D1/D5 are
fixed *and* P0.1's coded rebaseline cutoff exists, and it charts only post-cutoff data. Stated
as a dependency in Part 6, not as a promise here.

### F7 — The GSC window is not in the snapshot _(field names corrected — Codex R1 #8)_

`search_analytics` records `pulled_at` but not the date range. That lives in `run.json` at
`tool_calls[].args.start_date` / `.end_date` — **snake_case**, as written by
`run_loop.py:314-315`. (Draft 1 wrote `startDate`/`endDate`, which are the *API's* names, not
the artifact's.) The builder joins both files. When `run.json` is missing, or has no `gsc`
tool call, the window is `null`, the point is labelled "window unknown", and no window is
guessed.

### F8 — Other structural discontinuities

- **`local_rank` target set changed mid-history**, and the boundary is *inside* a single day
  (Codex R2 #10 — "from 2026-07-26 on" was temporally false): observations
  `2026-07-24T03:09:56Z` and `2026-07-26T05:42:02Z` carry **36** rows (3 locations × 12
  keywords); `2026-07-26T05:58:58Z` **and every observation after it** carries **12** (2
  locations × 6, the `location:`-pinned set). The two 2026-07-26 observations are 16 minutes
  apart and on opposite sides of the change. Series that vanish are not zeros.
- **One of the 23 run directories has no snapshot** (`2026-08-10T12-00-01-321Z-2qbkux`,
  `status: partial-failure`). Normal; must not abort a build.
- **`sample_size` in `search_analytics` is total impressions**, not row count (verified: 10308
  on the 2026-08-11 pull). Row count must be `len(keywords)`. Confusing the two silently
  changes every truncation check.
- **`row_limit` is not recorded anywhere in the artifacts** (Codex R2 #9). `run.json`'s `gsc`
  tool call records only `site_url`, `start_date`, `end_date`; the `rowLimit=1000` lives as a
  default in `gsc.py:77`. The builder therefore cannot *know* a historical request's limit. It
  records `row_limit: 1000` with `row_limit_source: "assumed-connector-default"` and names the
  flag **`truncation_suspected`**, never `truncated` — an assumption-derived flag must not be
  worded like a measurement. If a future connector changes the limit, the assumption is wrong
  and the field says so. Recording `row_limit` in `run.json`'s args is a one-line improvement
  that belongs to P0.3 (which is already editing `gsc.py`); it is **noted, not done here**,
  because Principle 2 forbids this work from touching the run path.

---

## Part 1b — What building it found (2026-08-16, after the plan was approved)

Two facts only obtainable by running the thing, both of which change claims made above.

### B1 — There is no pre-loop baseline. GSC's history for this property starts 2026-07-15.

Part 4 argued Phase B would backfill "~16 months … including the months *before* this loop
existed, which is the only available pre-intervention baseline". **It does not.** The live
backfill requested 481 days (2025-04-23 → 2026-08-16) and GSC returned **31 rows, the earliest
2026-07-15** — one day before this loop's first run. The property simply has no data before
then.

The tool handled it exactly as designed (it records `requested_span_days: 481`,
`returned_row_count: 31`, and warns rather than presenting a short response as complete), so
nothing is wrong with the code — but the plan's headline justification for Phase B was
optimistic and is corrected here. **Phase B is still worth having**: it produces genuine
non-overlapping weekly figures from 2026-07-15 on, which Phase A structurally cannot. It just
does not buy a before-and-after.

### B2 — The loop has been seeing about a third of the site's actual clicks

Measured across the *same* 28-day window (2026-07-15 → 2026-08-11, all 28 days present in the
daily history):

| | clicks | impressions |
|---|---|---|
| Site total (`dimensions: ["date"]`) | **72** | 13,540 |
| What the loop records (`dimensions: ["query","page"]`) | **23** | 10,308 |
| Withheld from the per-query view | **49** | 3,232 |

Google withholds queries it treats as rare, so a per-query pull returns only the disclosed
subset. Every conclusion this workspace has drawn about traffic volume — including
`PLAN-SEO-PROGRAM-INTEGRATION.md`'s central finding, *"23 clicks in 28 days, 20 of them
brand"* — rests on the smaller number. The site was getting roughly **three times** that.

**Stated with its limits, because the tempting inference is not measured.** What is measured:
the site total, the per-query total, and the gap. What is **not** measured: whether the 49
withheld clicks are brand or non-brand. Anonymised queries skew long-tail, which *would*
suggest mostly non-brand — and if so, "3 non-brand clicks" is badly wrong — but that is an
inference. It is rendered on the dashboard as an open question, not resolved.

This is surfaced as its own dashboard panel ("What the loop can and cannot see"), computed only
for windows the daily history covers in full, since a partial overlap would understate the site
total and manufacture a discrepancy. It is the strongest argument for Phase B, and it was not
one this plan predicted.

---

## Part 2 — Design principles

1. **Snapshots stay the system of record.** The time series is a derived, rebuildable cache.
   Delete it; one command reconstructs it byte-identically. Nothing in it is authoritative.
2. **Zero risk to the run contract.** No code is added to `run_loop.py`, no scheduled task or
   shared wrapper is edited, no snapshot section is added. A broken dashboard build cannot
   fail, degrade, or delay a loop run.
3. **Deduplicate by measurement, never by run** (F1).
4. **Never invent a data point.** No interpolation, no zero-fill, no carry-forward into a
   chart. A gap is a gap. Absence is `null`, not `0` (F4).
5. **Never draw a number the data cannot support.** F6 is the governing example: the correct
   chart for uncollected data is no chart.
6. **Label what the number actually is** — "trailing 28 days ending X", "across N pages",
   "truncated at row limit", "provisional".
7. **Determinism.** Same snapshots in → byte-identical artifacts out, so `--check` can assert
   freshness. The build timestamp is the one permitted exception and is handled explicitly
   (Part 3.3).
8. **Self-contained, inert output.** No network at build or view time; no external resource of
   any kind; **no JavaScript** (Part 3.4).
9. **Python only** (`CLAUDE.md`), standard library only. No new `requirements.txt` entry.

---

## Part 3 — Phase A: derive and render from what is already on disk

**Deliverable:** `./.venv/Scripts/python.exe tools/seo_timeseries.py art seo` reads every
`runs/*/{snapshot.json,run.json}`, writes a canonical dataset plus a self-contained HTML
dashboard, and prints a one-screen summary. Sub-second. No network.

### 3.1 — `tools/lib/timeseries.py` — extraction (pure, no writes, no network)

```
normalize_snapshot(doc)          -> v2-shaped dict (handles the v1 bare-dict form; F1)
normalize_page(url)              -> canonical page key (F4)
derive_batch_index(rows, run_json)-> per-row ordinal or "unknown" (F6)
classify_local_rank_row(row, domain, batch_index) -> own | competitor | absent | not-queried | unknown
extract_observations(runs_dir)   -> ObservationSet(series..., warnings=[...])
```

Five independently-keyed series, each entry `{observed_at, first_seen_run_id, …}`:

| Series | Key | Payload |
|---|---|---|
| `gsc` | `search_analytics.pulled_at` | `window_start`/`window_end` (F7), `row_count`, `row_limit` + `row_limit_source`, `truncation_suspected`, totals, brand/non-brand split, merged `(query, page)` rows |
| `local_rank` | `local_rank.as_of` | per target: `location, keyword, page, batch_index, classification, competitor_domain, result_url`, plus `forensic.raw_rank_absolute_not_a_rank` — **no field named `position`, and no rank number outside `forensic`** |
| `backlinks` | `backlinks.as_of` | `referring_domains`, `backlinks` |
| `pagespeed` | `technical_health.pagespeed_as_of` | per page: `performance_score, cwv_status` — **latest observation only** (§3.5) |
| `indexation` | `technical_health.indexation_as_of` | per page: `verdict` — **latest observation only** (§3.5) |

Rules baked in:

- **Totals computed, not read**, over *all* rows unfiltered:
  `total_clicks = Σclicks`, `total_impressions = Σimpressions`,
  `ctr = round(clicks/impressions, 4)`,
  `avg_position = round(Σ(position×impressions)/Σimpressions, 1)`.
  **The rounding is specified, not incidental** (Codex R1 #13): those exact roundings are what
  `looping.py:_gsc_totals()` applies, and check 9 asserts equality against the real 2026-08-11
  snapshot. Full precision is retained internally; rounding is applied at the boundary only.
  *(Verified by hand while writing this: 23 / 10308 / 0.0022 / 24.2 — identical.)*
- **Brand/non-brand split** from a `brand_terms` list, read from `spec.md` when P0.3 adds that
  key, else defaulting to `["accelerated rehab", "accelerated rehab therapy"]`. Reporting only,
  never a filter — `PLAN-SEO-PROGRAM-INTEGRATION.md`'s central finding is that 20 of 23 clicks
  are brand-navigational, and an unsplit click line is precisely the chart that hides it.
- **Local-rank classification** per F6, including `not-queried` (derived from `batch_index > 0`
  under the current connector), with host normalisation and **segment-boundary** path matching
  so `/massage/` never matches `/massage-therapy-guide/`.
- **Empty observations dropped** (the 2026-07-16 zero-row pull); non-empty v1 observations
  **kept** (F1).
- **Unreadable snapshot** → skipped, appended to `warnings`, surfaced in CLI output *and* the
  dashboard footer. Never aborts; never silent.

### 3.2 — The charted universe, versioned

> **universe** = the 12 `spec.targets` queries
> ∪ every **rolled-up query** with ≥1 click in any observation
> ∪ every **rolled-up query** in the top 25 by impressions in any observation
>
> where "rolled-up query" means *after* page normalisation and per-query aggregation (F4) —
> **not** raw `(query, page)` rows. The rule is stated at this precision because the two
> readings differ by 11 members (F5).

Union-across-all-time, so a query that falls out of the top 25 keeps its line — falling off is
exactly the movement worth seeing. **Measured size today: 61 queries** (F5).

**Membership is versioned** (Codex R1 #5). `series.json` records
`universe: {version: <sha256 of the sorted member list, 12 hex>, members: [...], previous_version, added: [...], removed: []}`.
Any build whose membership changes prints the diff and renders a notice band on the page. The
set is never silently different from the one the operator looked at last week. `removed` is
always empty by construction (union-across-time) — it exists so that a future rule change that
*can* remove members is forced to declare it.

### 3.3 — `tools/seo_timeseries.py` — CLI and writer

```
seo_timeseries.py <project> <loop> [--out DIR] [--check] [--verify]
```

Path containment via `lib/paths.assert_within` on every read and write. Outputs to
`projects/<slug>/loops/<loop>/timeseries/`:

| File | What |
|---|---|
| `dashboard.html` | self-contained page, data embedded |
| `series.json` | canonical derived dataset (universe rows, all totals, universe version, warnings) |
| `gsc_queries.csv` | tidy long table, **every** row: `observed_at, window_end, query, page, clicks, impressions, position, brand, truncation_suspected` |
| `gsc_daily.jsonl` + `gsc_daily.meta.json` | Phase B only — daily history and its pull metadata |

**All of these are gitignored, Phase B's included** — see Q1's default below (Codex R3 #3:
Draft 3 listed only three files while Q2 defaulted to building a fourth containing client data).

**Determinism and the build stamp** (Codex R1 #9). The generated-at stamp is the sole
non-deterministic element. It is emitted once, in a single line of the footer, in a fixed
format, and:
- `--verify`'s determinism check builds twice with an injected fixed clock and asserts
  byte-identical output;
- `--check` rebuilds into a temp dir and diffs with **that one line normalised**, so "the
  dashboard is stale" is detectable and a re-run's clock tick is not a false positive.

**A redaction gate before write.** Snapshots are already redacted, so the risk is low and the
check is nearly free: the writer runs `lib/redact.redact_deep` over the payload and **refuses
to write** if any known alias's raw value appears, mirroring MMC's canary gate. It makes "safe
to commit" an asserted property rather than an assumption.

### 3.4 — The dashboard: static HTML + SVG, zero JavaScript

**Codex R1 #12 is upheld.** `CLAUDE.md`'s Python-only rule exists because of a real prior
incident (`RISK-REGISTER.md` R8), and the standing instruction is to never introduce another
implementation language without explicit prior approval. Nate is asleep and cannot give it.
Draft 1's "small hand-written inline `<script>`" was the wrong call. **The dashboard ships with
no JavaScript at all** — which also eliminates most of Codex R1 #10's injection surface, and
is simply less to get wrong.

What replaces the interactivity:

| Wanted | JS-free mechanism |
|---|---|
| Hover tooltip on a data point | native SVG `<title>` child on each mark — real browser tooltip, no script |
| Expand a keyword's full history | `<details>`/`<summary>` per row |
| Show the numbers behind a chart | a real `<table>` beside every chart inside `<details>` |
| Sort the keyword table | pre-sorted server-side by latest impressions; a second pre-rendered ordering by position change behind a `<details>` |
| Filter to a section | in-page `#anchor` navigation |

This is a documented, deliberate downgrade from the `dataviz` skill's crosshair-tooltip
default, taken because a repo rule outranks a house style. Charts remain inline SVG, direct
labels do the work the crosshair would have done, and the always-present tables mean no
information is reachable only by interaction.

**Escaping** (Codex R1 #10). GSC queries and result URLs are attacker-adjacent external text.
Every interpolation goes through one `html_escape()` covering `& < > " '`, applied to text
nodes *and* attribute values; SVG `<title>` content is escaped identically. No value is ever
placed in a JS context, because there is no JS context. Competitor URLs render as **plain
non-clickable text**, never `<a href>`. Check 15 plants `</script><img src=x onerror=…>`,
`"><svg onload=…>`, and a URL-shaped payload as a query string and asserts they appear inert.

### 3.5 — Page layout

Top to bottom, built to the `dataviz` procedure (form → colour → validated palette → marks →
a11y), light and dark both selected via `prefers-color-scheme`, palette = the skill's validated
default (slots 1–3 for all-pairs forms; status palette reserved for status, always with icon +
label, never colour alone).

**Scope cut, adopting Codex R2 #14 in part.** Draft 2's per-page CWV time series (6 small
multiples) and indexation grid (page × observation) were the most layout-expensive sections on
the page, are not keyword progress, and are **already reported daily by MMC's briefing**
(`looping.py` emits `data_freshness`, `deindexed_priority_pages`, and the CWV scores the
operator sees every morning). They are cut from Phase A as time series. What remains is a
**technical status strip** — latest CWV score range, indexation N/6 PASS, sitemap warnings —
which costs almost nothing, answers "is anything on fire", and has no trend to misread. It
carries **separate `pagespeed` and `indexation` as-of dates** (Codex R3 #9): the two refresh
independently and are carried forward independently, so collapsing them into one "as of" would
present stale data as fresh. Either being absent or older than its cadence renders as
`stale`/`unknown`, never as a bare value.

**Backlinks: narrowed and relabelled** (Codex R3 #10, upheld against Draft 3's own wording).
The real series is `86 → 80 → 81 → 81 → 81` referring domains — it **fell** by six and then
flattened. Calling that "referring-domain growth… a direct indicator of the off-page work",
as Draft 3 did, was wrong on the data and causal on top of it: DataForSEO returns *current
index totals*, which move when its crawler re-indexes, not when work is performed. Kept, but
reduced to **one small labelled table with a sparkline** rather than two charts, titled
*"DataForSEO index totals, as of <date>"*, with no growth or causal language anywhere.

1. **Header** — client, domain, observation counts per series, freshness strip (GSC "trailing
   28d, last pull N days ago"), the technical status strip, truncation banner if any,
   universe-change notice if any, build warnings.
2. **KPI row** — non-brand clicks, total clicks, impressions, avg position, referring domains.
   Value + delta vs the previous **distinct** observation + sparkline. Delta direction stated
   in words (position: lower is better).
3. **Clicks over time** and **Impressions over time** — two separate charts, never a dual axis.
   Brand vs non-brand as two series. **Real time axis** (F2), points at their true dates,
   window-end labelled. **Near-duplicate pulls are operationalised, not just promised**
   (Codex R3 #11): any observation whose `pulled_at` is **< 24 h** after the previous one is
   annotated *"re-pull, N h after previous — near-identical by construction"* on its marker and
   in the table, and is excluded from delta arithmetic. Check 27d asserts this on the DOM.
   **Three** of the eight real observations are near-duplicates, not two: the two minute-apart
   pairs (`2026-07-21T03:30:49Z`/`03:39:45Z`, `2026-08-04T03:05:49Z`/`03:13:55Z`) **and**
   `2026-08-04T03:05:49Z`, which sits 12.5 h after `2026-08-03T14:37:42Z`. All three return
   totals byte-identical to their predecessor, and no non-flagged observation does — which is
   why the threshold is 24 h rather than "same minute", and why the implementation's check
   asserts that *property* rather than a hardcoded count. _(Measured during implementation;
   an earlier count of "two" here was written from the minute-apart pairs alone.)_
4. **Average position over time** — line, **y-axis inverted** (1 at top) and labelled as such;
   site-wide, plus small multiples for the six `priority_pages`.
5. **Tracked-query table** — one row per universe member: query, `page_count`, latest position
   (with "across N pages" when `page_count > 1`), Δ vs previous observation, clicks,
   impressions, sparkline. `<details>` expands the full per-`(query, page)` history. **This is
   the "each of the various keywords" view the operator asked for.**
6. **Local-rank collection integrity** — the F6 panel. A table, a plain-language finding, and
   **no rank chart and no rank number**.
7. **DataForSEO index totals** — referring domains and backlinks, one small labelled table with
   sparklines, no growth or causal wording.
8. **Footer** — methodology in plain language: what each number is, what the 28-day window
   means, what `own`/`competitor`/`absent`/`not-queried`/`unknown` mean, which observations
   were dropped and why, an explicit statement that **clicks and positions are not leads,
   calls, or booked patients**, and the exact command that regenerates the page.

**Truncation is a rendering requirement, not just a flag** (Codex R2 #11). When an observation
carries `truncation_suspected`, every surface derived from it — the KPI tile, its delta, the
chart marker, the CSV row, and the axis label — is marked partial, and the delta is rendered as
"not comparable" rather than as a number. Check 22 asserts this on the rendered DOM, because a
`true` in a JSON file that the page ignores is not a safeguard.

---

## Part 4 — Phase B: a *true* week-over-week series

Phase A can only show what was measured: nine overlapping 28-day windows, four of them within
minutes or days of each other. Real, but not "week by week", and F2 says arithmetic cannot
convert one into the other.

**Phase B buys the real thing with one extra API call.** A second Search Analytics request with
`dimensions: ["date"]` returns one row per day, site-wide — **no new credential** (the stored
`art-gsc-readonly` already carries `webmasters.readonly`) and no per-call cost (GSC is free;
DataForSEO is the metered one).

That single call yields **true daily clicks/impressions**, and a backfill covering GSC's full
retention on the first run — including the months *before* this loop existed, which is the only
available pre-intervention baseline.

**The row-count claim is computed, not asserted** (Codex R2 #12). Draft 2 said "≤480 rows for
16 months"; 16 calendar months is up to 489 days, so the constant was wrong and the headroom
under `rowLimit=1000` was being asserted rather than checked. The tool therefore **computes the
requested span in days**, asserts `span ≤ row_limit` before issuing the request, and refuses
with a clear message rather than silently truncating if a future window ever exceeds it.
GSC's retention is treated as *approximately* 16 months and is not relied on: the tool asks for
its computed window and accepts however many rows come back, recording `requested_span_days`
and `returned_row_count` so a short response is visible rather than assumed complete.

**Delivery mechanism, chosen for blast radius:** a standalone `tools/gsc_daily.py <project>
<loop>` — **not** a step in `run_loop.py`, **not** a new snapshot section, and **not an edit to
`tools/gsc.py`**. Adding a snapshot section means editing `_build_snapshot()`'s key tuple and
the carry-forward path, inside a run whose `gsc` connector is `critical: True` and aborts the
run on failure. A separate tool cannot abort anything.

**`gsc.pull_metrics()` cannot be reused, and that is why the tool owns its own parser**
(Codex R2 #6 — correct and load-bearing). `pull_metrics` hard-maps `keys[0] → keyword` and
`keys[1] → page` (`gsc.py:103-105`); handed `dimensions: ["date"]` it would faithfully return
a list of rows whose `keyword` is a date and whose `page` is `None`. Draft 2's "reuse the
existing connector" was wrong. `gsc_daily.py` builds its own request body and its own parser
(`keys[0] → date`), and **imports** `gsc.py`'s auth, redaction, error-raising and HTTP helpers
rather than duplicating or modifying them — so the credential path, the secret-map redaction
and the API-error handling stay identical and single-sourced, while `gsc.py` itself is not
edited at all.

**Aggregation, specified** (Codex R1 #6 — CTR and position are not summable):

- `clicks_week = Σ clicks` over the week's days
- `impressions_week = Σ impressions`
- `ctr_week = clicks_week / impressions_week` — **recomputed, never averaged**
- `position_week = Σ(position_d × impressions_d) / Σ impressions_d` — impression-weighted,
  days with `impressions_d = 0` contribute nothing (and cannot, since GSC omits them)
- **A week missing any day is `partial: true`**, rendered dashed and excluded from any
  "vs. last week" delta. Weeks are ISO weeks (Mon–Sun), stated on the axis.
- **Days GSC omits are absent, not zero** — the same F4 rule. A week of 5 present days is
  partial; it is not a week with two zero days.

**Storage:** `timeseries/gsc_daily.jsonl`, one line per `{date, clicks, impressions, position,
provisional}`.

**The refresh window is gap-proof by construction, which is what makes an on-demand tool
safe** (Codex R2 #7). Draft 2 hard-coded "re-pull the trailing 90 days", which silently loses
data if more than 90 days pass between invocations — and Draft 2 also gave Phase B no
invocation path at all, so that gap was not hypothetical. Instead:

> requested window = **`max`**`(last_recorded_date − provisional_days, today − retention_days)` … `today`
> — i.e. **always back to wherever the file actually ends**, never a fixed lookback, and never
> past the retention bound; the full retention window when the file is empty; `--full` forces
> the retention window regardless.

> **Draft 3 wrote `min` here, which is exactly backwards** (Codex R3 #1, a blocker). `min`
> selects the *earlier* of the two dates, so a file ending 200 days ago would request the full
> retention window every single time, and a gap older than retention would request a start date
> **before** retention begins. `max` selects the later — the true "wherever the file ends,
> clamped to what GSC still has". A one-character error that would have made the whole
> gap-proofing argument false; check 30 now tests both a 200-day gap and an
> older-than-retention gap explicitly.

Consequently **any cadence works**: run it weekly, monthly, or twice a year, and the series
still comes back complete, because the tool asks for exactly the range it is missing plus the
provisional overlap. Data is only lost if an invocation gap exceeds GSC's own retention, which
the tool detects (`requested_span_days` clamped by retention) and reports as an explicit
`retention_gap` warning rather than a silent hole.

`provisional_days` defaults to **3** and is a **named, configurable heuristic, not a
completeness guarantee** (Codex R2 #12): GSC does not publish a settling deadline. Those rows
are re-fetched and overwritten on every subsequent invocation, drawn dashed, and excluded from
"latest complete week". Older rows are kept byte-identical.

**Defaults, stated so nothing is guessable** (Codex R3 #4):

| Setting | Default | Note |
|---|---|---|
| `retention_days` | `480` | GSC's ~16-month retention, treated as approximate; a short response is recorded, never assumed complete |
| `provisional_days` | `3` | a named heuristic, not a completeness guarantee |
| `row_limit` | `1000` | asserted against `requested_span_days` **before** the request; refuses rather than truncating |
| stale-banner threshold | `14` days | newest non-provisional day older than this banners the dashboard |

**A metadata sidecar records what the request actually did** (Codex R3 #4). `gsc_daily.meta.json`
holds `{last_pull_at, requested_start, requested_end, requested_span_days, returned_row_count,
retention_days, provisional_days, row_limit, retention_gap, warnings[], jsonl_row_count,
jsonl_sha256}`. The per-day JSONL schema stays minimal; everything about *the pull* lives beside
it, so "was this response complete?" is answerable from the artifact rather than from memory.

**Writes are atomic, and the two files are paired by a checkable stamp** (Codex R3 #13, R4 #2).
Each file is written to a same-directory temp file and `os.replace`d — jsonl first, then meta —
so neither can be left truncated. Two files cannot be replaced in one transaction, though, so
the pairing is made *detectable* instead of assumed: the meta carries **`jsonl_sha256`, a
sha256 over the exact bytes of `gsc_daily.jsonl`**, plus `jsonl_row_count` as a cheap
human-readable cross-check. Both must match.

> **A row count alone is not a pairing proof** (Codex R5 #1). Draft 5 relied on
> `jsonl_row_count`, but a provisional refresh rewrites the last three days *without changing
> the line count* — the single most common write this tool performs. Stale metadata would have
> passed. A content hash is the only thing that actually binds the sidecar to the bytes it
> describes. This is the same mechanism, for the same reason, that MMC's briefing cursor already
> uses to bind itself to the exact `inputs.json` that was sent (`CLAUDE.md`, Phase 7).

**The consistency contract, and what happens when it fails** (Codex R4 #2, R5 #1). The weekly
section renders **only** when both files are present, both parse, `jsonl_sha256` matches the
file's actual digest, and `jsonl_row_count` matches its line count. On any of — meta missing,
meta unparseable, hash mismatch, count mismatch — the page **omits the weekly section entirely**
and renders in its place: *"Daily GSC data is inconsistent (`<reason>`) — re-run
`gsc_daily.py`. Showing trailing-window data only."* It never renders a weekly chart from a
JSONL it cannot vouch for, and it never silently falls back as though Phase B were simply
absent, because "absent" and "broken" are different states and the operator can only fix one of
them. Phase A is entirely unaffected in all cases.

**The dashboard's consumption of it is specified** (Codex R3 #2 — Draft 3 built the file and
never said who reads it). `seo_timeseries.py` reads `gsc_daily.jsonl` + `gsc_daily.meta.json`
when present:

- **Absent** (neither file present) → the page renders Phase A only, and the "Search performance
  over time" section carries a one-line note that a true weekly series is available by running
  `gsc_daily.py`. This is the default state until Phase B ships; nothing else changes.
- **Inconsistent** (one file present, meta unparseable, `jsonl_sha256` mismatched, or
  `jsonl_row_count` mismatched) → the weekly section is omitted and replaced by the explicit
  inconsistency notice above — never silently treated as "absent".
- **Present** → a **new** "True weekly performance" section is added *above* the Phase A
  trailing-window charts, with ISO-week bars for clicks and impressions. **The Phase A charts
  are not replaced or merged** — they measure a different thing (§F2), and silently swapping
  one for the other is precisely the misrepresentation this plan exists to prevent. Each
  section states which source it comes from.
- **Present but stale** (newest non-provisional day > 14 days old) → the weekly section renders
  with the stale banner and its most recent week is drawn dashed.
- **Partial weeks** are dashed and excluded from deltas, as specified above.

**Invocation is explicitly manual/on-demand** (Codex R2 #7). Phase B is *not* described as an
automatically-maintained weekly series, because nothing automates it: Part 7 leaves the
scheduling layer untouched. The gap-proof window above is what makes that honest rather than
lossy.

**Phase B verification** (Codex R1 #7 — Draft 1 tested none of this). **Checks 28–36** in Part 5,
all offline against a fake `http_post` in the established style of `tools/gsc.py`'s own tests.
No live API call is made during the build. *(Draft 3 said "20–27" here and "28–35" in Q2; both
were wrong — Codex R3 #12.)*

**Optional B.2, deferred:** six per-page daily pulls via `dimensionFilterGroups` on `page` for
the six `priority_pages`. Six extra free calls per week, true weekly traffic per service page.
Not built until B.1 has run and been looked at.

---

## Part 5 — Verification

`--verify` self-tests in this repo's established style (printed `PASS`/`FAIL` per check, exit 1
on any failure).

**`lib/timeseries.py --verify`**

1. 22 synthetic runs sharing 9 distinct `pulled_at` values → exactly 9 observations (F1).
2. A carried-forward section never produces a second observation across 10 runs.
3. **A v1 bare-dict snapshot with 177 non-empty rows normalises and is KEPT** — the regression
   test for Draft 1's own error (F1). Asserts the row count survives, not merely that no
   exception is raised.
4. A zero-row observation is dropped; a 177-row one is not.
5. A missing `snapshot.json` is skipped without raising (F8).
6. A corrupt/unparseable `snapshot.json` is skipped **and** recorded in `warnings`.
7. Missing `run.json` → `window: null`, point still emitted, marked window-unknown; a present
   one is read from `tool_calls[].args.start_date`/`.end_date` (F7, snake_case).
7b. **A `run.json` that exists but contains no `gsc` tool call** → same `window: null` +
   window-unknown marker + a `warnings` entry. Draft 2 claimed this branch in F7 and tested
   only the missing-file one (Codex R2 #8).
8. `row_count` comes from `len(keywords)`, **not** `sample_size`; `sample_size` equals total
   impressions on the real 2026-08-11 snapshot (F8).
9. `row_count >= row_limit` → `truncation_suspected: true`, and the record carries
   `row_limit_source: "assumed-connector-default"` (F8, Codex R2 #9). A test asserts the flag is
   *not* named `truncated`.
10. Page normalisation merges `http://…/` and `https://…/` into one entity with `clicks=18`,
    `impressions=55`, impression-weighted position — the real F4 case.
11. Query-level rollup across multiple pages uses impression weighting and sets `page_count`.
    On the real 2026-08-11 snapshot: **129 multi-page queries before normalisation, 69 after**,
    both asserted, and total clicks/impressions are conserved across the transform (Codex R2 #3).
12. A query absent from an observation yields `null`, never `0` (F4).
13. Totals equal MMC's `_gsc_totals()` **including its rounding** (`ctr` 4dp, `avg_position`
    1dp) on the real 2026-08-11 snapshot: `23 / 10308 / 0.0022 / 24.2` (Codex R1 #13).
14. Brand/non-brand split sums back to the unsplit total exactly.
15. Local-rank classification on P0.1's own fixtures: `occ-ortho.com/physical-therapy/` @6 →
    `competitor`; `www.mgmc.org/care/…/physical-therapy/` @33 → `competitor`; ART's own URL →
    `own`; `www.` variant → `own`; `/massage/` vs `/massage-therapy-guide/` → **not** `own`;
    null position → `absent`; `batch_index > 0` → `not-queried`.
16. **The F6 invariant on real data**: across all 16 real local-rank observations, every
    non-null position has `batch_index == 0`. If this ever fails, the connector changed and the
    `not-queried` inference must be revisited — the test says so in its failure message.
16b. **Batch-index provenance** (Codex R2 #4): rows whose `location_name` groups are
    non-contiguous → `batch_index: "unknown"`; a `run.json` whose `dataforseo-local-rank`
    `args.targets` disagree with the snapshot's row order → `unknown`; and an `unknown` row is
    classified `unknown`, **never** `not-queried`.
17. No field named `position` exists anywhere in the emitted `local_rank` series, and no rank
    number appears outside `forensic.raw_rank_absolute_not_a_rank` (F6's decision enforced
    structurally, not by discipline — Codex R2 #5).
18. The 36-row observations (`2026-07-24T03:09:56Z`, `2026-07-26T05:42:02Z`) and the 12-row ones
    (`2026-07-26T05:58:58Z` onward) coexist without inventing 24 nulls (F8).
19. The universe rule's output on the real snapshots is **61** under the adopted rolled-up rule
    and **50** under the raw-row rule — both asserted, so the ambiguity Codex R2 #2 found cannot
    silently reappear. Membership is stable across a rebuild; adding a clicked query changes
    `universe.version` and populates `added` (§3.2). `keyword_exclusions` members are present
    and badged, not dropped.

**`seo_timeseries.py --verify`**

20. Two builds with an injected fixed clock are byte-identical (Principle 7).
21. `--check` exits non-zero on real drift and **zero** when only the build stamp differs.
22. A planted canary secret in the payload blocks the write (§3.3).
23. **Injection**: a query of `</script><img src=x onerror=alert(1)>`, one of
    `"><svg onload=alert(1)>`, and a `javascript:` URL all render inert; assert on the parsed
    DOM, not on a substring (Codex R1 #10).
24. **No external references and no script** (Codex R1 #11 — Draft 1's "no URL in link text"
    was far too weak): parse the emitted HTML and assert **zero** `<script>` elements, zero
    `<iframe>`/`<object>`/`<embed>`, no `src`/`href`/`srcset`/`data`/`poster` attribute with an
    off-page scheme, no `@import` or `url(...)` in any `<style>`, no `on*` attribute anywhere,
    and no `<a href>` pointing at a competitor domain.
25. Every chart has a corresponding `<table>` in the DOM (a11y).
26. Empty input (a project with zero runs) produces a valid page saying so, not a traceback.
27. `--out` pointing outside the project directory is refused (`lib/paths`).
27b. **Truncation is rendered, not merely flagged** (Codex R2 #11): given an observation with
    `truncation_suspected: true`, assert on the parsed DOM that its KPI tile is marked partial,
    its delta reads "not comparable" rather than a number, and its chart marker differs; and
    assert that **every** `gsc_queries.csv` row belonging to that observation carries
    `truncation_suspected` in its own column (Codex R5 #2 — Draft 5's CSV schema omitted the
    column this check requires). A flag no surface reads is not a safeguard.
27c. **The local-rank panel renders no rank number** (Codex R2 #5): assert the panel's DOM
    contains no digits sourced from `raw_rank_absolute_not_a_rank`.
27d. **Near-duplicate pulls are annotated and excluded from deltas** (Codex R3 #11): assert on
    the DOM for the two real cases (`2026-07-21T03:30:49Z`/`03:39:45Z`;
    `2026-08-04T03:05:49Z`/`03:13:55Z`) and that a > 24 h gap is *not* annotated.
27e. **The technical strip carries separate pagespeed and indexation as-of dates** (Codex R3
    #9), and renders `stale`/`unknown` when either is absent or older than its cadence.
27f. **Phase B integration and the sidecar contract** (Codex R3 #2, R4 #2) — six DOM cases:
    (a) neither daily file present → Phase A only + availability note;
    (b) both present and consistent → a **separate** weekly section appears **and** the Phase A
    trailing-window charts are still present and unmodified;
    (c) both present but stale (> 14 days) → weekly section renders with the stale banner and a
    dashed final week;
    (d) `gsc_daily.jsonl` present, `gsc_daily.meta.json` **missing** → weekly section omitted +
    inconsistency notice naming the reason;
    (e) meta present but **unparseable** → same;
    (f) `meta.jsonl_row_count` **mismatching** the file's real line count → same.
    In every one of (a)–(f), Phase A's output is asserted byte-identical to the no-daily-file
    build, so a Phase B fault can never damage Phase A.
27g. **The stale-metadata case a row count cannot catch** (Codex R5 #1, R6 #1 — promoted to its
    own numbered check so it cannot be dropped from the A2 gate): `meta.jsonl_sha256`
    mismatching **while `jsonl_row_count` still matches** → weekly section omitted +
    inconsistency notice, Phase A byte-identical. This is the *ordinary* case, not an exotic
    one: a provisional refresh rewrites the last three days without changing the line count, so
    every routine re-pull produces exactly this shape if the sidecar goes stale.

**`gsc_daily.py --verify` (Phase B only)**

28. The request body sent is `dimensions: ["date"]` with the computed date range, against a
    fake `http_post`; no live call. **And the parser maps `keys[0] → date`** — a response of
    `{"keys": ["2026-08-11"], …}` must yield a `date` field, never a `keyword` field
    (Codex R2 #6: `gsc.pull_metrics` would produce the latter).
29. A full-retention fake response backfills every returned dated row in one call, and
    `requested_span_days` is **computed** and asserted `<= row_limit`, with a refusal (not a
    silent truncation) when a span would exceed it (Codex R2 #12).
30. **Gap-proofing, both directions** (Codex R2 #7, R3 #1): with a file ending **200 days ago**
    the requested start is `last_recorded − provisional_days` (**not** the full retention
    window — the `min`/`max` regression test); with a file ending **older than
    `retention_days`** the requested start is clamped to `today − retention_days` and a
    `retention_gap` warning is emitted; re-running leaves rows older than the provisional
    window byte-identical (idempotence).
31. The last `provisional_days` dates are `provisional: true`, `provisional_days` is
    configurable, and the emitted metadata labels it a heuristic rather than a completeness
    guarantee.
32. Weekly aggregation: `ctr` recomputed from summed clicks/impressions (**not** averaged) and
    position impression-weighted, on a fixture whose naive average differs from the correct
    answer (Codex R1 #6).
33. A week missing a day is `partial: true` and is excluded from deltas.
34. An API failure (non-2xx, and a transport exception) leaves `gsc_daily.jsonl` **unmodified**
    and exits non-zero.
34b. **An interrupted write leaves the files intact and the inconsistency detectable**
    (Codex R3 #13, R4 #2, R5 #1): a failure injected between temp-file write and `os.replace`
    leaves the previous `gsc_daily.jsonl` byte-identical with no partial file behind; **and** a
    failure injected *between* the jsonl replace and the meta replace during a **provisional
    refresh that does not change the line count** leaves a stale meta whose `jsonl_sha256` no
    longer matches — asserted to be caught, proving the hash and not merely the count is doing
    the work.
35. The credential value never appears in the written file or in any error message.
36. `gsc_daily.py` **imports** `gsc.py`'s auth/redaction/error helpers and does not modify
    `gsc.py`: a test asserts `tools/gsc.py`'s public surface is unchanged and that
    `gsc_daily.py` reuses `_auth_headers` and the same secret-map redaction path (Codex R2 #6).

Plus the two manual checks a test cannot do: run the `dataviz` palette validator over the
chosen slots in **both** modes, and **open the page and look at it** for label collisions and
overflow before calling it done.

---

## Part 6 — Relationship to MMC and to the approved SEO plan

**MMC (`D:\Dev\MMC`).** `collector/gsc_history.py` keeps a 12-entry, disk-only, site-wide
totals history keyed on `pulled_at`, feeding the briefing's `gsc_trend`. This plan does not
replace, modify, or read from it. The two overlap only on four numbers, which is why §3.1
computes them with byte-identical arithmetic *and rounding* to `looping.py:_gsc_totals()`, and
check 13 asserts it. **No file under `D:\Dev\MMC` is modified** (standing rule: no cross-repo
commits without asking).

**`PLAN-SEO-PROGRAM-INTEGRATION.md`.** Additive, non-conflicting, and deliberately *downstream*:

- Touches no selector (P5), no evaluation semantics (P0.5), no proposal path, and **no
  connector**. It only reads.
- **P0.1 is a hard dependency for one deferred feature, not for this plan.** Numeric local-rank
  charting does not exist in Phase A and lands only after D4/D1/D5 are fixed *and* P0.1's coded
  rebaseline cutoff exists, charting post-cutoff data only (F6). Phase A's integrity panel is
  designed to become P0.1's acceptance baseline: it already states, per target, what was and
  was not collected.
- **Anticipates P0.1's output shape**: when rows gain `matched_domain`/`organic_rank_group`,
  the builder prefers those fields and falls back to its own classification for historical
  rows — one branch, already designed.
- **Anticipates P0.3**: `brand_terms` is read from `spec.md` when present, and truncation is
  surfaced rather than fixed.
- A partial, honest down-payment on **P4** without claiming to be P4: it visualises *search*
  metrics, and the footer says in plain language that clicks and positions are not leads.

---

## Part 7 — Automation

**Decided: none.** (Revised from Draft 1 after Codex R1 #15.)

Draft 1 proposed adding the rebuild to `tools/scheduled/task.cmd`. That file is the **shared**
wrapper every scheduled job runs through — `_demo`, the watchdog, everything — so a rebuild
there would fire for jobs that produce no SEO data, and it would put a cosmetic artifact inside
the path that reports a run's exit status. Codex asked for an integration test proving a
dashboard failure cannot mask a failed loop. The better answer is to remove the possibility:
**don't touch the scheduling layer at all.**

Instead:
- On demand: `./.venv/Scripts/python.exe tools/seo_timeseries.py art seo`.
- The `/run-loop` skill gains one **guarded** step (Codex R2 #13 — Draft 2's version was
  unconditional and would have fired the SEO renderer after `_demo` or any future non-SEO
  loop): *"if and only if the loop just run is `seo` and the project has a `timeseries/`
  directory or `--out` default, rebuild the dashboard and report its path. **A non-zero exit
  here is reported as a note and never changes the run's reported outcome.**"*
- The page states its own freshness — every observation is dated and counted, and the daily
  series banners itself stale after 14 days — so an out-of-date dashboard is self-announcing
  rather than misleading. This is exactly why Principle 6 exists.

A scheduled rebuild can be added later in one line if Nate wants it (**Q3**); nothing here
depends on it. Phase B has **no** automated invocation either, by the same reasoning, and Part 4
says so where the operator will read it.

---

## Part 8 — Sequence

| Step | Work | Gate |
|---|---|---|
| A1 | `lib/timeseries.py` + checks 1–19 | all green |
| A2 | `seo_timeseries.py` writer, `series.json`, CSV + checks 20–27g | all green |
| A3 | `dashboard.html` renderer; palette validated both modes; page opened and eyeballed | visual pass |
| A4 | `.gitignore`, guarded `/run-loop` skill step, `CLAUDE.md` tools list, `PLAN-SEO-PROGRAM-INTEGRATION.md` cross-reference note | — |
| A5 | Backfill by **enumerating** the runs directory (no hard-coded count); commit **tools and docs only** (Q1) | — |
| B1 | `gsc_daily.py` + full-retention backfill + gap-proof window + provisional days + checks 28–36 | Q2 default = build |
| B2 | Per-priority-page daily pulls | after B1, only on request |

---

## Part 9 — Risks and non-goals

- **The dashboard will show very little movement, and that is the honest output.** Eight usable
  GSC observations over four weeks, 23 clicks, 20 brand. A page that made that look like a
  trend would be the failure. The design leans on stat tiles, explicit observation counts, and
  a real time axis rather than confident-looking trend lines.
- **`avg_position` is impression-weighted over a sampled, possibly-truncated row set.** It
  moves when Google shows the site for *more* low-position queries — not a ranking regression.
  Labelled; the priority-page and per-query views are where real movement shows.
- **Local rank shows no ranks, on purpose** (F6). Expect that to look like a missing feature;
  it is the correct output for uncollected data, and the panel says so in plain language.
- **No new secrets or network surface in Phase A.** Phase B adds one read-only call on an
  already-granted scope.
- **Nothing here runs on a schedule** (Part 7). Both the dashboard rebuild and Phase B's daily
  pull are on-demand. The page announces its own staleness; Phase B's window is gap-proof so
  staleness is never lossy. If that trade is wrong, Q3 flips it in one line.
- **Not built:** per-page CWV or indexation *time series* (cut — MMC reports both daily; a
  latest-only status strip remains), cross-project rollup, any write to a client repo, any
  proposal generated from this data, any conversion/lead metric (P4, needs definitions first),
  any hosted or shared copy of the page, any numeric local-rank chart (deferred behind P0.1).

---

## Open questions for Nate

**Q1 — Commit the generated artifacts to the public repo? Default: NO (hold), Phase B's
included.**
`runs/*/snapshot.json` — every client query, click and impression — is *already* committed to
`github.com/nateginn/looping_agency`, which is public. This plan adds no new category of data,
but it does make it far easier to read, and Codex is right that "no worse than the status quo"
is not consent.

**Default taken: the entire generated directory is ignored, by path, not by file list**
(Codex R4 #1 — Draft 4 named three files in this answer while Part 3.3 listed five, so an
implementer following this section alone could have published Phase B's client history):

```gitignore
projects/*/loops/*/timeseries/
```

That covers `dashboard.html`, `series.json`, `gsc_queries.csv`, `gsc_daily.jsonl`,
`gsc_daily.meta.json`, and anything added later — a path rule cannot fall out of sync with a
file list the way an enumeration can. The tools and this plan are committed; the artifacts live
locally at `projects/art/loops/seo/timeseries/` and rebuild in under a second from any checkout.
Deleting that one line reverses it. (Whether `runs/` itself should be public is a separate
question, out of scope here, and worth its own decision.)

**Q2 — Phase B: build the daily GSC pull with ~16-month backfill? Default: YES.**
It is what turns "nine overlapping 28-day windows" into a genuine week-by-week chart with a
real pre-loop baseline, at the cost of one free read-only API call and a new standalone tool
that cannot affect a run. Gated on Phase A passing its checks and on Part 5's checks 28–36
passing offline first.

**Q3 — Scheduled rebuild? Default: NO.** On-demand plus the `/run-loop` hook, with a
self-announcing freshness strip. Registering a scheduled refresh is a one-line change whenever
he wants it.
