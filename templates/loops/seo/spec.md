---
version: 1
loop: seo
objective: <one line — e.g. "Improve GSC position/clicks for the target keyword set without regressing currently-ranking pages">
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
site_url: <exact GSC property, e.g. "sc-domain:example.com" or "https://example.com/">
metrics_window_days: 28
# Required once dataforseo is in inputs - the (keyword, page) pairs to rank-check:
# targets:
#   - keyword: <target keyword>
#     page: </path/on/site>
# location_code: 2840     # optional, DataForSEO location (2840 = United States)
# language_code: en       # optional
# device: desktop         # optional: desktop | mobile | tablet
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 14
    min_sample_size: 100
  - type: meta-description-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 14
    min_sample_size: 100
  - type: internal-link-addition
    tier: 1
    rollback: revert PR
    observation_window_days: 21
    min_sample_size: 150
approval_mode: propose-only
max_run_duration_minutes: 30
schedule: "0 6 * * 1"
stop_condition: <e.g. "human sets loops_enabled.seo to false in project.md">
memory: memory.md
credential_aliases:
  gsc: <alias, e.g. acme-gsc-readonly>
  # dataforseo: <alias, e.g. acme-dataforseo-read>   # required once dataforseo is in inputs
---

# SEO loop spec — <project>

Fill in the placeholders above, then validate with:

```
./.venv/Scripts/python.exe tools/spec_validate.py projects/<slug>/loops/seo/spec.md
```

A spec that fails validation refuses to run — fix the reported errors and re-validate before the loop's first run.

## Notes

- `guardrail_metrics` and `failure_threshold` currently track ranking-page position regressions. Add more guardrails (e.g. traffic share, indexation count) as the loop matures.
- A new project starts `inputs: [gsc]` only. GSC is the primary metrics source (clicks/impressions/sample size); enabling `dataforseo` adds an independent `serp_position` per (keyword, page) from `targets` — turn it on only after the first GSC-only reports look right.
- Every `allowed_actions` entry must declare a non-empty `rollback`, or be marked `manual_approval_only: true` if it has no clean reversal path.
- `approval_mode: propose-only` is the safe default for a new project. Switch to `tier1-enabled` only after human review of the first two reports (see AgentColabPlan.md Phase 2).
