---
version: 1
loop: seo
objective: Improve GSC position/clicks on lead-intent pages (services, locations) for acceleratedrehabtherapy.com without regressing currently-ranking pages
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
  # - dataforseo   # enable only after the first GSC-only reports are reviewed
site_url: "sc-domain:acceleratedrehabtherapy.com"
metrics_window_days: 28
keyword_exclusions:
  - "accelerate health"
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: 'no automated apply/PR path exists for this project — the artwebsite repo has no staging gate and no loop tooling may push to it; any live change and its reversal are made by Nate directly, by hand'
    manual_approval_only: true
    observation_window_days: 14
    min_sample_size: 100
  - type: meta-description-rewrite
    tier: 1
    rollback: 'no automated apply/PR path exists for this project — the artwebsite repo has no staging gate and no loop tooling may push to it; any live change and its reversal are made by Nate directly, by hand'
    manual_approval_only: true
    observation_window_days: 14
    min_sample_size: 100
  - type: internal-link-addition
    tier: 1
    rollback: 'no automated apply/PR path exists for this project — the artwebsite repo has no staging gate and no loop tooling may push to it; any live change and its reversal are made by Nate directly, by hand'
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
  # dataforseo: <alias>   # required once dataforseo is in inputs
---

# SEO loop spec — art (acceleratedrehabtherapy.com)

Validated with:

```
./.venv/Scripts/python.exe tools/spec_validate.py projects/art/loops/seo/spec.md
```

## Notes

- Starts `inputs: [gsc]` only. GSC is the primary metrics source (clicks/impressions/sample size). No seed keyword/page list constrains what the loop proposes - it discovers ranking pages from live GSC data on its first run and proposes changes on the ones with the most traffic. `project.md`'s "Priority reference pages" section lists the lead-intent pages/queries Nate should judge the first report's proposals against; it's a review aid only, not a spec-level filter. `dataforseo` may be enabled later for independent `serp_position` verification, once the first GSC-only reports look right.
- Repo `D:\Dev\artwebsite` auto-deploys on push with no staging gate - every push is Tier 2, human-only (see `project.md` and `RISK-REGISTER.md` R6). This loop's tooling never reads or writes that repo.
- `approval_mode: propose-only` - the safe default; not switched to `tier1-enabled` until Nate has reviewed the first two reports. Even then, these `allowed_actions` are `manual_approval_only: true` with no automated rollback path, since `D:\Dev\artwebsite` has no staging gate - any real apply/rollback for this project is a manual, human action, not a `tools/apply.py` transition.
- `project.md`'s Goals and "Priority reference pages" sections also note Answer Engine Optimization (AEO) as a review consideration for these lead-intent pages. No new guardrail, metric, or connector exists for AEO - GSC and DataForSEO don't report AI-answer-engine citations - so this is documentation/reviewer guidance only, not something `run_loop.py` measures, filters, or acts on differently.
- `keyword_exclusions: ["accelerate health"]` - the first real run (2026-07-18) surfaced GSC query rows for "Accelerate Health," an unrelated Denver business, alongside this domain's own data (GSC's domain-property report has no relevance filter; it includes every query with even one impression, however tangential). All 3 draft proposals from that run were built on this branded noise and were rejected. This filter drops any keyword candidate containing the term (case-insensitive substring) before proposal picking - see `tools/run_loop.py`'s `_pick_new_actions`.
