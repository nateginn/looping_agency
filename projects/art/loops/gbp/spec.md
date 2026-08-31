---
version: 1
loop: gbp
objective: >
  Turn deficiencies found in this client's own SEO/local-visibility research into draft
  Google Business Profile Posts, as a content-freshness/engagement lever - never as a claimed
  fix for local-pack rank, distance, category, or review-count problems (see the framing note
  below). Every draft-stage proposal is reviewed and published by a human; nothing in this
  loop ever publishes anything on its own (see "What this loop does NOT do yet" below).
primary_metric: gbp_compliance_pass_rate
# gbp_compliance_pass_rate := (drafted payloads that pass draft_gbp_post.py's automated
# validator) / (all drafted payloads submitted to that validator) - the denominator is every
# payload the validator was ever run against, not just the ones that subsequently entered
# approval review (gating the denominator on "entered review" would make this 100% by
# construction and measure nothing - see the plan's Phase 2 section). NOT computed by any code
# yet - draft_gbp_post.py (Phase 4) and its own observability counters are unbuilt. Recorded
# here now so the metric name and definition are fixed before any evaluator code exists to
# compute it, per the plan's explicit warning against a metric that's decided after the fact.
guardrail_metrics:
  - name: remote_post_rejections
    comparator: ">"
    threshold: 0
    consecutive_runs: 1
# The count of unique remote post IDs Google itself reports in a rejected state within a
# rolling window, each counted once and only when the rejection was observed after this
# proposal's own create attempt (never a re-observation of an already-known rejection, never
# one predating this proposal). NOT computed by any code yet - requires the remote-reconciliation
# machinery in publish_gbp_post.py (Phase 6, unbuilt) and its own evaluator (Phase 7, unbuilt;
# explicitly NOT a reuse of run_loop.py's _evaluate_prior_experiments, which has no concept of
# a published post - see the plan's Phase 5 section). Recorded here now for the same
# fix-the-definition-before-the-code reason as primary_metric above.
failure_threshold:
  metric: remote_post_rejections
  comparator: ">"
  value: 0
inputs:
  - dataforseo-local-rank
  - gsc-indexation
# Deliberately NOT included yet: dataforseo-maps-rank, dataforseo-local-pack (both shipped in
# tools/ as of Phase 1 - see connector_registry.py - but require a verified place_id/cid per
# location in `locations` below, which only a human can supply after confirming DataForSEO's
# identifier and the Business Profile API's accountId/locationId genuinely refer to the same
# physical location - see the plan's Phase 1 "one-time, safety-sensitive, human-verified step"
# and Phase 0 below). Deliberately NOT included: gsc, dataforseo, mock (any connector that
# would populate `search_analytics`) - this loop's proposal selector (Phase 3, shipped
# 2026-08-31 as run_loop.py's `_pick_gbp_actions`) is its own code, never run_loop.py's
# generic _pick_new_actions. As of 2026-08-31 this is a
# real structural guarantee, not just a fact about this spec's current `inputs`:
# run_loop.py gates both _evaluate_prior_experiments and _pick_new_actions on
# spec["loop"] == "seo", so even a stale search_analytics section carried forward from some
# earlier run that mistakenly did include one of these connectors could not make the
# SEO-shaped selector fire for this loop (a Codex review of an earlier version of this
# spec's claim found the un-gated version false in general - `_build_snapshot` carries an
# un-refreshed section forward verbatim). Until Phase 3 ships, this loop generates no
# proposals at all - a run collects local-rank and indexation observability only.
domain: "acceleratedrehabtherapy.com"
targets:
  # Same keyword/page/location targets as the seo loop's dataforseo-local-rank config
  # (projects/art/loops/seo/spec.md) - local_rank absence is one of this loop's deficiency
  # signals (plan Phase 1's "Deficiency / opportunity signals" list, item 1). Kept as a
  # separate, independently-maintained list rather than a shared file, since the two loops'
  # specs are independently editable per the plan's "sibling loop" design (different cadence,
  # different terminal action).
  - keyword: physical therapy greeley
    page: /physical-therapy/
    location: Greeley
  - keyword: physical therapy denver
    page: /physical-therapy/
    location: Denver
  - keyword: chiropractor greeley
    page: /chiropractor/
    location: Greeley
  - keyword: chiropractor denver
    page: /chiropractor/
    location: Denver
  - keyword: auto injury treatment greeley
    page: /auto-injury/
    location: Greeley
  - keyword: auto injury treatment denver
    page: /auto-injury/
    location: Denver
  - keyword: work comp injury care greeley
    page: /work-comp/
    location: Greeley
  - keyword: work comp injury care denver
    page: /work-comp/
    location: Denver
  - keyword: massage therapy greeley
    page: /massage/
    location: Greeley
  - keyword: massage therapy denver
    page: /massage/
    location: Denver
  - keyword: acupuncture greeley
    page: /acupuncture/
    location: Greeley
  - keyword: acupuncture denver
    page: /acupuncture/
    location: Denver
