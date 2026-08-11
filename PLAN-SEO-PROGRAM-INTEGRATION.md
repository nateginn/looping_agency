# Plan: integrating `SEO_PROGRAM.md` into the Loop Agency process

_Round 7 revision by Claude, 2026-08-11, after seven rounds of Codex adversarial review.
Argument transcript: `PLAN-REVIEW-LOG-SEO-PROGRAM.md`._

`SEO_PROGRAM.md` is the operator's reference compilation (three independent source sets,
merged 2026-08-10). It is **untracked by standing instruction** and stays that way — this
document is the tracked artifact that decides what, if anything, the Loop Agency adopts
from it.

**Scope, stated accurately** (Codex Round 5 #4 — Round 4's blanket "changes no approval
gate" was false): **no tier boundary moves.** Push, merge, deploy and rollback stay Tier 2
/ human-only; no new action type becomes auto-implementable; no R6 amendment is required
for anything here; Path B stays unbuilt. But this plan does deliberately **add** integrity
controls and **change** evaluation semantics — P3 adds a rules-file authorization check on
the apply path, and P0.5 changes what a terminal evaluation means and which events it
emits. Those are tightenings, not loosenings, and they are named as such wherever they
appear.

---

## The finding that reorders this entire plan

Round 0 assumed the loop's problem was *candidate selection*. Codex's Round 1 critique
forced a check of the actual numbers, and the numbers say something bigger. All of the
following is measured from the 2026-08-11 run
(`runs/2026-08-11T05-18-25-362Z-u5z1dd/snapshot.json`) plus a direct read of the connector
code — not inferred, and not from `SEO_PROGRAM.md`.

**1. The organic web-search channel this loop optimizes produced 23 clicks in 28 days.**

| | |
|---|---|
| GSC rows returned | 910 (`gsc.py:77` requests `rowLimit=1000` and never paginates — a separate defect) |
| Impressions | 10,308 |
| **Clicks** | **23** |
| Site-wide CTR | 0.22% |

**2. Twenty of the 23 clicks are brand-navigational** ("accelerated rehab therapy" ×18,
"accelerated rehab" ×2). The remaining **three non-brand clicks** are one each on
`dry needling near me`, `stellate ganglion block near me`, and
`best vestibular doctor near me` — all long-tail clinical queries, **none of which is
among the loop's 12 configured `targets`, and none of which maps to a `priority_pages`
entry.** The only non-brand demand converting at all is for services the loop is not
tracking.

**3. Zero clicks on the five highest-impression commercial queries, including three at
average position ~1.0.**

| Query | Avg position | Impressions | Clicks |
|---|---|---|---|
| auto injury chiropractor | 1.0 | 710 | **0** |
| sports chiropractic near me | 1.0 | 578 | **0** |
| sports chiropractor near me | 1.0 | 575 | **0** |
| chiropractor near me | 1.2 | 476 | **0** |
| chiropractor | 1.1 | 398 | **0** |

Position buckets 4–8 and 10–20 contain **zero clicks in total**. A CTR-by-position curve
derived from this site's own data is `0` almost everywhere — Codex Round 1 #1, confirmed
exactly.

**4. DataForSEO cannot currently find the configured target page for 10 of 12 city
targets — and the two "ranks" it does report are wrong.**
`physical therapy greeley → rank 34` is actually `mgmc.org`; `physical therapy denver →
rank 6` is actually `occ-ortho.com`. Both are competitors, matched by the defect below.

**Stated precisely** (Codex Round 2 #2): a null result means *the configured page was not
found in the top 100*, **not** that the domain has no organic presence. Another ART URL, a
canonical, a redirect target, or the homepage could be ranking and the connector would
never report it, because it only ever looks for one path. Establishing actual domain
presence is a P0.1 deliverable, not something these numbers already show.

**5. Core Web Vitals field data is `unknown` on every page** — CrUX has insufficient
real-user traffic to publish.

### What this means, and what it does not

**Supported by the evidence:** for `art`, organic web-search clicks are a rounding error.
The loop's objective function is measuring a channel that delivers under one click per day,
almost all of it people typing the clinic's name. Rewriting a title tag to lift CTR on a
query that gets 0 clicks at position 1 cannot produce a measurable result — which is
precisely what the two 2026-08-11 Codex reviewers concluded from the other direction when
they refused both proposals on insufficient evidence.

**A hypothesis, explicitly not yet supported** (Codex Round 2 #1): that the position-1 rows
are local-pack / Maps appearances rather than organic web rankings. GSC reports a position
for a result, not a classification of which SERP element produced it, and the current pull
carries no search-appearance dimension. The reading is *consistent* with DataForSEO finding
no top-100 organic result for the same class of term and with the query mix ("near me" in
three of the top five), but consistency is not evidence. **P0.2 and P0.7 are designed to
test it, against those exact five queries.** Until they run, it stays labelled a
hypothesis everywhere it appears.

If the hypothesis holds, the clinic's discovery is happening on Google Business Profile,
which this loop does not measure at all. `SEO_PROGRAM.md` Appendix A asserts exactly that
shape for local businesses — *"For a local business this outranks most of Phases 4–6 in
ROI"*, *"Customers aren't Googling your website — they search 'plumber near me'"*. Round 0
agreed in principle and then sequenced GBP work fifth. **That was Round 0's biggest error.**
The document's own priority ordering was right, and the site's data points the same way —
though P1 below is defensible on Appendix A's reasoning even if the hypothesis fails.

### P0-b RESULT — run live 2026-08-11 16:35Z, 10 paid DataForSEO tasks

**The hypothesis is substantially confirmed, with one important refinement.**

All ten SERPs — five queries × Greeley and Denver — **open with a three-entry local pack at
rank 1, and ART is in none of them.** Not one.

| Query | Greeley: ART organic | Denver: ART organic | Local pack | ART in pack |
|---|---|---|---|---|
| auto injury chiropractor | **#2** | not in top 100 | rank 1 | no |
| sports chiropractic near me | **#8** | not in top 100 | rank 1 | no |
| sports chiropractor near me | **#12** | not in top 100 | rank 1 | no |
| chiropractor near me | not in top 100 | not in top 100 | rank 1 | no |
| chiropractor | not in top 100 | not in top 100 | rank 1 | no |

Refinement that matters: it is **not** simply that GSC's "position 1" was a local-pack
artifact. ART genuinely ranks **#2 organically in Greeley for "auto injury chiropractor"**
and still takes zero clicks — because a three-entry local pack sits above it and absorbs
the intent. Ranking #2 under a pack you are absent from is worth approximately nothing.

- **Greeley is competitive organically** (#2, #8, #12 on three of five) and losing to the
  pack, not to the organic competition.
- **Denver is not competitive at all** — absent from the top 100 on all five.
- **AI Overviews are not a factor here.** Zero of ten SERPs contained one. A clean negative
  result: whatever is suppressing clicks, it is not AI Overviews on these queries today.

**Consequence for the plan:** P1 (GBP / local entity) is confirmed as the highest-value
work, and its justification is now measured rather than inferred. The GBP checklist half
should start immediately.

### Four defects this investigation found that were not previously recorded

- **D1 (High) — `dataforseo.py` matches SERP results by URL path substring, with no domain
  check.** `tools/dataforseo.py:165` and `:261`:
  `target["page"] in (item.get("url") or "")`. So `/physical-therapy/` matches
  `https://www.mgmc.org/care/rehab-and-therapy/physical-therapy/`. This produces **silently
  false rank data attributed to competitors' URLs**, and it feeds
  `attention_thresholds[organic_rank_position]`, so the loop can raise an attention finding
  on a competitor's rank movement. A paid connector returning wrong data that looks right.
  `HANDOFF.md`'s 2026-07-24 item A2 suspected a matching bug; this is it, and it is worse
  than "no match".
- **D2 (Medium, free to fix) — the loop already pays for data that would answer the
  zero-click question, and discards it.** Both DataForSEO call sites filter to
  `item.get("type") == "organic"` and drop everything else. The same response already
  carries the SERP's composition — `local_pack`, `ai_overview`, `people_also_ask`, `map`.
  Capturing it costs **zero additional API spend**.
- **D4 (Critical, found during P0-b on 2026-08-11) — `pull_local_rank` has only ever
  checked ONE keyword per city. The other five were never checked, and were silently
  reported as "not found".**
  `dataforseo.py:242-248` builds one task per target and POSTs them **as a single array**.
  DataForSEO's `live/advanced` endpoint accepts **one task per request** — every additional
  task comes back `status_code: 40000, "You can set only one task at a time"`, with
  `result: None` and `cost: 0`. Verified directly: a two-task POST returned 113 items for
  the first keyword and that error for the second.
  Downstream, `result = task.get("result") or []` turns that error into an empty item list,
  which yields `match = None`, which is written as `organic_rank_position: null` —
  indistinguishable from a genuine "not ranking".
  This explains the data exactly: across 14 runs the only keyword ever producing a result
  in Greeley is `physical therapy greeley` and in Denver `physical therapy denver` — the
  **first entry for each city** in `spec.md`'s `targets`. It also means the 2026-07-26
  "36 checks → 12 checks" cost optimization was moot: only 2 tasks per run were ever
  actually billed.
  `pull_metrics`'s SERP path (`dataforseo.py:142-148`) batches identically and has the same
  defect; it is not enabled for `art`.
  **Combined with D1, essentially the entire `local_rank` history is invalid** — 10 of 12
  rows were never measured, and the 2 that were are frequently attributed to a competitor.
  Fixing this is now the top item in P0.1, ahead of the domain-match fix, because no amount
  of correct matching helps a request that was never sent.
- **D3 (High) — "verified winner" is currently vacuous on this site.** `run_loop.py:518`
  computes `sample_ok = metrics["sample_size"] >= p["min_sample_size"]`, where `sample_size`
  is the run's **total** GSC sample (10,308), not the target row's impressions. With
  `min_sample_size: 100` that test passes unconditionally. The evaluation then sets status
  `verified` whenever position has not drifted more than 5 places
  (`run_loop.py:557-562`). So on a page with zero clicks, an applied change is recorded as
  a *"verified winner"* on the strength of position not having gotten worse. `PLAN.md`
  Phase 1 closed the false-win route through missing/ambiguous/null rows; **this is a
  different door into the same room, and it is open today on a live, auto-implementing
  loop.**

---

## Design principles

1. **No boundary moves.** Tier 2 (push/merge/deploy/rollback) stays human-only. The three
   auto-implementation gates stay as they are. Path B stays unbuilt.
2. **New capability arrives read-only first.** Measurement, then diagnostics, then
   drafting, then anything that could reach a commit.
3. **The auto-implementable action set does not grow.** `title-tag-rewrite` and
   `meta-description-rewrite` remain the only two actions that can reach a local commit.
4. **Nothing on `SEO_PROGRAM.md` Part 11's human-only list gets automated.**
5. **Every backticked number in `SEO_PROGRAM.md` is a presenter's claim, not a fact.**
6. **Where a program method and this site's measured data disagree, the data wins and the
   disagreement gets written down.**
7. **Name the epistemic status of every claim.** Measured, inferred, or hypothesis. Round 1
   of this plan blurred the three and Codex was right to attack it.

---

## Step 0 — the operating-state decision only Nate can make

**Recommendation: put `art/seo` back into manual-approval mode before any of P0 is
implemented, and keep it there until the whole of P0, P3, P5, P6, P8's structural half and
P9.2's page→primary-query map are done — then restore it on an explicit, recorded go/no-go.
Not after P3 alone.**

The reasoning, which is Codex's (Round 3 #2) and which I agree with:

- `art` is currently live with all three auto-implementation gates open on
  `title-tag-rewrite` / `meta-description-rewrite`.
- D3 means the loop's success verdict is currently vacuous — it would mark essentially any
  change to this site a "verified winner".
- P3 means there is no approved statement of what may be claimed about a **medical**
  practice, and P3's operational prerequisite is *not enforced by any code*: writing
  `PROJECT_RULES.md` changes nothing mechanically until the hash check exists.
- P0.5 changes evaluation semantics, and P0.3/P5 change candidate ordering. Changing the
  scoring and the scoring's verdict underneath a loop that is authorized to act on them
  unsupervised is the wrong order of operations.

**The mechanism — corrected, because Round 4 named the wrong switches** (Codex Round 5 #1,
verified in `apply.py:200-244`). The three gates are *not* equivalent, and only one of them
actually closes both doors:

| Switch | What it actually does |
|---|---|
| **`approval_mode: propose-only`** | **The real freeze.** `apply.py:217` calls `_load_approval_mode(loop_dir)`, reading the spec **fresh at apply time**, and refuses every Tier-1 apply regardless of which path reached it. |
| `auto_implementation_enabled: false` | Re-checked only inside `if auto_path:` (`apply.py:233`), i.e. only for status `approved-for-implementation`. A proposal a human moves to `approved` via `/review-pending` **still applies**. |
| `manual_approval_only: true` on the two actions | `apply.py:223` reads `proposal["manual_approval_only"]` — the value **cached on the proposal when it was created** (`run_loop.py:695`), not the current spec. Editing the spec therefore does **not** lock down proposals that already exist. |

So: **set `approval_mode: propose-only`.** That single line is the fail-closed switch.
Setting `auto_implementation_enabled: false` alongside it is worthwhile defense in depth,
but on its own it would leave the human-approval route to `apply.py` wide open — which is
exactly the route a well-meaning morning session would take.

R14's claim that flipping "any gate" genuinely disables the path is true **for the
unattended Codex path only**; it does not generalize to the manual-approval path, and this
plan should not repeat it as though it did.

**How urgent this actually is — stated precisely, because the honest answer is "not very":**

| Job | Next run | Can it create a proposal? |
|---|---|---|
| `LoopAgency-Art-SEO` (full) | **Mon 2026-08-17 06:00** | Yes — this is the only one that can |
| `LoopAgency-Art-SEO-DailyRank` | daily 07:00 | No — `technical-only` mode skips `_pick_new_actions` |
| `LoopAgency-Art-SEO-Technical` | Thu 2026-08-13 06:00 | No — same |

And auto-implementation cannot fire on a schedule at all: `/codex-seo-review` is invoked on
demand only, because no durable Claude-Code-capable scheduler exists in this environment
that can reach the local `codex login` session (R14, decided 2026-08-03). **So nothing can
auto-implement unless Nate types a command, and no *scheduled* proposal-generating run
occurs before Monday** — `run_loop.py art seo` invoked by hand would still create proposals
at any time (Codex Round 5 #6). There is a six-day runway on the automatic path. This is a
considered recommendation, not an alarm — but it is the first thing to decide, because
everything in P0 is safer with it done.

**I have not made this change.** It alters the live operating state of Nate's own site's
loop, which is his call, and nothing forced it overnight.

## P0 — Correctness and diagnosis. Before any new capability.

**Staged deliberately** (Codex Round 3 #1): P0 is *not* free and *not* zero-surface, and
Round 2 was wrong to imply it. P0.2's diagnostic queries are **new paid DataForSEO SERP
tasks**; P0.3's pagination changes the returned dataset; P0.5 changes evaluation semantics.
Each stage completes before the next begins:

- **P0-a — offline.** Code and tests: P0.1, P0.5's design and migration, P0.6's
  documentation sync. Verified by `--verify` self-tests and
  `tools/tests/phase1_exit_criteria.py`.
  **There is no deployment isolation in this workspace** (Codex Round 4 #3, and Round 3's
  wording was wrong to imply otherwise): the scheduled tasks execute
  `tools/scheduled/task.cmd` against **this working tree**, so the moment a corrected
  `dataforseo.py` or `_evaluate_prior_experiments` is saved, the next scheduled run uses
  it. "Offline" here means *no new live API surface and no new credential* — it does **not**
  mean the scheduled jobs are unaffected. Therefore: do P0-a under the Step 0 freeze, and
  state in the commit which scheduled outputs change as a result (P0.1 changes
  `local_rank` row values; P0.5 changes evaluation outcomes).
- **P0-b — one controlled live diagnostic run**, invoked by hand, outside the weekly pass:
  P0.2 and P0.7. Needs Nate's explicit cost approval for the extra SERP tasks first.
  **This needs a real one-shot mechanism, not a spec edit** (Codex Round 4 #2): `run_loop.py`
  dispatches from `spec.inputs`, and `gsc-indexation` derives its page list from
  `_absolute_pages(spec)`, so simply adding `diagnostic_queries`/`diagnostic_pages` to
  `spec.md` would hand the daily `dataforseo-local-rank` job 10 extra paid tasks every day,
  forever. Define an explicit non-scheduled diagnostic command with its own inputs and its
  own output path, no proposal or evaluation path, and a test proving a scheduled job cannot
  invoke it.
- **P0-c — scheduled activation.** Only after P0-a and P0-b have been read by a human. P0.3
  lands here, and lands **reporting-only first** (Codex Round 3 #10): pagination can
  materially change candidate ordering, and the row-set change must not go live in the same
  run as a selector-behaviour change.

### P0.1 — Fix the SERP connector: one task per request (D4) first, then domain matching (D1)

**D4 comes first.** `pull_local_rank` (and `pull_metrics`'s SERP path) must issue **one
POST per task**. Batching silently drops every task after the first, at zero cost, in a way
that is indistinguishable downstream from "not ranking". Add a `--verify` case that asserts
a multi-target pull issues one request per target, and treat any non-`20000` task status as
a **connector error**, never as an empty result — the current `task.get("result") or []`
pattern is what converts an API rejection into a false negative.

Expect the real cost of the daily rank check to rise from 2 billed tasks to 12 once this is
fixed. At DataForSEO's observed `$0.02`/task that is ~`$0.24`/run rather than ~`$0.04` —
still trivial, but it should be a known change rather than a surprise on the invoice.

A shared URL-normalization/matching helper with an **explicit `domain` parameter**, used by
both call sites. This is more than a one-line fix (Codex Round 2 #4, verified): neither
`pull_metrics()` nor `pull_local_rank()` currently receives the domain at all —
`run_loop.py:348` passes `targets`, `locations`, `language_code`, `device`, but not
`spec.get("domain")`, even though the adjacent backlinks branch (`run_loop.py:370`) does.
The domain must be propagated through `_dispatch_connector` before any host check is
possible.

The helper must handle: absolute vs relative target paths, `www.` variants, ports, query
strings and fragments, trailing-slash equivalence, and path-prefix collisions (`/massage/`
must not match `/massage-therapy-guide/`). Redirect and canonical aliases cannot be
resolved from the SERP response alone — record the matched URL and let P0.7/P8 reconcile it.

Report **four separate fields per target, never collapsed into one position** (Codex Round
2 #2, Round 3 #11):
- `target_page_rank` — the configured page's rank, as today but correct.
- `domain_presence_top100` — scan **every** organic result for the domain, recording each
  matching URL and rank. This is what actually answers "does this site rank for this term".
- `matched_url` — the actual URL that matched, kept distinct from the configured target.
  A canonicalized or redirected ART URL may legitimately be the ranking result, and
  treating exact target-page matching as *the* rank would turn that into a false negative.
  The SERP response cannot resolve aliases; P0.7/P8 reconcile them.
- The top-ranking competitor URL for the keyword regardless of match — genuinely useful
  competitive data the loop already pays to receive and currently discards.

**Backfill — Round 3's claim here was wrong and is corrected** (Codex Round 4 #4, verified
against every snapshot in the run history). It is *not* true that every historical
`organic_rank_position` for these two queries is false. The defect makes the rows
**untrusted, not uniformly wrong**, and the actual history is more interesting:

| Run | `physical therapy greeley` result URL | Rank |
|---|---|---|
| 2026-08-04 (×2), 08-07, 08-08 | **`acceleratedrehabtherapy.com/physical-therapy/`** | 37, 33, 34, 50 |
| 2026-07-26, 08-03, 08-04, 08-05, 08-06, 08-09, 08-11 | `mgmc.org` (competitor) | 23–34 |

So ART's own page **does** rank for `physical therapy greeley`, around **position 33–50**,
and the connector flips between reporting ART's URL and a competitor's depending on which
matching path appears first in the SERP items. For `physical therapy denver`, **every
non-null result was a competitor** (`occ-ortho.com`, earlier `denverhealth.org`) — ART has
no host-validated result there in any run; the remaining rows are null.

Correct remedy: snapshots are immutable, so do not rewrite them. Instead **quarantine only
the rows whose `result_url` host is not ART's domain**, preserve the host-validated rows as
usable history, and label the rest unverifiable. Record the defect, its date range, and this
distinction in `RISK-REGISTER.md`.

**Verification:** `dataforseo.py --verify` gains cases for a competitor URL sharing the
target path (must not match), a `www.` host variant (must match), a path-prefix collision
(must not match), and a domain-present-but-different-URL case (must report
`domain_presence_top100` while `target_page_rank` stays null).

### P0.2 — Capture SERP composition (D2) — free on existing calls, 10 paid diagnostic tasks

Two distinct pieces, and Round 3 blurred them under a "zero marginal cost" heading that its
own body contradicted (Codex Round 4 #5): **capturing composition on the 12 SERP calls the
loop already makes is genuinely free**; **the five diagnostic queries are 10 new paid
tasks**.

Record per checked keyword: the ordered list of `item_types` present, whether a
`local_pack` appears and at what rank, whether an `ai_overview` appears, and — separately
and only where the response actually supports it — `site_in_local_pack` and
`site_in_ai_overview`.

**Name the persistence path, or the data is discarded again** (Codex Round 4 #6). Capturing
the fields in `dataforseo.py` is not sufficient: `_merge_metrics()` (`run_loop.py:151-170`)
carries only `serp_position` across from the DataForSEO result onto the merged keyword rows,
so anything else is dropped at the merge. The change must name each hop —
`dataforseo.py` → `_merge_metrics()` → snapshot section → `report.md` — with a fixture that
asserts the composition fields survive the merge, not just that the connector produced them.

**Named precisely** (Codex Round 2 #3): this is **SERP composition**, not AI visibility.
That an AI Overview appeared says nothing about whether ART was cited in it, which URL was
cited, or whether the citation was visible. Same for the local pack. Real AI
citation/mention measurement — `SEO_PROGRAM.md` Part 2's "citation vs mention" KPIs and
Part 6.5's Bing AI Performance report — **remains an unresolved, unscoped question**, and
this plan should not be read as delivering it. What P0.2 does deliver is the diagnostic
that tells us what kind of SERP those five queries actually produce.

**Run it against the right queries, and cost it before running it** (Codex Round 2 #1,
Round 3 #1/#3): the loop's 12 configured `targets` are city-qualified terms; the zero-click
queries are different strings (`auto injury chiropractor`, `sports chiropractic near me`,
`chiropractor near me`, `sports chiropractor near me`, `chiropractor`). Add a
`diagnostic_queries` set — as **`{query, locations, device}` triples**, not bare strings.
`pull_local_rank()`'s current contract is target-page-oriented; running five bare queries
would either fabricate a page target or resolve to one arbitrary location, and "matching
geography" would be undefined.

**These do not go in `spec.md`** (Codex Round 5 #2 — Round 4 said both things and
contradicted itself). Any field in the spec is a field a recurring connector may read, and
the whole point of P0-b is that this runs once. Put the diagnostic inputs in a **separate
one-shot command manifest** owned by the diagnostic command. That manifest needs a defined
schema and command interface, and three assertions proved by test: it cannot write
proposals, it cannot write evaluation state, and no recurring connector reads it.

Concretely: **5 queries × 2 real locations (Greeley, Denver) = 10 new SERP tasks**, run
**once, by hand, outside the weekly pass** (P0-b), with Nate's explicit cost approval
first. These are new paid tasks on a metered account — the existing 12 target tasks are
unaffected, and nothing here becomes recurring without a separate decision.

### P0.3 — Separate brand from non-brand; stop silently truncating GSC

A `brand_terms` list in `spec.md`, used **only for reporting and scoring**, never as a
filter. Report clicks, impressions and CTR split brand / non-brand. Without the split,
"23 clicks" reads as small-but-real; with it, "3 non-brand clicks, none on a tracked
service" reads as what it is.

Paginate the GSC pull via `startRow`. **Stated honestly** (Codex Round 2 #7): the Search
Analytics API does not guarantee that pagination retrieves every row — Google documents
internal grouping and truncation. So paginate where possible and record
`returned_row_count`; never present it as a true total unless it comes from an aggregate or
bulk-export source.

**Ship pagination reporting-only first** (Codex Round 3 #10). More rows means a different
candidate set, so pagination is a silent selector change if deployed carelessly. Land it in
P0-c with the selector still reading the old row set, compare the two row sets in one
report, and only then let the selector consume the full set — never in the same live run as
a P5 behaviour change.

### P0.4 — Dispose of the two held proposals; fix the skill contradiction

Round 0 claimed the selector fix would "unblock" the two homepage proposals stuck at
`review-revision-needed`. **That was wrong** (Codex Round 1 #11). They are non-terminal, so
cooldown blocks new homepage proposals until a human resolves them via `/review-pending`.
On the evidence above the likely disposition is `reject`, with the reason recorded
(insufficient click signal for any measurable outcome) — but that is Nate's call, not this
plan's.

Fix `HANDOFF.md` known-defect #4: `.claude/skills/codex-seo-review/SKILL.md` still says it
"never runs against `art`" while its own Prerequisites section says the opposite —
self-contradictory since the 2026-08-03 activation.

### P0.5 — Make "verified" mean something (D3)

Not a small change, and Codex Round 2 #5/#6 are right that it needs a full design before
implementation:

- **Baseline provenance.** Deciding "zero clicks before and after" requires baseline
  clicks; proposals currently persist `baseline_position` only (`run_loop.py:692`). Persist
  `baseline_clicks` and `baseline_impressions` at creation, or bind evaluation to the
  creation snapshot. Without this the outcome cannot be computed at all.
- **Sample gate.** Compare against the **target row's own impressions**, not the run total.
  This requires matching the row *before* the sample test, which reorders
  `_evaluate_prior_experiments` and changes what the evidence packet reports. It also
  introduces a real hazard: a low-impression proposal could sit in `applied` forever.
  Mitigate with an explicit `not-evaluable: insufficient-target-impressions` outcome plus a
  bounded consecutive-not-evaluable escape, reusing the existing `not_evaluable_streak`
  counter (`run_loop.py:503`).
- **Status vs outcome.** `TERMINAL_PROPOSAL_STATUSES` is a fixed set
  (`run_loop.py:63` — `rejected`, `review-rejected`, `verified`, `breached`) consumed by
  report rendering, event emission and reconciliation. **Keep terminal status `verified`;
  add `evaluation_outcome: verified-no-signal`** meaning "did not harm position, produced
  no measurable click effect". Introducing a new terminal *status* would require touching
  every consumer of that set for no gain.
- **The escape must be specified, not gestured at** (Codex Round 3 #6). A proposal that
  stays `applied` stays non-terminal and keeps holding its page's cooldown — potentially
  forever, which is the hazard the escape exists to close. Decide explicitly: after N
  consecutive `not-evaluable: insufficient-target-impressions` results, the proposal
  becomes **terminal** with `status: verified` + `evaluation_outcome: never-evaluable`,
  releasing cooldown and emitting an event, **or** it stays `applied` and the indefinite
  cooldown hold is an accepted, visible, reported behaviour. It cannot be left to whichever
  the implementation happens to do.
  **And the event path must be built with it** (Codex Round 4 #7): `run_loop.py` emits
  verification events only for `verified`/`breached`, while `event_log.py --reconcile` maps
  other outcomes onto `proposal_verified`. Left as-is, a `never-evaluable` escape would
  release cooldown while reporting itself to the event log — and therefore to MMC's daily
  briefing — as a verification. Specify the event type and change `run_loop.py`,
  `event_log.py`, reconciliation, report rendering, the MMC field set, and the tests in one
  move.
- **Migration for existing proposals** (Codex Round 3 #5). Changing the sample gate changes
  the meaning of every proposal already written, and older proposals carry no
  `baseline_clicks`/`baseline_impressions`. `art` has no `applied` proposals today, but the
  plan must not depend on that staying true: before deployment, enumerate every
  `applied`/`implemented` proposal, backfill provenance from its creation snapshot where
  possible, and otherwise mark it `not-evaluable` with a recorded reason **while preserving
  its cooldown**.

**This deliberately changes evaluation semantics** — it does not merely add something, and
it should not be described as harmless. Existing outcomes will be computed differently, and
some proposals that would previously have been marked `verified` will not be. That is the
intent: it stops the loop accumulating a record of "verified winners" that mean nothing,
which on current evidence is what it would otherwise do for every change it ever makes to
this site. The migration rule below is what keeps the change from corrupting history, and
it must distinguish `implemented` from `applied` proposals — they sit at different points
in the evaluation path and cannot be migrated identically.

### P0.6 — Documentation sync (Codex Round 2 #15, verified)

Two operator-facing files currently state the opposite of the live configuration, and an
operator reading either would form a materially wrong belief about what the loop may do
unsupervised:

- **`projects/art/project.md:53`** — says the `seo` loop is **`propose-only`** and will move
  to `tier1-enabled` only "after Nate reviews the first two reports". `spec.md` has been
  `approval_mode: tier1-enabled` + `auto_implementation_enabled: true` since 2026-08-03. Its
  "Guardrails / caps" section also still describes title/meta rewrites as "human-reviewed
  before approval regardless".
- **`projects/art/loops/seo/instructions.md:19`** (Codex Round 4 #10, verified) — says
  *"Every `allowed_action` in `spec.md` is `manual_approval_only: true`"*. Two of the three
  are `false`.

Sync both, plus `.claude/skills/codex-seo-review/SKILL.md` (P0.4), and re-check `CLAUDE.md`,
`HANDOFF.md` and `RISK-REGISTER.md` for the same drift. Note `templates/loops/seo/instructions.md:19`
also still describes rollback as "revert PR", which is Path A language that Path B retired —
mark it historical rather than leaving it as guidance.

### P0.7 — Verify indexability before interpreting the zero-click pattern (Codex Round 2 #16)

Before concluding anything about local packs, confirm the pages behind those five
high-impression query rows are indexed, canonical, and eligible for the appearance in
question. A page that is canonicalized elsewhere, or whose title/snippet is unrelated to
the query, is a simpler explanation than a local pack and must be excluded first.

**And the pages in question are not currently inspected at all** (Codex Round 3 #4, verified
and sharper than stated there): **all five** of those query rows resolve to the **homepage**,
`https://acceleratedrehabtherapy.com/` — and the homepage is **not in `priority_pages`**,
which is the only list `gsc-indexation` inspects. So the existing connector has never looked
at the page this whole diagnosis is about.

Therefore: derive an explicit `diagnostic_pages` set from the five GSC rows, include the
homepage, and split the work by what each tool can actually answer — **URL Inspection for
indexation and canonical state**, and a **live HTTP fetch for the server HTML's `<title>`
and meta description**, which URL Inspection does not return. That fetch is *not* Google's
rendered SERP snippet (Codex Round 4 #9) — Google may rewrite either; snippet measurement
stays unresolved. Note also that `http://…/massage/` carries
1,739 impressions against `https://…/massage/`'s 157 — a protocol/canonical split worth
understanding in the same pass.

---

## P1 — Google Business Profile and the local entity (`SEO_PROGRAM.md` Appendix A)

Promoted from Round 0's fifth position to first-after-correctness. Appendix A is the
deepest material in the source document and the only part aimed at exactly this business
shape.

**The checklist half costs nothing and is not blocked on anything** —
`templates/loops/seo/gbp-checklist.md`, human-run: primary/secondary categories, the hidden
300-character service-description menu, products-as-services, genuine service areas within
a realistic radius, weekly posts, 15–20 real work photos, messaging response speed, the CID
footer link, and the monthly competitor-profile audit.

**The measurement half** (build after P0 reports what those SERPs contain). Scope these as
**three separate connector capabilities with separate access checks and independent
degradation** (Codex Round 3 #15) — they are distinct data surfaces and should not be
assumed to arrive through one API or one permission:
- **Performance** — profile views, search vs maps split, calls, direction requests, website
  clicks.
- **Reviews** — count, average rating, and **recency** drift as `attention_thresholds`
  metrics. (Appendix A's "fresh reviews are worth ~3× older ones" is a presenter claim;
  recency is measurable regardless and is a genuine leading indicator.)
- **NAP consistency** between the site footer (already captured in
  `locations-detected.json`), `spec.md`'s `locations`, and each profile.

**Access cost, corrected from Round 0** (Codex Round 1 #17): the Business Profile APIs use
**OAuth 2.0 with a user-consented refresh token**, not the service-account arrangement
`art-gsc-readonly` uses, and access may require a separate enablement request. A genuine
prerequisite with its own timeline, not a reuse of existing credentials.

**Two profiles, not three.** Greeley and Denver are real locations. `spec.md`'s third
entry, **UNC Campus, is footer-only** and must never be modelled as a third GBP profile.

**Never automated, at any point:** review solicitation (Guardrail 4 — gating can wipe a
profile), business-name changes (Guardrail 5), service-area claims (Guardrail 9).

## P2 — Local authority and citations (Part 5.4/5.5, Appendix A)

A two-location clinic's authority work is local: chambers of commerce, sponsorships, BBB,
Nextdoor, citation consistency, unlinked-mention reclamation, monthly competitor-profile
audit. `SEO_PROGRAM.md` Conflict 3 adjudicates the three sources' disagreement into a rule
worth adopting verbatim: **free local citations universally; paid links only as a last
resort, only after competitor analysis shows links are demonstrably the gap, and only with
a vendor who survives an audit of their reference sites.**

**Round 1 claimed 81 referring domains means links are not this site's binding constraint.
That claim is withdrawn** (Codex Round 2 #11): a raw count says nothing about quality,
relevance, local authority, or how it compares with whoever actually ranks. **No conclusion
yet** — a competitor backlink comparison is the prerequisite for deprioritizing authority
work, and the loop has never run one.

Deliverable: a human checklist plus an authority backlog artifact. **No automated link
acquisition, ever.**

## P3 — Business brief and `PROJECT_RULES` — a live risk, not a future one

`art` is a **medical** business — physical therapy, chiropractic, acupuncture, dry
needling. The loop today can auto-implement a rewritten `<title>` and `<meta description>`
on `/physical-therapy/`, `/chiropractor/` and `/acupuncture/` after two Codex rounds agree,
with **no approved statement anywhere in the repo** of which services are actually offered,
which claims the practice will stand behind, what may be said about outcomes, or which
credentials may be cited. `project.md`'s own "Guardrails / caps" section says it:
*"Brand-voice/content constraints: none specified yet."*

Program 0.1 makes the business brief the source of truth for everything downstream, and P1
names the prohibited inventions exactly: testimonials, experience, credentials, locations,
prices, statistics, guarantees. For a healthcare practice, an overreaching claim in a title
tag is a regulatory and trust problem, not a ranking problem.

Deliverable: `projects/art/BUSINESS_BRIEF.md` and `projects/art/PROJECT_RULES.md`,
human-authored with Nate.

**Enforcement, stated honestly** (Codex Round 2 #12): writing those files changes nothing
mechanically. `apply.py` does not read them, `review_protocol.py` does not hash them, and
the Codex skill prompt does not require them — the three existing gates would still permit
auto-implementation if the files were absent, empty, or silently edited. So P3 has two
parts, and only the second is a control:
1. **An operational prerequisite**, recorded in `RISK-REGISTER.md`: the artifacts exist and
   are approved before further auto-implementation of medical service-page copy.
2. **The durable version**: a rules-file presence-and-content-hash check added to the
   review/apply path, so a missing or changed rules file invalidates a prior review the
   same way `spec_content_hash` already invalidates one on a spec change. **Specified, not
   gestured at** (Codex Round 3 #12): a deterministic sha256 over the file's UTF-8 bytes
   with a defined newline canonicalization; persisted in the evidence packet, on the
   proposal, and in the event log (which means adding it to `event_log.py`'s
   `ALLOWED_EXTRA_FIELDS`, or it is silently dropped); and `apply.py` refuses
   auto-implementation when the current hash differs from the adjudicated one. Decide
   explicitly whether a rules change invalidates **only auto-implementation** (recommended
   — it matches how `spec_content_hash` is scoped) or all review.

Cheapest item in the plan by engineering cost, and the only one addressing a risk that is
live right now.

## P4 — Business-outcome measurement (Part 2's two dashboards)

The stated project goal is "more organic leads and phone calls". The loop measures position
and clicks. With three non-brand clicks a month — **none of them on a tracked service** —
position is not a proxy for anything.

**Definition before collection** (Codex Round 2 #13): calls and form fills are not
themselves organic leads, qualified leads, or booked patients. A small
`projects/art/BUSINESS_DASHBOARD.md` must define source, period, attribution method,
qualified-lead criteria, and booking outcome *before* any of these numbers is used to rank
SEO actions. Otherwise P4 replaces one bad proxy with a more confident-sounding one.

Minimum viable version: a monthly, human-entered figure for calls and form submissions,
plus GBP's call and direction-request counts once P1 lands.

**Sequencing correction** (Codex Round 2 #14): P4's *definition* must land **before** P5,
because P4 is what decides whether "priority" means search visibility, local discovery, or
booked patients — and P5 ranks candidates by exactly that. Only P4's connector work may be
deferred. Until it exists, `priority_pages` weighting is a **human assumption about
business value and must be labelled as one**, not presented as a measured input.

## P5 — Selector: rescoped, demoted, honest

Round 0 proposed replacing click-ranked selection with
`expected_click_gain = impressions × (expected_ctr − actual_ctr)` from a site-derived
CTR-by-position curve. **The data shows that curve is zero almost everywhere, so the score
degenerates to zero for nearly every candidate and stops discriminating between them** —
not undefined, just non-actionable, which for a ranking function is the same practical
outcome. Codex Round 1 #1 and #2 upheld in full.

**P5 is diagnostic-only until its inputs exist** (Codex Round 3 #7, corrected in Round 7).
Action selection needs four things that come from four different places: the page's
structure and its current title/meta from **P8**, SERP composition from **P0.2**,
conversion context from **P4**, and an approved primary query per page from **P9.2's map**.
Until those exist, P5 may score, diagnose and emit findings, but it **must not create
rewrite proposals**.

What survives:

- **Ranking is a diagnostic aid, not a business-value estimate.** `impressions × CTR gap`
  estimates observed-average clicks, not the uplift from a rewrite. It must never be called
  expected business value while no conversion data exists (P4).
- **Three distinct signal states, not one fallback** (Codex Round 2 #10). "Insufficient
  signal" cannot mean only "too few impressions" — 700 impressions with zero clicks *is*
  mathematically an estimate, and a useless one. Distinguish: `no-curve-estimate` (the
  bucket lacks impressions to estimate at all), `zero-observed-ctr` (enough impressions,
  zero clicks — an estimate that cannot support an uplift claim), and
  `no-actionable-uplift-signal`. **Never fall back to impression-ordered ranking for a
  rewrite proposal** — impression ordering may produce a *finding*, never a proposal.
  On today's `art` data the honest output is *"this site has insufficient click signal for
  CTR-based selection"*, said out loud in `report.md`.
- **Separate scoring, diagnosis, and action selection into three steps** (Codex Round 1
  #5). `_pick_new_actions` receives only GSC rows and a cooldown set — no current
  title/meta, no rendered HTML, no SERP composition, no conversion data — so it cannot
  decide whether a CTR problem is a title problem or a meta problem. Score, then diagnose,
  then propose *only* where an action can plausibly address the diagnosis; otherwise
  persist a finding.
- **Position 11–20 with impressions and no clicks is a finding, not a proposal.** A title
  rewrite cannot fix a page-two content/authority problem.
- **Two separate floors, in two separate fields** (Codex Round 2 #9 — the distinction I
  defended in Round 1 is accepted, the implementation is not). `SEO_PROGRAM.md` Conflict 1
  answers *does this term deserve a page* (where a 50-search high-intent local term
  absolutely can) → `target_opportunity_floor`. Experiment measurability answers *can a
  change to this be evaluated* → `min_target_impressions_for_evaluation`, which gates
  auto-implementation and evaluation only. Conflating them into today's `min_sample_size`
  is what produces both the vacuous "verified" (D3) and the temptation to discard
  low-volume opportunities. **Low-volume, high-intent terms route to human/content work;
  they are not rejected.** Neither floor may be a made-up default: the evaluation floor
  needs an explicit minimum-detectable-effect assumption written down, and an impression
  count alone does not establish one.
  **Migrate the old field rather than leaving it alongside the new ones** (Codex Round 4
  #8): `spec.md` still carries `min_sample_size: 100/150` per action, and
  `_evaluate_prior_experiments` still reads it. Introducing two new names without removing
  or redefining that one leaves three floors, one of which silently keeps the vacuous
  behaviour D3 describes. Decide explicitly: remove `min_sample_size`, or redefine it as
  `min_target_impressions_for_evaluation` with a schema change in `spec_validate.py`. The
  evaluation policy must be **fail-closed** — a spec with no authored evaluation
  configuration refuses to evaluate rather than falling back to 100.
- **Fix URL normalization first** (Codex Round 1 #4, verified): `priority_pages` holds
  absolute `https://` URLs while GSC returns both `http://` and `https://` variants —
  `http://…/massage/` carries 1,739 impressions against `https://…/massage/`'s 157, so they
  are not comparable strings today. Normalize to **path** on both sides. Note also that the
  homepage — 4,099 impressions and every brand click — **is not in `priority_pages`**, a
  gap needing Nate's decision either way.
- **Replace the arbitrary `2.0` multiplier** with explicit priority tiers or a
  human-approved allowlist. An unexplained magic number is what this project's own review
  discipline exists to reject.
- **Cooldown identity, and the reservation gap it exposes** (Codex Round 3 #9).
  `_cooldown_key_from_target` keys on page only (`run_loop.py:112`), so one proposal blocks
  every other query on that page. If scoring moves to `(query, page)`, cooldown must move
  with it — but that alone is unsafe: `_pick_new_actions` does **not reserve** a target it
  has just selected within the same run, so a query-plus-page cooldown would let one pass
  emit several proposals for the same page, including a competing title *and* meta rewrite.
  That directly widens the auto-implementation blast radius. So: reserve pages during
  selection, and **cap in-flight rewrites at one per page** until the previous experiment
  is terminal, regardless of cooldown identity.

## P6 — Evidence packet, provenance, and hashes

- **Restate the misleading fields** (`HANDOFF.md` defect #2): `guardrail` must state that it
  applies to *position drift from `baseline_position`*; `min_sample_size` must state what it
  is compared against — with P0.5, the target row's own impressions. Both 2026-08-11
  reviewers misread both fields identically; in an agreement-required system a misleading
  packet manufactures spurious holds.
- **Add selection evidence**: impressions, actual CTR, which of P5's three signal states
  applies, the candidate's rank among candidates, priority-tier membership, and the other
  pages competing for the same query (P7's cannibalization signal).
- **Provenance** (Codex Round 1 #6, verified): `codex_review_proposal.py:188` builds the
  packet from `_latest_snapshot(loop_dir)`, not the proposal's `created_run_id`. Daily
  rank-check and technical-Thursday runs write snapshots too, so a packet can be built from
  a snapshot carrying **different** GSC data than the one that created the proposal.
  Persist the selector's inputs and outputs on the proposal at creation — source run ID,
  score, rank, policy version — and build the packet from the creation snapshot.
- **Selector-policy hash** (Codex Round 1 #7, verified): `SPEC_HASH_FIELDS`
  (`review_protocol.py:21`) covers authorization-relevant fields. New selector fields change
  *why* a proposal was selected, not what it may do. Do not dilute the authorization hash —
  add a **separate** `selector_policy_hash` to the evidence packet and proposal record.
- **Hash echo** (`HANDOFF.md` defect #3, Codex Round 1 #8): accept a documented
  12-character prefix, verified against the full hash the tool already holds. This is a
  **code** change at `codex_review_proposal.py:199` (currently exact 64-character equality)
  plus a `SKILL.md` change — Round 0 wrongly implied documentation alone. Note also that
  `event_log.py:31`'s `ALLOWED_EXTRA_FIELDS` allowlist **drops unknown fields silently**, so
  every new evidence field must be added there or it vanishes from the event log.

## P7 — Portfolio diagnostics and an auditable filter

Not free (Codex Round 1 #13, accepted): each needs new logic in `_evaluate_attention`, new
`run.json` fields, report rendering, and tests.

- **Cannibalization**: a query where ≥2 pages each take a material share of impressions.
  Already present — `what do patients say about accelerate health` returns `/chiropractor/`,
  `/resources/` and `/auto-injury/`; `accelerate health denver reviews…` returns four pages.
  Both are the known unrelated-business noise, so the detector's first real finding is a
  *false* positive — design for that.
- **Page-two queue**, **impressions without clicks**, and **priority-page coverage** (which
  priority pages receive no impressions at all).
- **Human review queue** (`SEO_PROGRAM.md` Part 1's closing rule; P2's
  `HUMAN_REVIEW_QUEUE.csv`): every candidate suppressed by any filter, the rule that
  suppressed it, and an empty decision field. A naive append-per-run produces noise and a
  second durable history beside `events.jsonl`, so: an idempotent key
  (`page + keyword + rule`), upsert-with-occurrence-count rather than append, an explicit
  "best-effort observability, not authorization" status, and writes under the existing run
  lock. **The human decision field is not machine-owned** (Codex Round 3 #13): the upsert
  updates machine observations only and must never overwrite a non-empty human decision.
  Separate the two field groups explicitly rather than relying on merge discipline.

## P8 — Page audit and acceptance criteria

- **Honest scope:** a plain HTTP GET audits the **server response**. It cannot assess
  rendered DOM completeness after client-side execution, mobile layout, or browser link
  behaviour. `artwebsite` is server-rendered Django, so the server response is close to the
  delivered page — but the connector must claim only what it checks, and a real
  rendered-DOM audit means budgeting a headless browser.
- **Define the crawl source properly** (Codex Round 3 #14). The 14 pages in the GSC export
  are *not* a canonical set — they are whatever one query/page export returned, they exclude
  every page with no impressions, and they include protocol variants
  (`http://…/massage/` and `https://…/massage/` are two of the 14). Crawl **`sitemap.xml`
  plus canonical links discovered from it**, and report GSC-only coverage as a separate
  figure. Auditing on the order of tens of pages costs nothing either way; getting the set
  right is what makes the uniqueness check sound.
- **New `page_audit` snapshot section**, not a squeeze into `technical_health`'s fixed
  pagespeed/indexation shape. Registry entry `critical: False`, degrades like `pagespeed`.
  Requires deliberate additions to `connector_registry.py`, `_dispatch_connector`,
  `spec_validate.py`, the snapshot schema, and report rendering.
- **Structural checks first; query-alignment checks later** (Codex Round 3 #8). Ship
  status, canonical, robots/noindex, exactly one `H1`, heading-order validity, title/meta
  presence and length, **title uniqueness across the crawled set**, image `alt` presence,
  and schema parse/`@type` — all of which need no notion of what a page is *for*. The
  Part 6 criterion "primary query present in title / `H1` / first 100 words" **cannot be
  checked until each page has an approved primary query**, and the GSC query list is not a
  page-contract authority (it contains competitor-brand noise, as `keyword_exclusions`
  already documents). So either a minimal approved page→primary-query map moves up from
  P9.2, or query alignment waits for P9.2. **"Naturally"**, medical accuracy, and capsule
  self-containment stay Codex/human criteria — Part 6 says so itself.
- Reconcile `draft_copy.py`'s 155-character meta bound to Part 6's 150. A claim either way;
  the stricter bound costs nothing.

## P9 — Page contracts, service pages, then content (Program Phases 1–4)

**Money pages before information pages.**

- **P9.1 — Service-page contracts and audit for the six real `art` service pages.** Program
  Phase 3 and P5: intent match, one page one keyword, an `H1` that answers "can you do this
  for me?", CTA above the fold on mobile, real work photos, reviews, Service /
  LocalBusiness schema, FAQ capped at 5, owner-supplied evidence for every claim. This is
  where a clinic's revenue comes from, and P0.1 will tell us whether any of these pages
  ranks at all.
- **P9.2 — Opportunity backlog and page contracts** (Phases 1–2) under
  `projects/<slug>/research/`, each with an `owner_review_status` column and P7's review
  queue as its companion. Produced by a Claude-Code skill — **never** inside `run_loop.py`,
  whose LLM-free determinism is load-bearing.
  **Its page→primary-query map ships earlier, at sequencing step 7** — P5's proposal phase
  and P8's query-alignment checks both depend on it, so it cannot wait for the rest of
  P9.2. One approved primary query per indexable page, human-signed-off. The rest of the
  backlog and contract work stays here.
- **P9.3 — Voice references** (`voice.md`, `humour.md`, `opinions.md`, `stats.md`,
  `stories.md`). Human-supplied; the program calls this the only input a competitor cannot
  replicate and a model cannot invent. Hard prerequisite for P9.4.
- **P9.4 — Content capsules for information pages** (Phase 4). Only after everything above.
  If built, the program's guardrails ship as hard mechanics, not prompt prose:
  `[INSERT EXPERIENCE]` / `[NEEDS HUMAN INPUT]` markers that block publication while
  unresolved, a claims-and-sources table, and Guardrails 6 and 7 as explicit Codex review
  criteria enforced against P3's `PROJECT_RULES.md`.

---

## Guardrails to codify (Part 4)

| Program guardrail | Binds where |
|---|---|
| 1. Publishing velocity | Only if P9.4 ships; and as a max-live-changes-per-week cap if Path B is ever built. The current max-3-proposals-per-run is a *proposal* cap, not a publication cap. |
| 2. Service-page sprawl / location-page uniqueness | P9.1/P9.2. Conflict 2: a service×location page is justified only where genuinely unique local substance exists. |
| 3 / Conflict 3. Paid links | P2 — free citations universally, paid only as an audited last resort. |
| 4. Review gating | Human-only, never automated. Named in P1's checklist. |
| 6. Fabricated citations | P9.4, enforced at Codex review against P3. |
| 7. Invented experience | P9.4, enforced at Codex review against P3. |
| 9. Service areas you don't serve | Narrower than Round 2 claimed: what was verified is that the **three configured physical locations match the site footer**. That proves nothing about future GBP service-area claims, neighbourhood pages, or authority artifacts. Service-area approval stays a human rule. UNC Campus stays footer-only. |
| 12. Never touch a `#1` page | **Partially** covered, and Round 0 overstated it. `3 < position <= 20` is a **selector heuristic** in `_pick_new_actions`; it excludes positions 1–3, not just #1, and `apply.py` does not enforce it against a proposal created by any other route. State it accurately or enforce it at review/apply time. |

## Explicitly not adopted

- **Part 1's ads-vs-SEO gate and Part 9's e-commerce/AOV material.** They belong to
  `D:\Dev\ART Marketing Agency` (paid) and to a business that sells products. Worth
  carrying over: the market-viability screen (difficulty < 30, CPC > $10) as an
  `/intake-project` question for *future* clients.
- **Anything on Part 11's human-only list.**
- **The program's numeric claims as thresholds.** Especially Conflict 4's click-distribution
  percentages — this site's own position-1 CTR is 0.51%, nowhere near any of them.
- **Any change to the auto-implementable action set, the three gates, or Tier 2.**

## Sequencing

0. **Step 0** — decide the operating state. Everything below assumes `approval_mode:
   propose-only` is in force, and it stays in force until the step-10 go/no-go.
1. **P0-a (offline)** — P0.1, P0.5 design + migration, P0.6 documentation sync. Code and
   tests, no new live API surface and no new credential — but **scheduled outputs do change**
   (`local_rank` row values, evaluation outcomes), because the scheduled tasks run against
   this working tree. Do it under the freeze and name the changed outputs in the commit.
2. **P3** — business brief and `PROJECT_RULES`, the risk-register prerequisite, then the
   hash check. An hour of Nate's time plus a small, well-defined code change; it addresses a
   live risk and it is what makes restoring auto-implementation defensible.
3. **P0-b (one controlled live run)** — P0.2 + P0.7, by hand, after cost approval. **This is
   the decision point for the whole plan**: it either supports the local-pack hypothesis or
   refutes it, and P1's priority depends on the answer.
4. **P1's checklist half** immediately (blocked on nothing); **P1's measurement half** once
   GBP OAuth access is obtained — its own prerequisite, its own timeline, three separate
   capabilities.
5. **P4's definition** — before P5, because it decides what "priority" means. Connector work
   may be deferred.
6. **P0-c** — pagination, reporting-only, compared against the old row set. Carry the old
   and new row sets in **separate named fields** so it is demonstrable — not merely
   asserted — that `_pick_new_actions` is still consuming the old one during the
   reporting-only phase.
7. **P8 (structural half) + P6 + P7's cannibalization detector + P9.2's page→primary-query
   map**, then **P5 as diagnostic-only**. The map is **split out of P9.2 and pulled forward
   into this step** (Codex Round 7): both P5's proposal phase and P8's query-alignment
   checks require it, so leaving it inside step 9 made the sequence literally unexecutable —
   step 8 could never start. Only the map moves; the broader backlog and page-contract work
   stays in step 9. The map is small: an approved primary query per indexable page,
   human-signed-off, and it is exactly what nothing in this system currently has.
8. **P5's proposal-producing half** — only once P8, P7's cannibalization signal, and the
   page→primary-query map all exist. **P7 cannot come after this** (Codex Round 6): P6
   names "other pages competing for the same query" as required selection evidence and
   sources it from P7, so ordering P7 later would let proposals resume without the very
   evidence the plan says they require. This site already has multi-page competition on
   `auto injury chiropractor` (homepage, `/auto-injury/`, `/chiropractor/`), so it is a
   live case, not a hypothetical. Belt and braces: where the cannibalization signal is
   missing for a candidate, that forces a **finding**, never a proposal.
   P7's review-queue half may land later; only the detector is on the critical path.
9. **P2, P9** — authority operations and the page/content program. Re-decide scope after one
   real cycle has run through the above. Do not pre-commit.

**Restoring automation is step 10, not step 3** (Codex Round 4 #1 — Round 3 got this wrong
and it was the most dangerous error in it). P3 makes the *content* of an automated rewrite
defensible; it does nothing about whether the loop picks a sensible target (P5), whether the
result can be measured (P0.5), or whether the page evidence exists (P7/P8). Automation stays
off until **all** of P0.5, P0.3, P8's structural half, P7's cannibalization detector, P9.2's
page→primary-query map, and the P6 evidence/hash changes are complete — and then only on an
explicit human go/no-go, recorded in `RISK-REGISTER.md` as its own dated row. The six-day
runway is a reason not to rush, not a reason to switch it back on early.

**Step 10 restores the full intended configuration, not one switch.** Leaving
`approval_mode: propose-only` in place fails closed, so a partial restore is safe but
inert. The deliberate end state is `approval_mode: tier1-enabled` +
`auto_implementation_enabled: true` + `manual_approval_only: false` on
`title-tag-rewrite`/`meta-description-rewrite` only, with `internal-link-addition` staying
`manual_approval_only: true`. Restore all three together, or state which is deliberately
being left off.

**Path B (direct push) is untouched and remains gated on the R6 amendment** — and this plan
is a stronger argument for not building it yet. An autonomous push pipeline whose measured
objective is three non-brand clicks a month, and whose "verified winner" verdict means only
"position didn't get worse", would automate the confident shipping of changes nobody can
evaluate. `HANDOFF.md` already said "fix candidate selection before the push"; the data says
the problem is one level deeper.

## Risks / open questions

- **The local-pack explanation is a hypothesis.** P0.2 and P0.7 test it against the exact
  five queries. If it fails, the zero-click pattern needs another explanation before P1's
  promotion is justified on *this site's* evidence — though P1 stands on Appendix A's
  reasoning regardless.
- **AI citation/mention measurement is unresolved.** P0.2 delivers SERP composition, not AI
  visibility. Part 6.5's Bing AI Performance report may or may not be reachable by API; that
  is unverified and must not be assumed.
- **P0.5 changes what "verified" means** and needs baseline-clicks provenance before it can
  be implemented at all. Document in `RISK-REGISTER.md`, don't just ship it.
- **P5 changes what the loop proposes**, so before/after comparisons across the change are
  not like-for-like. Run both selectors side-by-side once and have a human read both lists.
- **P1's GBP access may be slow** — OAuth consent plus possible enablement approval. The
  checklist half is the deliverable that isn't blocked on it.
- **P9 is a different kind of system.** Content drafting has fabrication failure modes that
  string rewriting does not. The existing review stage is a good fit but should not be
  assumed to transfer without redesign.
- **`SEO_PROGRAM.md` is one operator's compilation of presenter claims.** A good source of
  *methods*; not a source of *facts*. Nothing here should cite one of its numbers as
  evidence — including the Appendix A figures that motivate P1.

## Out of scope

- Any Path B / push / deploy / rollback work.
- Any LLM call inside `run_loop.py`'s run contract.
- Any new auto-implementable action type.
- MMC changes beyond what an existing briefing section already reads.
- The ads and e-commerce material.
