---
version: 1
loop: seo
objective: Improve GSC position/clicks on lead-intent pages (services, locations) for acceleratedrehabtherapy.com without regressing currently-ranking pages, while monitoring local rank, backlinks, and technical/indexation health
primary_metric: gsc_position
guardrail_metrics:
  - name: ranking_pages_position
    comparator: ">"
    threshold: 5
    consecutive_runs: 1
failure_threshold:
  metric: ranking_pages_position
  comparator: ">"
  value: 5
inputs:
  - gsc
  - pagespeed        # keyless-capable, no new credential needed - safe to run live today
  - gsc-indexation   # reuses the existing, already-stored art-gsc-readonly credential - safe to run live today
  - dataforseo-local-rank   # enabled 2026-07-23: art-dataforseo-readonly stored and verified. Runs PLAN.md step 0's documented fallback (city/state SERP), not Business Data API/Google My Business - that product-tier confirmation is still open, see Notes below
  - dataforseo-backlinks    # enabled 2026-07-23: same credential as dataforseo-local-rank, no separate gate
domain: "acceleratedrehabtherapy.com"
site_url: "sc-domain:acceleratedrehabtherapy.com"
metrics_window_days: 28
targets:
  - keyword: physical therapy greeley
    page: /physical-therapy/
  - keyword: physical therapy denver
    page: /physical-therapy/
  - keyword: chiropractor greeley
    page: /chiropractor/
  - keyword: chiropractor denver
    page: /chiropractor/
  - keyword: auto injury treatment greeley
    page: /auto-injury/
  - keyword: auto injury treatment denver
    page: /auto-injury/
  - keyword: work comp injury care greeley
    page: /work-comp/
  - keyword: work comp injury care denver
    page: /work-comp/
  - keyword: massage therapy greeley
    page: /massage/
  - keyword: massage therapy denver
    page: /massage/
  - keyword: acupuncture greeley
    page: /acupuncture/
  - keyword: acupuncture denver
    page: /acupuncture/
language_code: en
device: desktop
# Organic-intent pages only. The 5 shockwave/chronic-tendon/non-surgical pages were
# removed 2026-07-24: verified they are deliberate Meta ad landing pages (noindex,nofollow
# + FB pixel + lead forms + no nav), so pagespeed/gsc-indexation monitoring them here just
# produced permanent "unknown to Google" false alarms. They are paid-ad assets (see
# D:\Dev\ART Marketing Agency), not organic-SEO pages. Do not re-add without checking noindex.
priority_pages:
  - https://acceleratedrehabtherapy.com/physical-therapy/
  - https://acceleratedrehabtherapy.com/auto-injury/
  - https://acceleratedrehabtherapy.com/work-comp/
  - https://acceleratedrehabtherapy.com/chiropractor/
  - https://acceleratedrehabtherapy.com/massage/
  - https://acceleratedrehabtherapy.com/acupuncture/
locations:
  - name: Greeley
    address: "1823 65th Ave Suite 3 Greeley, CO 80634"
    zip: "80634"
  - name: Denver
    address: "2480 W 26th Ave #90B Denver, CO 80211"
    zip: "80211"
  - name: UNC Campus
    address: "1901 10th Ave, Cassidy Hall Greeley, CO 80639"
    zip: "80639"
keyword_exclusions:
  # Case-insensitive substring match (see run_loop.py _pick_new_actions). These drop
  # "accelerated X" near-brand-noise queries that are actually other businesses or generic
  # terms (0 clicks, wrong intent) - the loop kept proposing homepage rewrites to chase them.
  # DO NOT add "accelerated rehab" or "accelerated rehab therapy" here - those are THIS
  # clinic's real brand terms (rank pos 1-2, high value). Each entry below was verified not
  # to collide with the real brand. Added 2026-07-24 after clearing the pending queue.
  - "accelerate health"          # "Accelerate Health Denver" - unrelated business
  - "accelerated health"         # same unrelated business, alt spelling
  - "accelerated healing"        # "accelerated healing center" - different business
  - "accelerated recovery"       # generic/ambiguous (addiction recovery, etc.), 0 clicks
  - "accelerated performance"    # "accelerated performance rehabilitation" - different business
attention_thresholds:
  - kind: numeric_delta
    metric: organic_rank_position
    comparator: ">"
    threshold: 5
    consecutive_runs: 2
  - kind: numeric_delta
    metric: referring_domains
    comparator: "<"
    threshold: -3
    consecutive_runs: 2
  - kind: enum_transition
    metric: cwv_status
    to: poor
additional_schedules:
  - name: technical-thursday
    cron: "0 6 * * 4"
    mode: technical-only
    inputs:
      - pagespeed
      - gsc-indexation
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: 'local branch/commit only — Loop Agency never pushes this repo, and any live change or rollback is pushed by Nate by hand'
    manual_approval_only: true
    observation_window_days: 14
    min_sample_size: 100
  - type: meta-description-rewrite
    tier: 1
    rollback: 'local branch/commit only — Loop Agency never pushes this repo, and any live change or rollback is pushed by Nate by hand'
    manual_approval_only: true
    observation_window_days: 14
    min_sample_size: 100
  - type: internal-link-addition
    tier: 1
    rollback: 'local branch/commit only — Loop Agency never pushes this repo, and any live change or rollback is pushed by Nate by hand'
    manual_approval_only: true
    observation_window_days: 21
    min_sample_size: 150