priority_pages:
  # Same lead-intent pages as the seo loop, for gsc-indexation - a deindexed priority page is
  # one of this loop's deficiency signals (plan Phase 1, item 3).
  - https://acceleratedrehabtherapy.com/physical-therapy/
  - https://acceleratedrehabtherapy.com/auto-injury/
  - https://acceleratedrehabtherapy.com/work-comp/
  - https://acceleratedrehabtherapy.com/chiropractor/
  - https://acceleratedrehabtherapy.com/massage/
  - https://acceleratedrehabtherapy.com/acupuncture/
site_url: "sc-domain:acceleratedrehabtherapy.com"
locations:
  # Same two real profiles as gbp-profiles.md - Greeley and Denver. UNC Campus is
  # deliberately absent: it is footer-only and must never be modelled as a GBP profile (see
  # gbp-profiles.md and the plan's Phase 6 "structurally absent from this mapping" rule).
  # place_id/cid are deliberately NOT set yet - see the "Deliberately NOT included" note under
  # inputs above and Phase 0 below.
  - name: Greeley
    address: "1823 65th Ave Suite 3 Greeley, CO 80634"
    zip: "80634"
  - name: Denver
    address: "2480 W 26th Ave #90B Denver, CO 80211"
    zip: "80211"
language_code: en
device: desktop
allowed_actions:
  - type: gbp-post-draft
    tier: 1
    manual_approval_only: true
    observation_window_days: 30
    min_sample_size: 1
    # observation_window_days/min_sample_size are schema-satisfying placeholders, not yet
    # meaningful numbers - run_loop.py's generic _evaluate_prior_experiments (which is what
    # normally gives these two fields their meaning for an SEO proposal) is explicitly NOT
    # used for this action type (plan Phase 5: "a GBP-specific evaluator is new code, written
    # in Phase 7"). Revisit both values once that evaluator exists and defines what
    # "observed long enough" and "enough engagement data" actually mean for a Post.
approval_mode: propose-only
auto_implementation_enabled: false
# Both match standing decision 6 (CURRENT-WORK.md: "automation stays off until 'did it work?'
# is trustworthy") and, independently, the fact that gbp-post-draft has no entry in
# tools/lib/action_types.py's IMPLEMENTABLE_ACTIONS - apply.py could never auto-implement it
# regardless of these two flags. A GBP Post's terminal action is a live publish via
# tools/publish_gbp_post.py (Phase 6, unbuilt), never a local git commit via apply.py, so these
# flags are inert for this loop today - kept at their safe defaults for when that changes.
max_run_duration_minutes: 30
schedule: "0 6 * * 3"
# Wednesday, deliberately offset from the seo loop's Monday full run and Thursday
# technical-only run (see art/loops/seo/spec.md) - a GBP Post's cadence (roughly weekly,
# per Google's own recommendation) is independent of the SEO loop's metric-pull cadence, and
# the two loops must not contend for the same clock minute (see RISK-REGISTER.md R15, the
# same-minute lock-contention defect already fixed once for the seo loop's own schedules).
# Not registered with Windows Task Scheduler yet - see "What this loop does NOT do yet" below.
stop_condition: "human sets loops_enabled.gbp to false in project.md"
memory: memory.md
credential_aliases:
  gsc: art-gsc-readonly
  dataforseo: art-dataforseo-readonly
