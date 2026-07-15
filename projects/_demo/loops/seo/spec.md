---
version: 1
loop: seo
objective: Prove memory -> verify -> learn end-to-end against mock GSC-shaped data, with no real credentials or network calls.
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
  - mock
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 0.0001
    min_sample_size: 100
  - type: meta-description-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 0.0001
    min_sample_size: 100
approval_mode: propose-only
max_run_duration_minutes: 5
schedule: "manual (dry-run only, not scheduled)"
stop_condition: human deletes projects/_demo
memory: memory.md
credential_aliases:
  mock: demo-gsc-readonly
---

# SEO loop spec — _demo (synthetic dry-run fixture)

`observation_window_days: 0.0001` (~9 seconds) is deliberate: it lets the two-consecutive-run dry run exercise the "evaluate prior experiments" step within a single test session, without waiting 14 real days. **This value is only valid for `_demo` — every real project template defaults to realistic windows (14–21 days).**