approval_mode: propose-only
max_run_duration_minutes: 30
schedule: "0 6 * * 1"
stop_condition: "human sets loops_enabled.seo to false in project.md"
memory: memory.md
credential_aliases:
  gsc: art-gsc-readonly
  dataforseo: art-dataforseo-readonly   # stored 2026-07-23, verified via credentials.py --check
  pagespeed: art-pagespeed-key   # stored 2026-07-23 - free keyless PSI quota was confirmed exhausted (429, quota_limit_value 0) before this was added; still optional per connector_registry.py's credential_required=False, but required in practice for this project
---

# SEO loop spec — art (acceleratedrehabtherapy.com)

Validated with:

```
./.venv/Scripts/python.exe tools/spec_validate.py projects/art/loops/seo/spec.md
```

## Notes

- Starts `inputs: [gsc]` only. GSC is the primary metrics source (clicks/impressions/sample size). No seed keyword/page list constrains what the loop proposes - it discovers ranking pages from live GSC data on its first run and proposes changes on the ones with the most traffic. `project.md`'s "Priority reference pages" section lists the lead-intent pages/queries Nate should judge the first report's proposals against; it's a review aid only, not a spec-level filter. `dataforseo` may be enabled later for independent `serp_position` verification, once the first GSC-only reports look right.
- Repo `D:\Dev\artwebsite` auto-deploys on push with no staging gate - every push is Tier 2, human-only (see `project.md` and `RISK-REGISTER.md` R6). This loop's tooling may create a local branch/worktree and commit there for approved Tier 1 proposals, but it never pushes, merges, fetches, or updates remote-tracking branches in that repo.
- `approval_mode: propose-only` - the safe default; not switched to `tier1-enabled` until Nate has reviewed the first two reports. Even then, each action's `manual_approval_only` flag is the real apply-time gate. For `art` today, all three actions remain `manual_approval_only: true`, so any real apply/rollback is still a manual, human action even though the codebase now supports local commit/branch creation for opted-in actions.
- `project.md`'s Goals and "Priority reference pages" sections also note Answer Engine Optimization (AEO) as a review consideration for these lead-intent pages. No new guardrail, metric, or connector exists for AEO in this loop - so this is documentation/reviewer guidance only, not something `run_loop.py` measures, filters, or acts on differently. Update 2026-07-23: DataForSEO's published API catalog now lists an `AI Optimization API` (LLM Mentions, AI Keyword Search Volume), so the "no data source exists" premise behind this note is stale - see `PLAN.md`'s Out of Scope section. AEO connector work stays a separate, unstarted effort, not part of this expansion.
- `keyword_exclusions: ["accelerate health"]` - the first real run (2026-07-18) surfaced GSC query rows for "Accelerate Health," an unrelated Denver business, alongside this domain's own data (GSC's domain-property report has no relevance filter; it includes every query with even one impression, however tangential). All 3 draft proposals from that run were built on this branded noise and were rejected. This filter drops any keyword candidate containing the term (case-insensitive substring) before proposal picking - see `tools/run_loop.py`'s `_pick_new_actions`.
- **2026-07-23 expansion** (`PLAN.md`, grilled + 6 rounds of Codex review): added machine-readable `priority_pages`, `locations`, `attention_thresholds`, and `additional_schedules` to this frontmatter. `project.md` remains human context only and is still not parsed.
- All five connectors (`gsc`, `pagespeed`, `gsc-indexation`, `dataforseo-local-rank`, `dataforseo-backlinks`) are live-enabled and individually confirmed working end-to-end against real accounts as of 2026-07-23 (`gsc`: 397 rows; `pagespeed`: 11 rows via a real API key, after the free keyless tier was confirmed exhausted; `gsc-indexation`: 1 sitemap + 11 inspection rows; `dataforseo-local-rank`: resolved Greeley to DataForSEO location code 1014529 and returned a real (null) rank result; `dataforseo-backlinks`: 86 referring domains, 130 backlinks). `pagespeed` initially had no credential wired at all - `connector_registry.py`'s entry hardcoded `credential_alias: None`, so a configured key would have been silently ignored; fixed by giving it a real alias key with `credential_required: False` (optional, but used when present). The initial `art-dataforseo-readonly` credential also 401'd on both DataForSEO connectors; re-storing it (64 chars -> 43 chars) resolved it, confirming a typo in the first attempt, not an account/plan issue.
- **Open item, not blocking:** `dataforseo-local-rank` runs `PLAN.md` step 0's documented fallback (city/state SERP via the standard endpoint), not Business Data API / Google My Business - whether Nate's DataForSEO plan includes that product for true zip/radius precision is still unconfirmed. The fallback is now proven live and working; upgrading to hyperlocal precision (if wanted) is a separate follow-up, not a blocker.
- Monday runs are the full monitoring pass. `technical-thursday` is observe-only: Core Web Vitals + indexation only, with no GSC Search Analytics pull and no proposal generation.
- `priority_pages` mirrors the human review list from `project.md` so CWV and URL Inspection have an explicit machine-readable source of truth.
- `locations` (Greeley, Denver, and the UNC Campus location) were verified against the live site's footer at build time, not invented - confirmed independently by fetching acceleratedrehabtherapy.com directly. The monthly footer-address check going forward writes `projects/art/loops/seo/locations-detected.json` and flags differences in `report.md`; it never edits this spec automatically - any future change still requires Nate's review, same as this initial set should get before the DataForSEO-dependent connectors above are enabled.