---

# GBP Posts loop spec — art (Accelerated Rehab Therapy)

Validated with:

```
./.venv/Scripts/python.exe tools/spec_validate.py projects/art/loops/gbp/spec.md
```

## Framing note — read this before anything else

A Google Business Profile Post is a **content-freshness / engagement lever, never a
mechanical fix for local-pack absence.** A missing local-pack result can be caused by
distance, category mismatch, review count/recency, or profile eligibility — none of which a
Post changes. This loop's visibility signals (local-rank absence, indexation gaps, and
`known-gaps.yaml`'s content-gap entries) inform **which service/location to post about and how
often**, never a claim that posting will move rank or pack presence. Structural profile gaps
belong in `templates/loops/seo/gbp-checklist.md` (human-run), not in a Post proposal — see
`known-gaps.yaml`'s `routing` field for the line between the two.

## What this loop does today (Phases 1-3 of the design)

- Collects `dataforseo-local-rank` (organic rank per keyword/location, reusing the already-
  verified `art-dataforseo-readonly` credential) and `gsc-indexation` (deindexation/coverage
  status on the same lead-intent pages the `seo` loop already tracks).
- Drafts up to 3 `gbp-post-draft` proposals per run via `run_loop.py`'s `_pick_gbp_actions`
  (Phase 3, shipped 2026-08-31) — but ONLY from a `known-gaps.yaml` entry explicitly routed
  `content_gap_evidence`, re-read and re-hashed fresh from disk every run. A visibility signal
  (local-rank absence, an indexation gap) is never sufficient on its own — see the framing
  note above and `known-gaps.yaml`'s own header. `run_loop.py`'s generic `_pick_new_actions`
  (SEO-shaped `{page, keyword}` proposals) is separately, structurally gated on
  `spec["loop"] == "seo"` and can never fire for this loop, including from a stale,
  carried-forward `search_analytics` section (see `tools/tests/gbp_scaffold_smoke.py`'s
  regression test). Cooldown is keyed on `{location, topic}`, not `{page}`.
