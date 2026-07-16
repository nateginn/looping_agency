---
version: 1
loop: content-social
objective: <one line — e.g. "Grow impressions/engagement per post while holding brand voice and cadence">
primary_metric: engagement_per_post
guardrail_metrics:
  - name: engagement_vs_trailing_average
    comparator: "<"
    threshold: 0.5
    consecutive_runs: 3
failure_threshold:
  metric: engagement_vs_trailing_average
  comparator: "<"
  value: 0.5
inputs:
  - social-analytics
allowed_actions:
  - type: post-schedule-proposal
    tier: 1
    rollback: delete/supersede procedure (declare exact steps before enabling)
    observation_window_days: 7
    min_sample_size: 3
approval_mode: propose-only
max_run_duration_minutes: 20
schedule: "0 8 * * 1"
stop_condition: <e.g. "human sets loops_enabled.content-social to false in project.md">
memory: memory.md
credential_aliases:
  social-analytics: <alias>
---

# Content/Social loop spec — DRAFT (Phase 1: not wired)

**Status: draft only.** This spec exists so its shape informs the shared run-contract abstractions; `tools/social_analytics.py` and any posting connector are **not implemented yet**. Do not attempt to run this loop — `run_loop.py` has no connector registered for `social-analytics` and will refuse.

## What's still open before this can go live (Phase 3)

- **Brand-voice guardrail is qualitative.** Per AgentColabPlan.md, qualitative guardrails are never auto-judged — this needs a scored rubric (not just a threshold) and always requires human approval regardless of tier, even once wired.
- Rollback semantics for `post-schedule-proposal` are a placeholder — a real delete/supersede procedure per platform must be declared before this action type is enabled (an action type with no clean reversal path is forbidden or `manual_approval_only`).
- Cadence-within-plan guardrail (mentioned in AgentColabPlan.md's metrics table) is not yet modeled as a `guardrail_metrics` entry here — add it before Phase 3 wiring.