- Every drafted proposal carries a closed-enum `opportunity_type` (always
  `content_freshness` today — the plan's other value, `engagement`, needs Phase 6's remote
  post-history, which is unbuilt), a `signal_reference`, an `intended_user_action`, and a
  `content_gap_evidence` object. Its `rationale` is templated exclusively from structured
  fields (entry id, location, confirmed date) — deliberately never the gap's own free-text
  `summary`, so a rank/visibility claim written into that summary later cannot reach a
  proposal (see `tools/run_loop.py`'s `_pick_gbp_actions` docstring).
- Is **not** in `project.md`'s `loops_enabled` and **not** registered with Windows Task
  Scheduler. It can be run on demand (`python tools/run_loop.py art gbp`) for testing; nothing
  currently runs it automatically. A drafted proposal goes no further than `draft` today —
  there is no drafting tool to add Post copy (Phase 4) and no way to approve it into anything
  that could ever be published (Phases 5-6 are unbuilt).

## What this loop does NOT do yet

- **No drafting tool** for the Post's actual copy (`tools/draft_gbp_post.py`, plan Phase 4) —
  validates a proposed Post against the real, current GBP API contract (character limit, CTA
  enum, post-type-specific required fields) and against both `gbp-profiles.md` and MMC's
  `registry/clients/art.yaml` (trademark/medical-claims/current-hours constraints). Unbuilt.
- **No approval-state extensions** (plan Phase 5) — the new terminal statuses a published
  post needs (`publishing`, `live`, `rejected-by-google`, `not-found`, `retracted`,
  `publish-unknown`) and `tools/lib/event_log.py`'s allowlist extension to carry them.
  Unbuilt.
- **No publish path** (`tools/publish_gbp_post.py`, plan Phase 6) — the only mechanism that
  would ever call Google's API to actually create a Post, always `--dry_run` by default and
  always operator-triggered, never scheduled or loop-invoked. Unbuilt. **Nothing in this
  workspace can publish a GBP Post today.**
- **No measurement** (`tools/measure_gbp_post.py`, plan Phase 7) — whether per-post engagement
  metrics are even obtainable from Google's current API surface is unconfirmed; this is a
  spike, not an assumption. Unbuilt.
- **No live Maps/local-pack data** — `dataforseo-maps-rank`/`dataforseo-local-pack` (Phase 1,
  shipped in `tools/`) are not in this spec's `inputs` because they require a verified
  `place_id`/`cid` per location — see Phase 0 below.

## Phase 0 — prerequisites only a human can complete

Blocks live Maps/local-pack data collection (not blocking Phases 2–5, which can proceed
against local-rank/indexation data alone) and, separately, blocks Phase 6/7 entirely:

1. **A verified place_id and/or cid for the Greeley and Denver GBP listings.** DataForSEO's
   Maps/organic-SERP responses carry these identifiers; a human must confirm which returned
   identifier is genuinely this client's own listing (not inferred, not derived from the
   domain) and add it to this spec's `locations[].place_id`/`.cid`. Only after that can
   `dataforseo-maps-rank`/`dataforseo-local-pack` be added to `inputs` above and a
   `gbp_targets` list added alongside them.
2. **For publishing (Phase 6):** a Google Cloud project with the posting-capable Business
   Profile API enabled (subject to Google's own access review, which can take days) and a
   refresh token carrying whatever OAuth scope that API actually requires — verify the exact
   scope at build time, and confirm account/location access for both Greeley and Denver
   before Phase 6 is ever trusted, per the plan's Phase 0 section.
3. **For measurement (Phase 7):** the Business Profile Performance API enabled separately,
   with its own scope and quota confirmed independently — this is what Phase 7's spike
   actually tests; do not assume it shares Phase 6's grant.
4. **The one-time, human-verified pairing** between DataForSEO's place_id/cid (item 1) and the
   Business Profile API's `accountId`/`locationId` (items 2–3) for the *same physical
   location* — these are different identifier spaces for different APIs and must never be
   assumed interchangeable or auto-derived from one another (plan Phase 1/Phase 6).

Credentials, once obtained, are stored via `tools/lib/credentials.py` exactly like every other
credential in this workspace (Windows Credential Manager, human-run `--store`) — never
hand-copied into a repo file, never requested in chat.

## Notes

- `gbp` is a **sibling loop** to `seo`, not an extension of it — different cadence (roughly
  weekly regardless of what a given SEO run finds, vs. `seo`'s metric-pull-driven cadence),
  different terminal action (publish to Google, vs. edit the website), and a cooldown that
  will be keyed on `{location, topic}` once Phase 3 exists, not `{page, keyword}` — reusing
  `seo`'s cooldown key would overload a concept that doesn't fit a GBP post (no page). Both
  loops reuse the same generic run-contract engine (`tools/run_loop.py`): lock, spec
  validation, snapshot, propose, report.
- `dataforseo-local-rank`'s `targets`/`locations` here are a deliberately separate,
  independently-maintained copy of `seo/spec.md`'s equivalent config, not a shared/imported
  file — the two loops' specs are meant to be editable independently (e.g. a location added
  to one for a reason specific to that loop should not silently appear in the other).
- See `projects/art/gbp-profiles.md` for the durable, human-maintained record of what each
  profile actually contains (categories, services, credentials/wording rules), and
  `known-gaps.yaml` for its machine-readable, versioned sibling.
